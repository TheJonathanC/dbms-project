"""Money transfers. The stored procedure sp_execute_secure_transfer does the real work;
this module checks the PIN, calls it, and retries when the database says "try again"."""
import logging
import time
import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from . import queries as q
from .db import session_scope
from .retry import backoff_delay

log = logging.getLogger("bank.transfers")
MAX_ATTEMPTS = 3


def check_pin(sender_account_id: uuid.UUID, pin: str) -> tuple[uuid.UUID | None, bool]:
    """Returns (owner user id, pin correct). The owner is None when the account does not exist."""
    with session_scope() as s:
        row = s.execute(text(q.PIN_CHECK), {"acc": sender_account_id, "pin": pin}).first()
    if row is None:
        return None, False
    return row.user_id, bool(row.pin_ok)


def record_failed_login(user_id: uuid.UUID, ip: str | None, reason: str) -> None:
    """Failed attempts feed the database's own defences: the failed-login fraud rule, and the
    trigger that blocklists an IP after 5 failures in 10 minutes."""
    with session_scope() as s:
        s.execute(text(q.RECORD_FAILED_LOGIN), {"uid": user_id, "ip": ip or "0.0.0.0", "reason": reason})


def account_exists(account_id: uuid.UUID) -> bool:
    with session_scope() as s:
        return s.execute(text(q.ACCOUNT_EXISTS), {"acc": account_id}).first() is not None


def execute_transfer(key: str, sender: uuid.UUID, receiver: uuid.UUID, amount: Decimal,
                     rail: str, ip: str | None, device: str | None) -> dict:
    """Call the stored procedure. A transient problem (lock timeout, deadlock, dropped connection)
    is retried with backoff and jitter. Retrying is safe because the idempotency key makes the
    database return the first result instead of moving the money twice."""
    with session_scope() as s:
        type_id = s.execute(text(q.RAIL_ID), {"rail": rail}).scalar_one()

    params = {"key": key, "sender": sender, "receiver": receiver, "amount": amount,
              "type_id": type_id, "ip": ip, "device": device}
    status, tx_id, attempt = "FAILED: not attempted", None, 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with session_scope() as s:
                row = s.execute(text(q.CALL_TRANSFER), params).one()
            status, tx_id = row[0], row[1]
        except (OperationalError, InterfaceError) as exc:
            log.warning("transfer attempt %s hit a database connection error: %s", attempt, exc)
            status, tx_id = "RETRYABLE: database connection problem", None

        if not status.startswith("RETRYABLE") or attempt == MAX_ATTEMPTS:
            break
        time.sleep(backoff_delay(attempt))

    outcome, _, reason = status.partition(":")
    return {
        "outcome": outcome.strip(),          # SUCCESS, DUPLICATE, REJECTED, QUARANTINED, DEFERRED, RETRYABLE, FAILED
        "reason": reason.strip() or None,
        "transaction_id": tx_id,
        "attempts": attempt,
    }
