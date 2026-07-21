"""Database helpers shared by pipeline workers."""
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]


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


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def platform_ids(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("select slug, id from platforms")
        return dict(cur.fetchall())


def get_or_create_account(
    conn,
    platform_id: int,
    *,
    native_id: str | None = None,
    handle: str,
    display_name: str | None = None,
    profile_url: str | None = None,
    status: str = "unknown",
    followers_count: int | None = None,
    discovered_via: str,
    discovery_details: dict | None = None,
    hydrated: bool = False,
) -> int:
    """Upsert an account. First discovery wins for discovered_via; profile
    fields refresh on re-hydration. Falls back to handle matching so a
    handle-only row later gains its native_id instead of duplicating."""
    import json

    details = json.dumps(discovery_details) if discovery_details else None
    with conn.cursor() as cur:
        if native_id is not None:
            cur.execute(
                """insert into accounts (platform_id, native_id, handle, display_name,
                                         profile_url, status, followers_count,
                                         discovered_via, discovery_details, last_hydrated)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                           case when %s then now() end)
                   on conflict (platform_id, native_id) where native_id is not null
                   do update set handle = excluded.handle,
                                 display_name = coalesce(excluded.display_name, accounts.display_name),
                                 profile_url = coalesce(excluded.profile_url, accounts.profile_url),
                                 status = excluded.status,
                                 followers_count = coalesce(excluded.followers_count, accounts.followers_count),
                                 last_hydrated = coalesce(excluded.last_hydrated, accounts.last_hydrated)
                   returning id""",
                (platform_id, native_id, handle, display_name, profile_url,
                 status, followers_count, discovered_via, details, hydrated),
            )
            return cur.fetchone()[0]

        # Handle-only reference (bio link target): reuse any existing row for
        # this handle — including one that already has a native_id.
        cur.execute(
            """select id from accounts where platform_id = %s and handle = %s
               order by native_id nulls last limit 1""",
            (platform_id, handle),
        )
        if row := cur.fetchone():
            return row[0]
        cur.execute(
            """insert into accounts (platform_id, handle, display_name, profile_url,
                                     status, discovered_via, discovery_details)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (platform_id, handle) where native_id is null
               do update set handle = excluded.handle
               returning id""",
            (platform_id, handle, display_name, profile_url, status, discovered_via, details),
        )
        return cur.fetchone()[0]


def is_suppressed(conn, account_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """select 1 from suppressions s
               where s.lifted_at is null
                 and (s.account_id = %(a)s
                      or s.artist_id in (select aa.artist_id from artist_accounts aa
                                         where aa.account_id = %(a)s and aa.removed_at is null))
               limit 1""",
            {"a": account_id},
        )
        return cur.fetchone() is not None


def insert_snapshot(conn, account_id: int, *, bio_text: str | None,
                    display_name: str | None, followers_count: int | None,
                    following_count: int | None, raw: dict, fetch_source: str) -> int:
    import json

    with conn.cursor() as cur:
        cur.execute(
            """insert into account_snapshots (account_id, bio_text, display_name,
                                              followers_count, following_count, raw, fetch_source)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (account_id, bio_text, display_name, followers_count,
             following_count, json.dumps(raw), fetch_source),
        )
        return cur.fetchone()[0]


def upsert_attestation(conn, account_id: int, signal: str, matched_text: str | None,
                       evidence_snapshot_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into attestations (account_id, signal, matched_text, evidence_snapshot_id)
               values (%s, %s, %s, %s)
               on conflict (account_id, signal, (coalesce(matched_text, '')))
               do update set last_seen = now(), active = true,
                             evidence_snapshot_id = excluded.evidence_snapshot_id""",
            (account_id, signal, matched_text, evidence_snapshot_id),
        )


def upsert_edge(conn, source_account_id: int, target_account_id: int, *,
                evidence_type: str, evidence_snapshot_id: int,
                evidence_url: str | None, matched_text: str | None) -> None:
    if source_account_id == target_account_id:
        return
    with conn.cursor() as cur:
        cur.execute(
            """insert into identity_edges (source_account_id, target_account_id, evidence_type,
                                           evidence_snapshot_id, evidence_url, matched_text, last_verified)
               values (%s, %s, %s, %s, %s, %s, now())
               on conflict (source_account_id, target_account_id, evidence_type)
               do update set evidence_snapshot_id = excluded.evidence_snapshot_id,
                             evidence_url = excluded.evidence_url,
                             matched_text = excluded.matched_text,
                             last_verified = now(), status = 'present'""",
            (source_account_id, target_account_id, evidence_type,
             evidence_snapshot_id, evidence_url, matched_text),
        )


def log_api_usage(conn, service: str, endpoint: str, units: int,
                  est_cost_cents: int = 0, note: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into api_usage (service, endpoint, units, est_cost_cents, note)
               values (%s, %s, %s, %s, %s)""",
            (service, endpoint, units, est_cost_cents, note),
        )
