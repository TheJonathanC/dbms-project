"""Concurrency / reliability scenarios against the live database.

    python -m app.simulate.load_test --scenario all
    python -m app.simulate.load_test --scenario double_spend --threads 12

Scenarios
  double_spend  N threads each try to spend the same balance        -> exactly one succeeds
  idempotency   N threads submit the SAME idempotency key           -> money moves once
  opposite      A->B and B->A at the same time                      -> no deadlock, books balance
  traffic       realistic noise + planted fraud (structuring, mule pass-through, blocklist hit,
                over-ceiling) to feed app.analytics.fraud_analysis

Every scenario ends with fn_reconcile_ledger(): an independent audit that must return zero rows.
Keep --threads <= pool_size + max_overflow (default 10) so threads are not just queueing for connections.
"""
from __future__ import annotations

import argparse
import random
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from sqlalchemy import func, select

from app.db import session_scope
from app.models import BlocklistedAccount, Transaction
from app.services.health import reconcile
from app.services.transfers import execute_transfer, resolve_type_id
from app.simulate.factory import SimCustomer, balance_of, create_customers

IMPS = "IMPS"


def _transfer(sender: SimCustomer, receiver: SimCustomer, amount: str, key: str | None = None, rail: str = IMPS):
    return execute_transfer(idempotency_key=key or f"sim-{uuid.uuid4().hex}",
                            sender_account_id=sender.account_id, receiver_account_id=receiver.account_id,
                            amount=Decimal(amount), type_id=resolve_type_id(rail),
                            client_ip="198.18.0.10", device_hash=sender.device_hash)


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} {detail}")
    return ok


def _books_balance() -> bool:
    problems = reconcile()
    return _check("ledger reconciliation returns zero rows", not problems, f"{problems[:3]}" if problems else "")


def double_spend(threads: int) -> bool:
    print(f"\n== double_spend: {threads} threads race to spend one 50,000 balance (45,000 each) ==")
    a, b = create_customers(1, Decimal("50000.00"))[0], create_customers(1, Decimal("100000.00"))[0]
    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(lambda _: _transfer(a, b, "45000.00"), range(threads)))
    counts = Counter(r.status for r in results)
    print("  outcomes:", dict(counts))
    ok = _check("exactly one transfer succeeded", counts["SUCCESS"] == 1)
    ok &= _check("sender balance is 5,000 (never negative)", balance_of(a.account_id) == Decimal("5000.00"))
    ok &= _check("receiver gained exactly 45,000", balance_of(b.account_id) == Decimal("145000.00"))
    return _books_balance() and ok


def idempotency(threads: int) -> bool:
    print(f"\n== idempotency: {threads} threads submit the same key ==")
    a, b = create_customers(1)[0], create_customers(1)[0]
    key = f"sim-idem-{uuid.uuid4().hex}"
    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(lambda _: _transfer(a, b, "1500.00", key=key), range(threads)))
    counts = Counter(r.status for r in results)
    print("  outcomes:", dict(counts))
    with session_scope() as s:
        rows = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.idempotency_key == key))
    ok = _check("exactly one ledger row for the key", rows == 1, f"(rows={rows})")
    ok &= _check("exactly one real SUCCESS", counts["SUCCESS"] == 1)
    ok &= _check("sender debited once", balance_of(a.account_id) == Decimal("98500.00"))
    return _books_balance() and ok


def opposite(threads: int) -> bool:
    n = 8     # stays below the 10-per-hour rapid-fire rule so every attempt is a legitimate transfer
    print(f"\n== opposite: {n} transfers A->B and {n} B->A at the same time (deadlock test) ==")
    a, b = create_customers(1)[0], create_customers(1)[0]
    jobs = [(a, b)] * n + [(b, a)] * n
    random.shuffle(jobs)
    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(lambda j: _transfer(j[0], j[1], "100.00"), jobs))
    counts = Counter(r.status for r in results)
    print("  outcomes:", dict(counts))
    ok = _check("all transfers succeeded (no deadlock surfaced)", counts["SUCCESS"] == 2 * n)
    ok &= _check("balances unchanged (money only went back and forth)",
                 balance_of(a.account_id) == Decimal("100000.00") and balance_of(b.account_id) == Decimal("100000.00"))
    return _books_balance() and ok


def traffic(threads: int) -> bool:
    print("\n== traffic: normal activity + planted fraud patterns ==")
    people = create_customers(25, Decimal("200000.00"))
    structurer, mule, mule_out = (create_customers(1, Decimal("500000.00"))[0],
                                  create_customers(1, Decimal("100000.00"))[0],
                                  create_customers(1, Decimal("1000.00"))[0])
    feeders = create_customers(5, Decimal("100000.00"))
    blocked_acc = create_customers(1)[0]
    with session_scope() as s:
        s.add(BlocklistedAccount(account_id=blocked_acc.account_id, reason="simulation: known mule"))

    jobs = []
    for _ in range(120):                                              # background noise
        s_, r_ = random.sample(people, 2)
        jobs.append((s_, r_, f"{random.choice([250, 500, 1200, 3400, 4999])}.00"))
    for _ in range(4):                                                # structuring just under the ceiling
        jobs.append((structurer, random.choice(people), "85000.00"))
    for f in feeders:                                                 # fan-in to a mule ...
        jobs.append((f, mule, "30000.00"))
    jobs.append((mule, mule_out, "148000.00"))                        # ... and immediate pass-through
    jobs.append((people[0], blocked_acc, "20000.00"))                 # attempt to pay a blocklisted account
    jobs.append((people[1], people[2], "150000.00"))                  # over the single-transfer ceiling
    random.shuffle(jobs)
    # keep ordering of the mule leg after its feeders so the pass-through pattern actually forms
    jobs.sort(key=lambda j: (j[0] is mule))

    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(lambda j: _transfer(*j), jobs))
    counts = Counter(r.status.split(":")[0] + (": " + r.status.split(": ")[1] if ": " in r.status else "") for r in results)
    print("  outcomes:", dict(counts))
    ok = _check("engine rejected the blocklist attempt", any(r.status == "REJECTED: RECEIVER_BLOCKLISTED" for r in results))
    ok &= _check("engine rejected the over-ceiling transfer",
                 any(r.status == "REJECTED: EXCEEDS_MAX_SINGLE_LIMIT" for r in results))
    ok &= _check("engine quarantined at least one planted pattern",
                 any(r.status.startswith("QUARANTINED") for r in results))
    print("  now run:  python -m app.analytics.fraud_analysis --days 1")
    return _books_balance() and ok


SCENARIOS = {"double_spend": double_spend, "idempotency": idempotency, "opposite": opposite, "traffic": traffic}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    ap.add_argument("--threads", type=int, default=10)
    args = ap.parse_args()

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    verdicts = {name: SCENARIOS[name](args.threads) for name in names}
    print("\n== summary ==")
    for name, ok in verdicts.items():
        print(f"  {name:<14} {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if all(verdicts.values()) else 1)


if __name__ == "__main__":
    main()
