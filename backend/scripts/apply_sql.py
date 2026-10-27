"""Install (or reset) the database: runs database/simple.sql against Neon.

    python scripts/apply_sql.py                  # uses DIRECT_DATABASE_URL from .env
    python scripts/apply_sql.py path/to/file.sql

WARNING: simple.sql drops and recreates every project table, so all data is replaced by the seed data.

The file is cut into single statements and run one by one in autocommit mode, exactly like psql does.
That matters: NOW() is fixed inside a transaction, so one big transaction would give every seed row
and every fraud-rule time window the same instant.
"""
import re
import sys
from pathlib import Path

DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def split_sql(sql: str) -> list[str]:
    """Split a SQL script on semicolons that are not inside quotes, comments or $$ bodies."""
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    dollar: str | None = None

    def flush() -> None:
        text = "".join(buf).strip()
        buf.clear()
        without_comments = re.sub(r"--[^\n]*|/\*.*?\*/", "", text, flags=re.S).strip()
        if without_comments:
            statements.append(text)

    while i < n:
        if dollar:                                   # inside a $$ ... $$ body
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
            else:
                buf.append(sql[i])
                i += 1
            continue

        pair = sql[i:i + 2]
        if pair == "--":                             # line comment
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j])
            i = j
        elif pair == "/*":                           # block comment
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j])
            i = j
        elif sql[i] == "'":                          # string literal ('' is an escaped quote)
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1])
            i = j + 1
        elif sql[i] == "$" and (m := DOLLAR_TAG.match(sql, i)):
            dollar = m.group(0)
            buf.append(dollar)
            i = m.end()
        elif sql[i] == ";":
            flush()
            i += 1
        else:
            buf.append(sql[i])
            i += 1
    flush()
    return statements


def main() -> int:
    import psycopg

    from app.config import settings

    url = settings.direct_database_url or settings.database_url
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "database" / "simple.sql"
    statements = split_sql(path.read_text(encoding="utf-8"))
    print(f"{path.name}: {len(statements)} statements -> {url.split('@')[-1].split('/')[0]}")

    with psycopg.connect(url, autocommit=True) as conn:
        conn.add_notice_handler(lambda d: print("  notice:", d.message_primary))
        for number, statement in enumerate(statements, 1):
            try:
                conn.execute(statement)
            except Exception as exc:
                head = " ".join(statement.split())[:120]
                print(f"\nFailed at statement {number}: {head}\n{exc}", file=sys.stderr)
                return 1
            if number % 50 == 0:
                print(f"  {number}/{len(statements)}")
    print("Done. Check it with:  python scripts/smoke_test.py http://localhost:8000 <API_KEY>")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
