"""Re-run extraction over stored snapshots — no network, no API spend.

Extraction is a pure function of snapshots, so improved parsers and newly
supported platforms apply retroactively. This worker also *retracts*
bio-derived edges the current parser no longer reproduces (e.g. links that
were half a handle because the platform truncated the URL), closes cluster
memberships that were added by now-retracted edges, deactivates signals whose
markers vanished, and auto-rejects pending review items built on retracted
evidence.

Usage: uv run python -m inkpages.reextract
"""
import json
from collections import Counter

from psycopg.rows import dict_row

from . import db
from .discover_skeb import emit_structured_edges
from .extract import (BSKY_NSFW_SELF_LABELS, find_attestations,
                      find_commission_status, find_email, find_mentions,
                      find_nsfw_flags, find_platform_links, find_website_links)
from .twitter import expanded_urls

REEXTRACTABLE_ATT = ("bio_tag", "glaze_mention", "nightshade_mention")
REEXTRACTABLE_NSFW = ("bio_marker", "self_label")


def latest_snapshots(conn):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select distinct on (s.account_id)
                      s.account_id, s.id as snapshot_id, s.bio_text, s.raw,
                      s.captured_at, a.handle::text, a.display_name,
                      p.slug as platform, p.id as platform_id
               from account_snapshots s
               join accounts a on a.id = s.account_id
               join platforms p on p.id = a.platform_id
               where p.kind <> 'link_hub'
                 -- hub pages, graphtreon ranking rows, artstation trending
                 -- rows and deviantart about pages are not (whole) bios:
                 -- truncated/absent text that must never drive bio_link
                 -- retraction against the links the full page yielded
                 -- (deviantart edges come from the about markup, while the
                 -- snapshot stores only tagline+excerpt).
                 and s.fetch_source not in ('hub_crawl', 'graphtreon:ranking',
                                            'artstation:trending',
                                            'deviantart:rss', 'deviantart:about')
               order by s.account_id, s.captured_at desc"""
        )
        return cur.fetchall()


def process(conn, platforms, row, stats) -> list[int]:
    """Re-extract one account's latest snapshot; returns retracted edge ids."""
    account_id, snapshot_id = row["account_id"], row["snapshot_id"]
    bio = row["bio_text"] or ""
    raw = row["raw"] or {}

    link_text = bio
    if row["platform"] == "twitter":
        link_text = "\n".join([bio, raw.get("location") or ""] + expanded_urls(raw))

    location = raw.get("location") if row["platform"] == "twitter" else None
    # Same rule as every discovery worker: only write signals the snapshot
    # actually yields — never wipe a stored email/commission with None/unknown
    # (this used to reset 300+ pixiv acceptRequest statuses every run).
    if email := find_email("\n".join(filter(None, [bio, location]))):
        db.set_contact_email(conn, account_id, email)
    if row["platform"] == "skeb" and "acceptable" in raw:
        # Platform-authoritative value from the stored API response — never
        # let a bio re-parse stomp it.
        db.set_commission(conn, account_id,
                          ("open", 0.95, "skeb:acceptable") if raw["acceptable"]
                          else ("closed", 0.95, "skeb:not accepting"),
                          row["captured_at"])
    elif (row["platform"] == "pixiv"
            and ((raw.get("commission") or {}).get("acceptRequest") is True)):
        # pixiv's request flag is platform state too (mirrors discover_pixiv).
        db.set_commission(conn, account_id,
                          ("open", 0.9, "pixiv:accept_request"),
                          row["captured_at"])
    else:
        comm_text = "\n".join(filter(None, [bio, row["display_name"], location]))
        if comm := find_commission_status(comm_text):
            db.set_commission(conn, account_id, comm, row["captured_at"])

    # Skeb structured fields (twitter_uid, id fields, url field, service
    # links) live in raw, not bio_text — re-derive their profile_field edges
    # so a bio re-parse never retracts them. Retraction below stays
    # bio_link-only: other profile_field sources (pixiv social block) are
    # owned by discovery.
    if row["platform"] == "skeb":
        emit_structured_edges(conn, platforms, account_id, row["handle"],
                              raw, snapshot_id, stats)

    link_targets: set[int] = set()
    for link in find_platform_links(link_text) + find_website_links(link_text):
        platform_id = platforms.get(link.platform)
        if platform_id is None:
            continue
        if link.platform == row["platform"] and link.handle \
                and link.handle.lower() == row["handle"].lower():
            continue
        target_id = db.get_or_create_account(
            conn, platform_id, native_id=link.native_id,
            handle=link.handle or link.native_id, profile_url=link.url,
            discovered_via="bio_link",
            discovery_details={"source_account_id": account_id},
        )
        if target_id != account_id:
            db.upsert_edge(conn, account_id, target_id, evidence_type="bio_link",
                           evidence_snapshot_id=snapshot_id,
                           evidence_url=link.url, matched_text=None,
                           claim="related" if link.platform == "website" else "same_person",
                           relation_hint="website" if link.platform == "website" else None)
            link_targets.add(target_id)

    mention_targets: set[int] = set()
    for mention in find_mentions(bio, row["platform"]):
        if mention.handle.lower() == row["handle"].lower():
            continue
        target_id = db.get_or_create_account(
            conn, platforms[row["platform"]], handle=mention.handle,
            discovered_via="bio_mention",
            discovery_details={"source_account_id": account_id},
        )
        if target_id != account_id:
            db.upsert_edge(conn, account_id, target_id, evidence_type="bio_mention",
                           evidence_snapshot_id=snapshot_id, evidence_url=None,
                           matched_text=mention.matched_text,
                           claim=mention.claim, relation_hint=mention.relation_hint)
            mention_targets.add(target_id)

    att = find_attestations(bio)
    for signal, matched in att:
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
    nsfw = find_nsfw_flags(bio)
    if row["platform"] == "bluesky":
        for label in raw.get("labels", []):
            if label.get("val") in BSKY_NSFW_SELF_LABELS and label.get("src") == raw.get("did"):
                nsfw.append(("self_label", label["val"]))
    for signal, matched in nsfw:
        db.upsert_content_flag(conn, account_id, "nsfw", signal, matched, snapshot_id)

    # Deactivate signals whose markers vanished from the latest bio.
    _deactivate_stale(conn, account_id, "attestations", REEXTRACTABLE_ATT, att)
    _deactivate_stale(conn, account_id, "content_flags", REEXTRACTABLE_NSFW, nsfw)

    retracted: list[int] = []
    with conn.cursor() as cur:
        # Retract bio-derived edges no longer reproduced (shortener-derived
        # edges carry ' -> ' evidence and are excluded — crawl_links owns them).
        cur.execute(
            """update identity_edges set status = 'retracted'
               where source_account_id = %s and status = 'present'
                 and evidence_type = 'bio_link'
                 and coalesce(evidence_url, '') not like '%% -> %%'
                 and target_account_id <> all(%s)
               returning id""",
            (account_id, list(link_targets) or [0]),
        )
        retracted += [r[0] for r in cur.fetchall()]
        cur.execute(
            """update identity_edges set status = 'retracted'
               where source_account_id = %s and status = 'present'
                 and evidence_type = 'bio_mention'
                 and target_account_id <> all(%s)
               returning id""",
            (account_id, list(mention_targets) or [0]),
        )
        retracted += [r[0] for r in cur.fetchall()]
    stats["edges_retracted"] += len(retracted)
    return retracted


