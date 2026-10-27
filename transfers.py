"""Thin wrapper around sp_execute_secure_transfer (thick database, thin backend).

All business rules (balance checks, fraud rules, locking, idempotency) live in
the database. This module only:
  * calls the procedure inside ONE transaction,
  * retries transient failures with exponential backoff + jitter, always with
    the SAME idempotency key,
  * maps the procedure's status string to an HTTP-friendly outcome.

Why retrying is safe: if the connection dies after the database committed but
before we read the answer, the retry hits the UNIQUE idempotency_key and the
procedure returns IDEMPOTENT_DUPLICATE: <original outcome>. Money moves once.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text

from app.config import get_settings
from app.db import session_scope
from app.models import TransactionType
from app.retry import backoff_delay, is_transient

log = logging.getLogger(__name__)
settings = get_settings()

_CALL_TRANSFER = text(
    """
    CALL sp_execute_secure_transfer(
        CAST(:key AS varchar), CAST(:sender AS uuid), CAST(:receiver AS uuid),
        CAST(:amount AS numeric), CAST(:type_id AS integer),
        CAST(:ip AS varchar), CAST(:device AS varchar),
        CAST(NULL AS text), CAST(NULL AS uuid))
    """
)

_CALL_REVIEW = text(
    """
    CALL sp_review_quarantined_transaction(
        CAST(:tx AS uuid), CAST(:approve AS boolean), CAST(:analyst AS uuid),
        CAST(:notes AS text), CAST(NULL AS text))
    """
)


@dataclass
class TransferResult:
    status: str                      # raw status string from the database
    transaction_id: Optional[uuid.UUID]
    attempts: int
    http_status: int
    retry_after_seconds: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS" or self.status.startswith("IDEMPOTENT_DUPLICATE: SUCCESS")


def http_status_for(status: str) -> tuple[int, Optional[int]]:
    """Map the procedure's status text to (HTTP status, Retry-After seconds)."""
    if status == "SUCCESS":
        return 201, None
    if status.startswith("IDEMPOTENT_DUPLICATE"):
        return 200, None
    if status.startswith("QUARANTINED"):
        return 202, None                      # accepted, held for review, no money moved
    if status == "IN_PROGRESS" or status == "REJECTED: IDEMPOTENCY_KEY_REUSED":
        return 409, 1 if status == "IN_PROGRESS" else None
    if status.startswith("REJECTED"):
        return 422, None
    if status.startswith("DEFERRED") or status.startswith("RETRYABLE"):
        return 503, 5                         # graceful degradation: try again shortly
    return 500, None


_type_cache: dict[str, int] = {}


def resolve_type_id(type_name: str) -> int:
    """UPI / IMPS / NEFT / RTGS -> transaction_types.type_id (cached; the table is tiny and static)."""
    name = type_name.upper()
    if not _type_cache:
        with session_scope() as s:
            for row in s.execute(select(TransactionType.type_name, TransactionType.type_id)):
                _type_cache[row.type_name] = row.type_id
    if name not in _type_cache:
        raise ValueError(f"Unknown transaction type {type_name!r}")
    return _type_cache[name]


def execute_transfer(
    *,
    idempotency_key: str,
    sender_account_id: uuid.UUID | str,
    receiver_account_id: uuid.UUID | str,
    amount: Decimal,
    type_id: int,
    client_ip: Optional[str] = None,
    device_hash: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> TransferResult:
    """Run one transfer with retry. Never raises for business outcomes; raises only if the
    database stays unreachable for every attempt."""
    attempts_allowed = max_attempts or settings.retry_max_attempts
    params = dict(key=idempotency_key, sender=str(sender_account_id), receiver=str(receiver_account_id),
                  amount=Decimal(amount).quantize(Decimal("0.01")), type_id=type_id,
                  ip=client_ip, device=device_hash)
    last_status = "FAILED: NO_ATTEMPT"

    for attempt in range(attempts_allowed):
        final_attempt = attempt == attempts_allowed - 1
        try:
            with session_scope() as s:
                row = s.execute(_CALL_TRANSFER, params).one()
            status, tx_id = row[0], row[1]
        except Exception as exc:  # noqa: BLE001
            if final_attempt or not is_transient(exc):
                raise
            delay = backoff_delay(attempt, settings.retry_base_ms, settings.retry_cap_ms)
            log.warning("transfer %s: transient DB error %s; retrying in %.2fs", idempotency_key,
                        exc.__class__.__name__, delay)
            time.sleep(delay)
            continue

        last_status = status
        if status.startswith("RETRYABLE") and not final_attempt:
            delay = backoff_delay(attempt, settings.retry_base_ms, settings.retry_cap_ms)
            log.info("transfer %s: %s; retry %d in %.2fs", idempotency_key, status, attempt + 1, delay)
            time.sleep(delay)
            continue
        if status == "IN_PROGRESS" and not final_attempt:
            # a concurrent request with the same key is still running: wait and re-read its outcome
            time.sleep(backoff_delay(attempt, 100, 1000))
            continue

        code, retry_after = http_status_for(status)
        return TransferResult(status, tx_id, attempt + 1, code, retry_after)

    code, retry_after = http_status_for(last_status)
    return TransferResult(last_status, None, attempts_allowed, code, retry_after)


def review_quarantined(tx_id: uuid.UUID | str, approve: bool, analyst_id: uuid.UUID | str,
                       notes: Optional[str] = None) -> str:
    """Analyst decision on a quarantined transfer (released / rejected)."""
    with session_scope() as s:
        row = s.execute(_CALL_REVIEW, dict(tx=str(tx_id), approve=approve,
                                           analyst=str(analyst_id), notes=notes)).one()
    return row[0]
