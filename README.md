# Banking & Relational Fraud Detection: Python tier

Thin Python layer over the thick PostgreSQL database in `../bank_fraud_db/`.
Hosted on **Neon**, accessed through the **SQLAlchemy 2.0 ORM** (psycopg 3 driver).

```
React dashboard (later) --> FastAPI (app/api) --> SQLAlchemy ORM --> Neon Postgres
                                |                                   (rules, locks, triggers,
        outbox worker, scheduler, LISTEN thread, analytics           procedures, audit)
```

## 1. Setup

```bash
cd bank_fraud_app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then paste your two Neon connection strings
```

Neon gives a **pooled** string (host contains `-pooler`) and a **direct** one:

| Variable | Used by | Why |
|---|---|---|
| `DATABASE_URL` (pooled) | API, workers, analytics | PgBouncer transaction pooling; every call here is one short transaction using only transaction-local settings |
| `DIRECT_DATABASE_URL` | LISTEN/NOTIFY thread, `apply_sql.py` | `LISTEN` and long scripts need a real session; the pooler drops session state |

## 2. Install the database

```bash
python scripts/apply_sql.py             # files 01-05: schema, triggers, procedures, views/roles, seed
python scripts/apply_sql.py --tests     # also runs 06_tests.sql and prints every result and NOTICE
```

## 3. Run the system

```bash
uvicorn app.api.main:app --reload              # API  (docs at /docs)
python -m app.workers.outbox_worker            # notification delivery (run 1..N copies)
python -m app.workers.scheduler                # housekeeping + gateway probes (run exactly 1)
python -m app.simulate.load_test --scenario all   # concurrency / idempotency / deadlock / traffic
python -m app.analytics.fraud_analysis --days 1   # deeper fraud analysis of what the traffic produced
```

Try the API (`/docs` has the same):

```bash
# log in (demo password for seeded users: Passw0rd!)
curl -s localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email":"aarav.sharma@example.com","password":"Passw0rd!","device_fingerprint":"fp-aarav-iphone-9f3a2c"}'

# transfer (needs the token, the transaction PIN 4821 and an Idempotency-Key)
curl -s localhost:8000/transfers -H "authorization: Bearer $TOKEN" -H 'idempotency-key: demo-0001-abcdef' \
  -H 'content-type: application/json' \
  -d '{"sender_account_id":"aaaaaaaa-aaaa-aaaa-aaaa-000000000101","receiver_account_id":"aaaaaaaa-aaaa-aaaa-aaaa-000000000102","amount":"15000.00","rail":"UPI","pin":"4821"}'
# repeat the exact same command: HTTP 200 with IDEMPOTENT_DUPLICATE: SUCCESS, money moved once

# analyst endpoints use the X-API-Key header
curl -s localhost:8000/dashboard/alerts -H 'x-api-key: change-me-too'
curl -s localhost:8000/health/ready
```

## 4. Where each requirement is met

| Requirement | Database | Python tier |
|---|---|---|
| ACID, concurrency | `sp_execute_secure_transfer`, ordered `FOR UPDATE`, CHECKs | one procedure call = one transaction (`services/transfers.py`) |
| Idempotency | unique key claim, payload fingerprint | same key reused on every retry; `Idempotency-Key` header |
| Timeouts | `lock_timeout` 3 s, `statement_timeout` 15 s inside the procedure | `SET LOCAL statement_timeout` per transaction (`db.py`), connect/pool timeouts |
| Backoff, jitter, retry limits | `fn_backoff_with_jitter`, outbox (5) and DLQ (3) caps | `retry.py` (same equal-jitter formula), capped attempts |
| Failure handling | `RETRYABLE:` / `DEFERRED:` / `FAILED:` statuses, DLQ | retries only transient errors; 503 + `Retry-After` for outages |
| TTL, invalidation, stale data | `expires_at` on blocks, config version + `NOTIFY` | `RulesCache`: TTL + version check + push invalidation + stale fallback |
| Failed messages, recovery | outbox leases, `fn_housekeeping`, `fn_replay_dead_letters` | `outbox_worker`, `scheduler` |
| Consistency | audit chain, `fn_reconcile_ledger` | `/admin/reconcile`, run after every load-test scenario |
| Dependency health | circuit breaker in `payment_gateways` | synthetic probes feed `sp_record_gateway_result` |
| Readiness / liveness | `fn_readiness`, `fn_liveness` | `/health/ready` (503 when not ready), `/health/live` (never touches the DB) |
| Graceful degradation | `fn_system_mode`, gateway rails `DEFERRED` | DB outage becomes 503 + `Retry-After`; rules served stale from cache |

## 5. Known limits (be upfront in the viva)

* Transfers settle inside the ledger. There is no real external gateway call or reversal flow; the gateway probes are simulated (`GATEWAY_PROBE_FAILURE_RATE`).
* New-device logins are recorded and notified, and the database quarantines high-value transfers from untrusted devices, but there is no OTP verification step.
* The transaction PIN is verified in the API without an attempt counter. Add a failed-PIN counter table or reuse `login_history` for production.
* `X-Forwarded-For` is trusted for the client IP; put the API behind a proxy that sets it.
* The listener needs the direct Neon connection; if it is down the cache still refreshes by TTL and version check.
