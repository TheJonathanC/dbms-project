"""Background work, run inside the API process every minute (and on demand from the Operations page):

  * sp_housekeeping    expire blocks whose time is up, release notifications held by a dead worker,
                       fail transfers stuck in PENDING
  * deliver_notifications   send the customer messages that the database triggers queued (outbox pattern)

Delivery here is simulated (it only logs). Swap the one marked line for a real SMS / email call.
A failed delivery is reported back to sp_mark_notification, which retries with exponential backoff
and jitter and, after the last retry, moves the message to the dead letter queue.
"""
import asyncio
import logging
import os
import random

from sqlalchemy import text

from . import queries as q
from .config import settings
from .db import session_scope

log = logging.getLogger("bank.jobs")
WORKER_ID = f"api-{os.getpid()}"


def run_housekeeping() -> None:
    with session_scope() as s:
        s.execute(text(q.CALL_HOUSEKEEPING))


def deliver_notifications(batch: int = 20) -> dict:
    with session_scope() as s:
        claimed = s.execute(text(q.CLAIM_NOTIFICATIONS), {"worker": WORKER_ID, "n": batch}).all()

    sent = failed = 0
    for n in claimed:
        error = None
        if random.random() < settings.notify_failure_rate:
            error = "Simulated provider timeout"
        else:
            log.info("[%s] %s", n.channel, n.message)     # <- replace with a real SMS / email call

        with session_scope() as s:
            s.execute(text(q.CALL_MARK_NOTIFICATION), {"id": n.notification_id, "sent": error is None, "error": error})
        sent += error is None
        failed += error is not None
    return {"claimed": len(claimed), "sent": sent, "failed": failed}


async def background_loop() -> None:
    while True:
        await asyncio.sleep(settings.background_interval_seconds)
        try:
            await asyncio.to_thread(run_housekeeping)
            await asyncio.to_thread(deliver_notifications)
        except Exception:                     # the loop must survive a database hiccup
            log.exception("background job failed; will try again next round")