def _deactivate_stale(conn, account_id, table, signals, produced) -> None:
    produced_keys = {(s, m or "") for s, m in produced}
    with conn.cursor() as cur:
        cur.execute(
            f"""select id, signal, coalesce(matched_text, '') from {table}
                where account_id = %s and active and signal = any(%s)""",
            (account_id, list(signals)),
        )
        stale = [r[0] for r in cur.fetchall() if (r[1], r[2]) not in produced_keys]
        if stale:
            cur.execute(f"update {table} set active = false where id = any(%s)", (stale,))


def repair_downstream(conn, retracted: list[int], stats) -> None:
    """Close memberships added by retracted edges; reject review items that
    were built on them."""
    if not retracted:
        return
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select artist_id, (details ->> 'account_id')::bigint as account_id,
                      (details ->> 'edge_id')::bigint as edge_id
               from artist_events
               where event = 'account_added' and (details ->> 'edge_id')::bigint = any(%s)""",
            (retracted,),
        )
        for row in cur.fetchall():
            cur.execute(
                """update artist_accounts set removed_at = now()
                   where artist_id = %s and account_id = %s and removed_at is null
                   returning id""",
                (row["artist_id"], row["account_id"]),
            )
            if cur.fetchone():
                cur.execute(
                    """insert into artist_events (artist_id, event, actor, details)
                       values (%s, 'account_removed', 'pipeline', %s)""",
                    (row["artist_id"],
                     json.dumps({"account_id": row["account_id"],
                                 "reason": "evidence_retracted",
                                 "edge_id": row["edge_id"]})),
                )
                stats["memberships_closed"] += 1
        cur.execute(
            """update review_items
               set status = 'rejected', resolved_at = now(), decided_by = 'pipeline',
                   resolution_note = 'evidence retracted by re-extraction'
               where status = 'pending' and (payload ->> 'edge_id')::bigint = any(%s)
               returning id""",
            (retracted,),
        )
        stats["review_items_rejected"] += len(cur.fetchall())


def main() -> None:
    stats: Counter = Counter()
    retracted: list[int] = []
    with db.connect() as conn:
        platforms = db.platform_ids(conn)
        rows = latest_snapshots(conn)
        for row in rows:
            retracted += process(conn, platforms, row, stats)
            stats["accounts"] += 1
        repair_downstream(conn, retracted, stats)
        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
