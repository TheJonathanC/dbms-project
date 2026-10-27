"""Check a running API from the outside.

    python scripts/smoke_test.py http://localhost:8000 <API_KEY>
    python scripts/smoke_test.py https://bank-fraud-api.onrender.com <API_KEY> --transfer

Read-only unless --transfer is given (that sends one small payment twice with the same
idempotency key and checks that the money moved once). Uses only the standard library.
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def call(base, key, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"X-API-Key": key, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    base, key = args[0].rstrip("/"), args[1]
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    t = time.time()
    code, body = call(base, key, "GET", "/health")
    check("liveness", code == 200, f"({time.time() - t:.1f}s, a sleeping Render service takes ~30s to wake)")
    code, body = call(base, key, "GET", "/health/ready")
    check("database reachable", code == 200, str(body))
    code, _ = call(base, "wrong-key", "GET", "/api/overview")
    check("wrong API key is rejected", code == 401)

    for path, shape in [("/api/overview", dict), ("/api/transactions", list), ("/api/accounts", list),
                        ("/api/fraud/alerts", list), ("/api/fraud/queue", list), ("/api/fraud/rules", list),
                        ("/api/blocklist", dict), ("/api/analysis", dict), ("/api/ops", dict),
                        ("/api/transfers/options", dict)]:
        code, body = call(base, key, "GET", path)
        check(path, code == 200 and isinstance(body, shape), f"HTTP {code}")

    code, ops = call(base, key, "GET", "/api/ops")
    check("ledger matches the audit trail", code == 200 and ops["ledger_mismatches"] == [])

    if "--transfer" in sys.argv:
        _, opts = call(base, key, "GET", "/api/transfers/options")
        acc = {(a["full_name"], a["type_name"]): a["account_id"] for a in opts["accounts"]}
        sender, receiver = acc[("Aarav Sharma", "Savings")], acc[("Priya Nair", "Savings")]
        body = {"sender_account_id": sender, "receiver_account_id": receiver, "amount": "10.00", "rail": "UPI", "pin": "4821"}
        k = {"Idempotency-Key": str(uuid.uuid4())}
        c1, first = call(base, key, "POST", "/api/transfers", body, k)
        c2, second = call(base, key, "POST", "/api/transfers", body, k)
        check("transfer goes through", c1 == 200 and first["outcome"] in ("SUCCESS", "REJECTED", "QUARANTINED"), str(first))
        check("same key again returns the first result", c2 == 200 and second["outcome"] == "DUPLICATE"
              and second["transaction_id"] == first["transaction_id"], str(second))
        c3, wrong = call(base, key, "POST", "/api/transfers", {**body, "pin": "0000"}, {"Idempotency-Key": str(uuid.uuid4())})
        check("wrong PIN is refused", c3 == 403, str(wrong))

    print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
