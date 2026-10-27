"""Apply the SQL files to Neon (uses the DIRECT connection string).

    python scripts/apply_sql.py                       # files 01-05 (install + seed)
    python scripts/apply_sql.py --tests               # also run 06_tests.sql and print every result
    python scripts/apply_sql.py --only 06_tests.sql   # run one file

Why not just feed each file to the driver in one call? A multi-statement string runs
as ONE implicit transaction, but the test scenarios rely on each statement being its
own transaction (and a failing statement must not undo the rest). So the script splits
files into statements (respecting $$ bodies, quotes and comments), drops psql
meta-commands (\\set, \\pset) and runs each statement in autocommit mode.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402

DEFAULT_SQL_DIR = Path(__file__).resolve().parents[2] / "bank_fraud_db"
INSTALL_FILES = ["01_schema.sql", "02_triggers.sql", "03_procedures.sql", "04_views_security.sql", "05_seed.sql"]


def split_sql(text: str) -> list[str]:
    """Split a SQL script on top-level semicolons.

    Understands: -- line comments, /* block comments */, 'single quoted' strings
    (with '' escapes), "quoted identifiers" and $tag$ ... $tag$ dollar quoting.
    Lines starting with a backslash (psql meta-commands) are skipped.
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("\\")]
    src = "\n".join(lines)
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "--":
            j = src.find("\n", i)
            i = n if j == -1 else j          # drop the comment, keep the newline
            continue
        if two == "/*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if src[j] == "'" and src[j + 1:j + 2] == "'":
                    j += 2
                elif src[j] == "'":
                    break
                else:
                    j += 1
            buf.append(src[i:j + 1]); i = j + 1
            continue
        if c == '"':
            j = src.find('"', i + 1)
            j = n - 1 if j == -1 else j
            buf.append(src[i:j + 1]); i = j + 1
            continue
        if c == "$":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j < n and src[j] == "$":
                tag = src[i:j + 1]
                end = src.find(tag, j + 1)
                end = n if end == -1 else end + len(tag)
                buf.append(src[i:end]); i = end
                continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def run_file(conn: psycopg.Connection, path: Path, *, show_results: bool, stop_on_error: bool) -> int:
    notices: list[str] = []
    conn.add_notice_handler(lambda d: notices.append(f"{d.severity}: {d.message_primary}"))
    stmts = split_sql(path.read_text())
    errors = 0
    print(f"\n=== {path.name}: {len(stmts)} statements ===")
    for idx, stmt in enumerate(stmts, 1):
        try:
            cur = conn.execute(stmt)
            while True:
                if show_results and cur.description:
                    cols = [d.name for d in cur.description]
                    rows = cur.fetchall()
                    print(f"-- result ({', '.join(cols)})")
                    for r in rows[:25]:
                        print("   ", r)
                if not cur.nextset():
                    break
        except Exception as exc:  # noqa: BLE001
            errors += 1
            first_line = stmt.splitlines()[0][:90]
            print(f"!! statement {idx} failed: {exc}\n   {first_line}")
            if stop_on_error:
                raise SystemExit(1)
        for note in notices:
            print("   NOTICE", note)
        notices.clear()
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql-dir", default=os.environ.get("SQL_DIR", str(DEFAULT_SQL_DIR)))
    ap.add_argument("--tests", action="store_true", help="also run 06_tests.sql afterwards")
    ap.add_argument("--only", help="run a single file from the sql dir")
    args = ap.parse_args()

    sql_dir = Path(args.sql_dir)
    files = [args.only] if args.only else INSTALL_FILES + (["06_tests.sql"] if args.tests else [])
    with psycopg.connect(get_settings().direct_url, autocommit=True, connect_timeout=15) as conn:
        for name in files:
            is_test = name.startswith("06")
            run_file(conn, sql_dir / name, show_results=is_test, stop_on_error=not is_test)
    print("\nDone.")


if __name__ == "__main__":
    main()
