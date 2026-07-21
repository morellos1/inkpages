#!/usr/bin/env python3
"""Apply migrations/*.sql in filename order, tracked in schema_migrations.

Usage: uv run scripts/migrate.py
Reads DATABASE_URL from the environment, falling back to .env in the repo root.
"""
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"


def database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    sys.exit("DATABASE_URL not set (export it or put it in .env)")


def main() -> None:
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """create table if not exists schema_migrations (
                       filename   text primary key,
                       applied_at timestamptz not null default now()
                   )"""
            )
            conn.commit()
            cur.execute("select filename from schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        pending = [p for p in sorted(MIGRATIONS.glob("*.sql")) if p.name not in applied]
        for path in pending:
            with conn.cursor() as cur:
                cur.execute(path.read_text())
                cur.execute(
                    "insert into schema_migrations (filename) values (%s)", (path.name,)
                )
            conn.commit()
            print(f"applied {path.name}")
        if not pending:
            print("nothing to apply")
        print(f"up to date ({len(applied) + len(pending)} migrations)")


if __name__ == "__main__":
    main()
