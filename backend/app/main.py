"""FastAPI service for the Bank Fraud System.

Run locally:   uvicorn app.main:app --reload        (interactive docs at /docs)

Everything under /api needs the X-API-Key header. The dashboard (Next.js on Vercel) adds it on the
server side, so it is never visible in the browser. /health is open so Render can probe it.
"""
import asyncio
import logging
import random
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from . import analysis, jobs, queries as q, transfers
from .config import settings
from .db import rows, session_scope
from .models import Account, BlocklistedAccount, BlocklistedIp, FraudRule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bank.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(jobs.background_loop()) if settings.background_jobs else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Bank Fraud System API", version="2.0", lifespan=lifespan)

if settings.allowed_origins.strip():
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
                       allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def database_unavailable(_: Request, exc: Exception):
    """Graceful degradation: a database outage becomes a clear 503 with Retry-After, not a stack trace."""
    log.warning("database unavailable: %s", exc)
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": "The database is temporarily unavailable. Try again in a few seconds."})


# ------------------------------------------------------------------ security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_key(key: Optional[str] = Depends(api_key_header)) -> None:
    if not key or not secrets.compare_digest(key.encode(), settings.api_key.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])


# ------------------------------------------------------------------ health (no key needed)
@app.get("/health", tags=["health"])
def liveness():
    """Is the process up? Never touches the database, so a sleeping database cannot fail the probe."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness():
    """Can the API serve requests? Checks the database and reports the system mode."""
    try:
        with session_scope() as s:
            health = rows(s, q.HEALTH)[0]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database not reachable: {exc.__class__.__name__}") from None
    return {"status": "ready", "system_mode": health["system_mode"]}


# ------------------------------------------------------------------ overview
@api.get("/overview", tags=["dashboard"])
def overview():
    with session_scope() as s:
        return {
            "kpis": rows(s, q.KPIS)[0],
            "health": rows(s, q.HEALTH)[0],
            "hourly": list(reversed(rows(s, q.HOURLY))),
            "alerts": rows(s, q.ALERTS, n=6),
            "risky": rows(s, q.RISKY, n=5),
        }


# ------------------------------------------------------------------ transactions and accounts
@api.get("/transactions", tags=["dashboard"])
def list_transactions(status: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    with session_scope() as s:
        if status:
            return rows(s, q.TX_BY_STATUS, status=status.upper(), n=limit)
        return rows(s, q.TX_ALL, n=limit)


@api.get("/accounts", tags=["dashboard"])
def list_accounts():
    with session_scope() as s:
        accounts = rows(s, q.ACCOUNTS)
        risk = {r["account_id"]: r for r in rows(s, q.ACCOUNT_RISK)}
    for a in accounts:
        r = risk.get(a["account_id"], {})
        a["risk_score"] = r.get("risk_score", 0)
        a["blocked_or_held"] = r.get("blocked_or_held", 0)
        a["failed_logins_24h"] = r.get("failed_logins_24h", 0)
    return accounts


class AccountStatusIn(BaseModel):
    status: Literal["ACTIVE", "FROZEN"]


@api.post("/accounts/{account_id}/status", tags=["dashboard"])
def set_account_status(account_id: uuid.UUID, body: AccountStatusIn):
    """Freeze or unfreeze an account. The database trigger notifies the account owner."""
    with session_scope() as s:
        account = s.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        account.account_status = body.status
    return {"account_id": account_id, "status": body.status}


# ------------------------------------------------------------------ fraud
@api.get("/fraud/alerts", tags=["fraud"])
def fraud_alerts(limit: int = Query(100, ge=1, le=500)):
    with session_scope() as s:
        return rows(s, q.ALERTS, n=limit)


@api.get("/fraud/queue", tags=["fraud"])
def review_queue():
    with session_scope() as s:
        return rows(s, q.QUARANTINE)


class ReviewIn(BaseModel):
    approve: bool
    notes: Optional[str] = Field(default=None, max_length=255)


@api.post("/fraud/review/{transaction_id}", tags=["fraud"])
def review_transfer(transaction_id: uuid.UUID, body: ReviewIn):
    """Release (approve) or reject a transfer that the fraud rules put on hold."""
    with session_scope() as s:
        status = s.execute(text(q.CALL_REVIEW), {
            "tx": transaction_id, "approve": body.approve,
            "analyst": settings.analyst_user_id, "notes": body.notes}).scalar_one()
    if status.startswith("ERROR"):
        raise HTTPException(status_code=409, detail="That transfer is not waiting for review any more.")
    return {"status": status}


@api.get("/fraud/rules", tags=["fraud"])
def list_rules():
    with session_scope() as s:
        stats = {r["rule_id"]: r for r in rows(s, q.RULE_STATS)}
        rules = s.scalars(select(FraudRule).order_by(FraudRule.rule_id)).all()
        return [{
            "rule_id": r.rule_id, "rule_name": r.rule_name, "description": r.description,
            "evaluation_metric": r.evaluation_metric, "threshold_value": r.threshold_value,
            "lookback_minutes": r.lookback_minutes, "action": r.action, "is_active": r.is_active,
            "total_hits": stats.get(r.rule_id, {}).get("total_hits", 0),
            "false_positives": stats.get(r.rule_id, {}).get("false_positives", 0),
            "confirmed": stats.get(r.rule_id, {}).get("confirmed", 0),
        } for r in rules]


class RulePatch(BaseModel):
    threshold_value: Optional[Decimal] = Field(default=None, ge=0)
    action: Optional[Literal["FLAG", "QUARANTINE", "BLOCK"]] = None
    is_active: Optional[bool] = None


@api.patch("/fraud/rules/{rule_id}", tags=["fraud"])
def update_rule(rule_id: int, patch: RulePatch):
    """Retune a fraud rule. The next transfer is checked against the new values."""
    with session_scope() as s:
        rule = s.get(FraudRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        for field, value in patch.model_dump(exclude_none=True).items():
            setattr(rule, field, value)
    return {"updated": rule_id}


@api.get("/fraud/logins", tags=["fraud"])
def login_security():
    with session_scope() as s:
        return rows(s, q.LOGIN_SECURITY)


@api.get("/blocklist", tags=["fraud"])
def blocklist():
    with session_scope() as s:
        return {"ips": rows(s, q.BLOCKED_IPS), "accounts": rows(s, q.BLOCKED_ACCOUNTS)}


class IpBlockIn(BaseModel):
    ip_address: str = Field(min_length=3, max_length=45)
    reason: str = Field(min_length=3, max_length=255)
    ttl_hours: Optional[int] = Field(default=None, ge=1, le=24 * 365)


@api.post("/blocklist/ips", status_code=201, tags=["fraud"])
def block_ip(body: IpBlockIn):
    expires = datetime.now(timezone.utc) + timedelta(hours=body.ttl_hours) if body.ttl_hours else None
    with session_scope() as s:
        existing = s.scalar(select(BlocklistedIp).where(BlocklistedIp.ip_address == body.ip_address))
        if existing:      # blocking an IP again simply re-activates it
            existing.is_active, existing.reason, existing.expires_at = True, body.reason, expires
        else:
            s.add(BlocklistedIp(ip_address=body.ip_address, reason=body.reason, source="MANUAL", expires_at=expires))
    return {"blocked": body.ip_address}


@api.delete("/blocklist/ips/{ip_block_id}", tags=["fraud"])
def unblock_ip(ip_block_id: uuid.UUID):
    with session_scope() as s:
        row = s.get(BlocklistedIp, ip_block_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Block not found")
        row.is_active = False
    return {"unblocked": ip_block_id}


class AccountBlockIn(BaseModel):
    account_id: Optional[uuid.UUID] = None
    external_account_number: Optional[str] = Field(default=None, max_length=50)
    reason: str = Field(min_length=3, max_length=255)


@api.post("/blocklist/accounts", status_code=201, tags=["fraud"])
def block_account(body: AccountBlockIn):
    """Blocklist one of our accounts (a database trigger then freezes it) or an outside account number."""
    if body.account_id is None and not body.external_account_number:
        raise HTTPException(status_code=422, detail="Choose one of our accounts or enter an outside account number.")
    try:
        with session_scope() as s:
            s.add(BlocklistedAccount(account_id=body.account_id,
                                     external_account_number=body.external_account_number, reason=body.reason))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="That account is already on the blocklist.") from None
    return {"blocked": True}


@api.delete("/blocklist/accounts/{block_id}", tags=["fraud"])
def unblock_account(block_id: uuid.UUID):
    """Lift a block. A frozen account stays frozen until someone unfreezes it on the Accounts page."""
    with session_scope() as s:
        row = s.get(BlocklistedAccount, block_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Block not found")
        row.is_active = False
    return {"unblocked": block_id}


# ------------------------------------------------------------------ analysis
@api.get("/analysis", tags=["analysis"])
def run_analysis(days: int = Query(30, ge=1, le=365)):
    return analysis.run(days)


# ------------------------------------------------------------------ operations
@api.get("/ops", tags=["operations"])
def operations():
    with session_scope() as s:
        return {
            "health": rows(s, q.HEALTH)[0],
            "gateways": rows(s, q.GATEWAYS),
            "queues": rows(s, q.QUEUES),
            "ledger_mismatches": rows(s, q.LEDGER),
            "dead_letters": rows(s, q.DEAD_LETTERS, n=10),
            "audit": rows(s, q.AUDIT, n=25),
        }


class GatewayResultIn(BaseModel):
    success: bool


@api.post("/ops/gateways/{gateway_id}/result", tags=["operations"])
def gateway_result(gateway_id: int, body: GatewayResultIn):
    """Report a success or failure for a payment gateway. Failures in a row open its circuit breaker."""
    latency = random.randint(80, 300) if body.success else None
    with session_scope() as s:
        s.execute(text(q.CALL_GATEWAY_RESULT), {"gateway": gateway_id, "ok": body.success, "latency": latency})
    return {"gateway_id": gateway_id, "success": body.success}


@api.post("/ops/housekeeping", tags=["operations"])
def housekeeping():
    jobs.run_housekeeping()
    return {"ok": True}


@api.post("/ops/deliver-notifications", tags=["operations"])
def deliver_notifications():
    return jobs.deliver_notifications()


# ------------------------------------------------------------------ transfers (simulates a customer request)
@api.get("/transfers/options", tags=["transfers"])
def transfer_options():
    with session_scope() as s:
        return {
            "accounts": rows(s, q.TRANSFER_OPTIONS_ACCOUNTS),
            "devices": rows(s, q.TRANSFER_OPTIONS_DEVICES),
            "rails": rows(s, q.TRANSFER_OPTIONS_RAILS),
        }


class TransferIn(BaseModel):
    sender_account_id: uuid.UUID
    receiver_account_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    rail: Literal["UPI", "IMPS", "NEFT", "RTGS"] = "UPI"
    pin: str = Field(min_length=4, max_length=8)
    source_ip: Optional[str] = Field(default=None, max_length=45)    # lets the demo pretend to be another IP
    device_hash: Optional[str] = Field(default=None, max_length=255)


@api.post("/transfers", tags=["transfers"])
def create_transfer(body: TransferIn, request: Request, response: Response,
                    idempotency_key: Optional[str] = Header(default=None, max_length=64),
                    x_client_ip: Optional[str] = Header(default=None)):
    """Send money. Send the same Idempotency-Key twice and the money moves once."""
    ip = body.source_ip or x_client_ip or (request.client.host if request.client else None)

    user_id, pin_ok = transfers.check_pin(body.sender_account_id, body.pin)
    if user_id is None:
        raise HTTPException(status_code=404, detail="Sender account not found.")
    if not transfers.account_exists(body.receiver_account_id):
        raise HTTPException(status_code=404, detail="Receiver account not found.")
    if not pin_ok:
        transfers.record_failed_login(user_id, ip, "INVALID_PIN")
        raise HTTPException(status_code=403, detail="Wrong PIN. The attempt has been recorded.")

    result = transfers.execute_transfer(
        key=idempotency_key or str(uuid.uuid4()), sender=body.sender_account_id,
        receiver=body.receiver_account_id, amount=body.amount, rail=body.rail,
        ip=ip, device=body.device_hash)

    if result["outcome"] in ("DEFERRED", "RETRYABLE"):
        response.status_code = 503
        response.headers["Retry-After"] = "5"
    elif result["outcome"] == "FAILED":
        response.status_code = 500
    return result


class FailedLoginsIn(BaseModel):
    ip: str = Field(min_length=3, max_length=45)
    count: int = Field(default=5, ge=1, le=20)


@api.post("/demo/failed-logins", tags=["transfers"])
def demo_failed_logins(body: FailedLoginsIn):
    """Demo helper: record failed logins from one IP. After 5 in 10 minutes the database blocklists the IP."""
    with session_scope() as s:
        user_id = s.execute(text(q.ANY_USER_WITH_ACCOUNT)).scalar_one()
        s.execute(text(q.FAILED_LOGIN_BURST), {"uid": user_id, "ip": body.ip, "n": body.count})
    with session_scope() as s:
        blocked = s.execute(text(q.IP_IS_BLOCKED), {"ip": body.ip}).scalar_one()
    return {"ip": body.ip, "recorded": body.count, "blocked": bool(blocked)}


app.include_router(api)
