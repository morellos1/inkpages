"""Database helpers shared by pipeline workers."""
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]


def env_var(name: str) -> str | None:
    if value := os.environ.get(name):
        return value
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1] or None
    return None


def database_url() -> str:
    if url := env_var("DATABASE_URL"):
        return url
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
    last_post_at=None,
) -> int:
    """Upsert an account. First discovery wins for discovered_via; profile
    fields refresh on re-hydration. A handle-only row (bio-link target) is
    claimed and given its native_id at hydration instead of duplicating."""
    import json

    details = json.dumps(discovery_details) if discovery_details else None
    with conn.cursor() as cur:
        if native_id is not None:
            cur.execute(
                "select id from accounts where platform_id = %s and native_id = %s",
                (platform_id, native_id),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """select id from accounts
                       where platform_id = %s and handle = %s and native_id is null
                       limit 1""",
                    (platform_id, handle),
                )
                row = cur.fetchone()
            if row:
                cur.execute(
                    """update accounts
                       set native_id = %s, handle = %s,
                           display_name = coalesce(%s, display_name),
                           profile_url = coalesce(%s, profile_url),
                           -- 'hidden' is an admin-side verification cull;
                           -- only an explicit admin action lifts it.
                           status = case when accounts.status = 'hidden'
                                         then 'hidden' else %s end,
                           followers_count = coalesce(%s, followers_count),
                           last_post_at = coalesce(%s, last_post_at),
                           last_hydrated = case when %s then now() else last_hydrated end
                       where id = %s""",
                    (native_id, handle, display_name, profile_url, status,
                     followers_count, last_post_at, hydrated, row[0]),
                )
                return row[0]
            cur.execute(
                """insert into accounts (platform_id, native_id, handle, display_name,
                                         profile_url, status, followers_count, last_post_at,
                                         discovered_via, discovery_details, last_hydrated)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           case when %s then now() end)
                   returning id""",
                (platform_id, native_id, handle, display_name, profile_url,
                 status, followers_count, last_post_at, discovered_via, details, hydrated),
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


def upsert_content_flag(conn, account_id: int, flag: str, signal: str,
                        matched_text: str | None, evidence_snapshot_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into content_flags (account_id, flag, signal, matched_text, evidence_snapshot_id)
               values (%s, %s, %s, %s, %s)
               on conflict (account_id, flag, signal, (coalesce(matched_text, '')))
               do update set last_seen = now(), active = true,
                             evidence_snapshot_id = excluded.evidence_snapshot_id""",
            (account_id, flag, signal, matched_text, evidence_snapshot_id),
        )


def upsert_edge(conn, source_account_id: int, target_account_id: int, *,
                evidence_type: str, evidence_snapshot_id: int,
                evidence_url: str | None, matched_text: str | None,
                claim: str = "same_person", relation_hint: str | None = None) -> None:
    if source_account_id == target_account_id:
        return
    with conn.cursor() as cur:
        cur.execute(
            """insert into identity_edges (source_account_id, target_account_id, evidence_type,
                                           evidence_snapshot_id, evidence_url, matched_text,
                                           claim, relation_hint, last_verified)
               values (%s, %s, %s, %s, %s, %s, %s, %s, now())
               on conflict (source_account_id, target_account_id, evidence_type)
               do update set evidence_snapshot_id = excluded.evidence_snapshot_id,
                             evidence_url = excluded.evidence_url,
                             matched_text = excluded.matched_text,
                             claim = excluded.claim,
                             relation_hint = excluded.relation_hint,
                             last_verified = now(), status = 'present'
               -- A human dismissed this edge as pure noise; re-extraction of
               -- the same bio link must never resurrect it.
               where identity_edges.status is distinct from 'dismissed'""",
            (source_account_id, target_account_id, evidence_type,
             evidence_snapshot_id, evidence_url, matched_text, claim, relation_hint),
        )


def set_platform_stats(conn, account_id: int, stats: dict) -> None:
    import json

    with conn.cursor() as cur:
        cur.execute("update accounts set platform_stats = %s where id = %s",
                    (json.dumps(stats), account_id))


def set_avatar(conn, account_id: int, url: str | None) -> None:
    if not url:
        return
    with conn.cursor() as cur:
        cur.execute("update accounts set avatar_url = %s where id = %s", (url, account_id))


def set_contact_email(conn, account_id: int, email: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute("update accounts set contact_email = %s where id = %s",
                    (email, account_id))


def set_commission(conn, account_id: int, found, checked_at) -> None:
    """found is (status, confidence, matched) or None => unknown."""
    status, confidence, detail = found if found else ("unknown", None, None)
    with conn.cursor() as cur:
        cur.execute(
            """update accounts
               set commission_status = %s, commission_confidence = %s,
                   commission_detail = %s, commission_checked_at = coalesce(%s, now())
               where id = %s""",
            (status, confidence, detail, checked_at, account_id),
        )


def touch_last_post(conn, account_id: int, last_post_at) -> None:
    if last_post_at is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            """update accounts set last_post_at = %s
               where id = %s and (last_post_at is null or last_post_at < %s)""",
            (last_post_at, account_id, last_post_at),
        )


def log_api_usage(conn, service: str, endpoint: str, units: int,
                  est_cost_cents: int = 0, note: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into api_usage (service, endpoint, units, est_cost_cents, note)
               values (%s, %s, %s, %s, %s)""",
            (service, endpoint, units, est_cost_cents, note),
        )
