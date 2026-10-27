"""FastAPI server (thin backend). Run:  uvicorn app.api.main:app --reload

Endpoint groups
  /health/live, /health/ready          probes
  /auth/login                          credential check + brute-force defence (in the database)
  /transfers                           money movement (one stored-procedure call, idempotent)
  /dashboard/*, /rules, /alerts/*      analyst & dashboard reads/writes (X-API-Key)
  /blocklist/*                         blocklist management through the ORM (triggers do the rest)
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import session_scope
from app.models import Account, BlocklistedAccount, BlocklistedIp, FraudRule, Transaction, UserCredential
from app.retry import is_transient
from app.services import health
from app.services.listener import NotificationListener
from app.services.rules_cache import RulesCache
from app.services.transfers import execute_transfer, resolve_type_id, review_quarantined

log = logging.getLogger("bank.api")
settings = get_settings()
rules_cache = RulesCache()
listener: NotificationListener | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global listener
    listener = NotificationListener(on_config_change=lambda _d: rules_cache.invalidate())
    listener.start()
    yield
    listener.stop()


app = FastAPI(title="Banking & Relational Fraud Detection API", version="1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def database_unavailable_handler(_: Request, exc: Exception):
    """Graceful degradation: a database outage becomes a clean 503 with Retry-After, not a stack trace."""
    from fastapi.responses import JSONResponse
    if is_transient(exc):
        return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                            content={"detail": "Database temporarily unavailable, please retry"})
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


# ----------------------------------------------------------------- security
def require_analyst(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.analyst_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


def current_user_id(authorization: str = Header(default="")) -> uuid.UUID:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        claims = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"])
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else None)


# ----------------------------------------------------------------- schemas
class LoginIn(BaseModel):
    email: str
    password: str
    device_fingerprint: Optional[str] = None


class TransferIn(BaseModel):
    sender_account_id: uuid.UUID
    receiver_account_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    rail: str = Field(default="UPI", pattern="^(UPI|IMPS|NEFT|RTGS)$")
    pin: str = Field(min_length=4, max_length=8)


class ReviewIn(BaseModel):
    approve: bool
    analyst_user_id: uuid.UUID
    notes: Optional[str] = Field(default=None, max_length=255)


class RulePatch(BaseModel):
    threshold_value: Optional[Decimal] = Field(default=None, ge=0)
    action: Optional[str] = Field(default=None, pattern="^(FLAG|QUARANTINE|BLOCK)$")
    is_active: Optional[bool] = None


class IpBlockIn(BaseModel):
    ip_address: str
    reason: str = Field(max_length=255)
    ttl_hours: Optional[int] = Field(default=None, ge=1)


class AccountBlockIn(BaseModel):
    account_id: Optional[uuid.UUID] = None
    external_account_number: Optional[str] = Field(default=None, max_length=50)
    reason: str = Field(max_length=255)


# ----------------------------------------------------------------- probes
@app.get("/health/live")
def live():
    return health.liveness()


@app.get("/health/ready")
def ready(response: Response):
    ok, report = health.readiness(listener.connected if listener else None)
    if not ok:
        response.status_code = 503
    return report


# ----------------------------------------------------------------- auth
@app.post("/auth/login")
def login(body: LoginIn, request: Request):
    with session_scope() as s:
        status = s.execute(
            text("SELECT fn_authenticate_user(CAST(:e AS varchar), CAST(:p AS text), "
                 "CAST(:ip AS varchar), CAST(:d AS varchar))"),
            dict(e=body.email, p=body.password, ip=client_ip(request), d=body.device_fingerprint),
        ).scalar_one()
        if not status.startswith("SUCCESS"):
            code = 403 if status.startswith("DENIED") else 401
            raise HTTPException(status_code=code, detail=status)
        user_id = s.execute(text("SELECT user_id FROM users WHERE lower(email) = lower(:e)"), dict(e=body.email)).scalar_one()
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    token = jwt.encode({"sub": str(user_id), "exp": exp}, settings.jwt_secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer", "login_status": status}


# ----------------------------------------------------------------- transfers
@app.post("/transfers")
def create_transfer(body: TransferIn, request: Request, response: Response,
                    idempotency_key: str = Header(min_length=8, max_length=64),
                    x_device_fingerprint: Optional[str] = Header(default=None),
                    user_id: uuid.UUID = Depends(current_user_id)):
    # authorisation + transaction PIN (the money rules themselves are all in the database)
    with session_scope() as s:
        account = s.get(Account, body.sender_account_id)
        if account is None or account.user_id != user_id:
            raise HTTPException(status_code=403, detail="Sender account does not belong to you")
        pin_ok = s.execute(
            select(func.crypt(body.pin, UserCredential.pin_hash) == UserCredential.pin_hash)
            .where(UserCredential.user_id == user_id)
        ).scalar_one_or_none()
    if not pin_ok:
        raise HTTPException(status_code=403, detail="Invalid transaction PIN")

    result = execute_transfer(
        idempotency_key=idempotency_key,
        sender_account_id=body.sender_account_id,
        receiver_account_id=body.receiver_account_id,
        amount=body.amount,
        type_id=resolve_type_id(body.rail),
        client_ip=client_ip(request),
        device_hash=x_device_fingerprint,
    )
    response.status_code = result.http_status
    if result.retry_after_seconds:
        response.headers["Retry-After"] = str(result.retry_after_seconds)
    return {"status": result.status, "transaction_id": result.transaction_id, "attempts": result.attempts}


@app.get("/transfers/{transaction_id}")
def get_transfer(transaction_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id)):
    with session_scope() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        owner = s.scalar(select(Account.user_id).where(Account.account_id == tx.sender_account_id))
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Not your transaction")
        return dict(transaction_id=tx.transaction_id, status=tx.status, reason=tx.status_reason,
                    amount=tx.amount, created_at=tx.created_at, completed_at=tx.completed_at)


# ----------------------------------------------------------------- dashboard / analyst
@app.get("/dashboard/{section}", dependencies=[Depends(require_analyst)])
def dashboard(section: str, limit: int = 50):
    try:
        return health.dashboard(section, min(limit, 500))
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown dashboard section")


@app.get("/rules", dependencies=[Depends(require_analyst)])
def list_rules():
    snap = rules_cache.get()
    return {"version": snap.version, "stale": snap.stale, "source": snap.source, "rules": snap.rules}


@app.patch("/rules/{rule_id}", dependencies=[Depends(require_analyst)])
def patch_rule(rule_id: int, patch: RulePatch):
    """Retune a fraud rule live. The UPDATE fires the database trigger that bumps the config
    version and NOTIFYs every listener, so caches refresh without a deployment."""
    with session_scope() as s:
        rule = s.get(FraudRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        for field, value in patch.model_dump(exclude_none=True).items():
            setattr(rule, field, value)
    rules_cache.invalidate()
    return {"updated": rule_id}


@app.post("/alerts/review/{transaction_id}", dependencies=[Depends(require_analyst)])
def review(transaction_id: uuid.UUID, body: ReviewIn):
    status = review_quarantined(transaction_id, body.approve, body.analyst_user_id, body.notes)
    return {"status": status}


@app.post("/blocklist/ips", dependencies=[Depends(require_analyst)], status_code=201)
def block_ip(body: IpBlockIn):
    expires = datetime.now(timezone.utc) + timedelta(hours=body.ttl_hours) if body.ttl_hours else None
    with session_scope() as s:
        s.add(BlocklistedIp(ip_address=body.ip_address, reason=body.reason, source="MANUAL", expires_at=expires))
    return {"blocked": body.ip_address, "expires_at": expires}


@app.post("/blocklist/accounts", dependencies=[Depends(require_analyst)], status_code=201)
def block_account(body: AccountBlockIn):
    if body.account_id is None and not body.external_account_number:
        raise HTTPException(status_code=422, detail="account_id or external_account_number required")
    with session_scope() as s:           # an internal account is auto-frozen by trigger trg_blocklist_freezes_account
        s.add(BlocklistedAccount(account_id=body.account_id,
                                 external_account_number=body.external_account_number, reason=body.reason))
    return {"blocked": True}


@app.get("/admin/reconcile", dependencies=[Depends(require_analyst)])
def reconcile():
    problems = health.reconcile()
    return {"consistent": not problems, "problems": problems}
