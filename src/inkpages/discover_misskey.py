"""Cross-hydrate known misskey accounts via each instance's open API (free).

Misskey instances serve full profiles unauthenticated: POST /api/users/show
{"username": u, "host": h} -> name, description, avatarUrl, followersCount,
fields[] (the profile link table -> profile_field edges, pixiv-social-block
rules: user-entered, no guard exemptions) and roles (misskey.io surfaces a
Skeb-creator verification role — recorded in platform_stats, display only).

Enrichment-only for now (cross-hydration rule: every held misskey account
gets enriched); discovery via instance art channels can come later.

Usage: uv run python -m inkpages.discover_misskey --hydrate-known [--limit N]
"""
import argparse
import re
import time
from collections import Counter

import httpx
from psycopg.rows import dict_row

from . import db
from .extract import (find_attestations, find_commission_status, find_email,
                      find_nsfw_flags, find_platform_links, find_website_links)

PACE_S = 0.6
# Give up on an instance after this many consecutive failures (down, walled,
# or rate-limiting hard) — remaining accounts stay unhydrated for a re-run.
INSTANCE_FAILURE_LIMIT = 5

UA = {"User-Agent": "inkpages/0.1 (no-AI artist directory)"}

# https://misskey.io/@name            -> local user
# https://misskey.io/@name@remote.tld -> REMOTE user shown on misskey.io; the
# username alone would resolve to a DIFFERENT local person — host must be
# passed through or we'd hydrate the wrong identity.
_REF = re.compile(r"https?://([^/]+)/@([A-Za-z0-9_]+)(?:@([A-Za-z0-9._-]+))?")


def parse_ref(profile_url: str | None):
    m = _REF.match(profile_url or "")
    if not m:
        return None
    return {"instance": m.group(1).lower(), "username": m.group(2),
            "host": m.group(3).lower() if m.group(3) else None}


def fetch_user(client: httpx.Client, ref: dict):
    resp = client.post(f"https://{ref['instance']}/api/users/show",
                       json={"username": ref["username"], "host": ref["host"]},
                       timeout=20)
    if resp.status_code == 404:
        return "gone"
    if resp.status_code == 429:
        time.sleep(10)
        resp = client.post(f"https://{ref['instance']}/api/users/show",
                           json={"username": ref["username"], "host": ref["host"]},
                           timeout=20)
    resp.raise_for_status()
    return resp.json()


def fields_text(data: dict) -> str:
    """The profile-field table as one line per 'name: value' pair."""
    lines = []
    for field in data.get("fields") or []:
        name, value = str(field.get("name") or ""), str(field.get("value") or "")
        if value:
            lines.append(f"{name}: {value}")
    return "\n".join(lines)


def process_account(conn, platforms, account, data, stats: Counter) -> None:
    ref = account["ref"]
    bio = data.get("description") or ""
    native_id = f"{ref['instance']}:{data['id']}"
    followers = data.get("followersCount")
    display_name = data.get("name") or None

    with conn.cursor() as cur:
        # Update THIS row directly — get_or_create's claim-by-handle path
        # could grab a same-username row from another instance.
        cur.execute(
            """update accounts
               set native_id = coalesce(native_id, %s),
                   display_name = coalesce(%s, display_name),
                   followers_count = coalesce(%s, followers_count),
                   status = case when status = 'hidden' then 'hidden' else 'active' end,
                   last_hydrated = now()
               where id = %s""",
            (native_id, display_name, followers, account["id"]))

    if db.is_suppressed(conn, account["id"]):
        stats["skipped_suppressed"] += 1
        return

    ftext = fields_text(data)
    roles = [r.get("name") for r in (data.get("roles") or []) if r.get("name")]
    snapshot_id = db.insert_snapshot(
        conn, account["id"], bio_text=bio or None, display_name=display_name,
        followers_count=followers, following_count=data.get("followingCount"),
        raw={"id": data.get("id"), "host": data.get("host"),
             "fields": data.get("fields") or [], "roles": roles,
             "isBot": data.get("isBot"), "uri": data.get("uri")},
        fetch_source="misskey:users/show",
    )
    stats["snapshots"] += 1
    db.set_avatar(conn, account["id"], data.get("avatarUrl"))
    if roles:
        db.set_platform_stats(conn, account["id"], {"misskey_roles": roles})

    if email := find_email("\n".join(filter(None, [bio, ftext]))):
        db.set_contact_email(conn, account["id"], email)
    if comm := find_commission_status("\n".join(filter(None, [bio, display_name, ftext]))):
        db.set_commission(conn, account["id"], comm, None)
        stats["commission_signals"] += 1
    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account["id"], signal, matched, snapshot_id)
        stats["attestations"] += 1
    for signal, matched in find_nsfw_flags("\n".join(filter(None, [bio, ftext]))):
        db.upsert_content_flag(conn, account["id"], "nsfw", signal, matched, snapshot_id)
        stats["nsfw_flags"] += 1

    # Edges. fields[] are user-entered profile fields -> profile_field (the
    # pixiv-social-block class: same_person for platform links, related for
    # websites, NO guard exemptions; reextract never retracts profile_field).
    # Description links are ordinary bio links.
    emitted: set[int] = set()
    for evidence_type, text in (("profile_field", ftext), ("bio_link", bio)):
        if not text:
            continue
        for link in find_platform_links(text) + find_website_links(text):
            platform_id = platforms.get(link.platform)
            if platform_id is None:
                continue
            target_id = db.get_or_create_account(
                conn, platform_id, native_id=link.native_id,
                handle=link.handle or link.native_id, profile_url=link.url,
                discovered_via="bio_link",
                discovery_details={"source_account_id": account["id"],
                                   "via": "misskey_profile"},
            )
            if target_id == account["id"] or target_id in emitted:
                continue
            emitted.add(target_id)
            claim = "related" if link.platform == "website" else "same_person"
            db.upsert_edge(conn, account["id"], target_id,
                           evidence_type=evidence_type,
                           evidence_snapshot_id=snapshot_id,
                           evidence_url=link.url, matched_text=None,
                           claim=claim,
                           relation_hint="website" if link.platform == "website" else None)
            stats[f"edges_{evidence_type}"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hydrate-known", action="store_true",
                        help="enrich held misskey accounts that were never fetched")
    parser.add_argument("--refresh", action="store_true",
                        help="also re-fetch already-hydrated accounts (oldest first)")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if not (args.hydrate_known or args.refresh):
        parser.error("nothing to do: pass --hydrate-known (and/or --refresh)")

    stats: Counter = Counter()
    with db.connect() as conn, httpx.Client(headers=UA) as client:
        platforms = db.platform_ids(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""select a.id, a.handle::text, a.profile_url
                   from accounts a
                   where a.platform_id = %s and a.status not in ('deleted', 'hidden')
                     and a.profile_url is not null
                     {'' if args.refresh else 'and a.last_hydrated is null'}
                   order by a.last_hydrated asc nulls first, a.id
                   limit %s""",
                (platforms["misskey"], args.limit))
            accounts = cur.fetchall()

        instance_failures: Counter = Counter()
        for account in accounts:
            ref = parse_ref(account["profile_url"])
            if ref is None:
                stats["unparseable_url"] += 1
                continue
            if instance_failures[ref["instance"]] >= INSTANCE_FAILURE_LIMIT:
                stats[f"skipped_{ref['instance']}_down"] += 1
                continue
            account["ref"] = ref
            time.sleep(PACE_S)
            try:
                data = fetch_user(client, ref)
            except (httpx.HTTPError, ValueError) as exc:
                instance_failures[ref["instance"]] += 1
                stats["fetch_failed"] += 1
                print(f"  fetch failed for @{ref['username']}@{ref['instance']}: {exc}")
                continue
            instance_failures[ref["instance"]] = 0
            if data == "gone":
                with conn.cursor() as cur:
                    cur.execute("""update accounts set status = 'deleted',
                                   last_hydrated = now() where id = %s""",
                                (account["id"],))
                stats["deleted"] += 1
                conn.commit()
                continue
            process_account(conn, platforms, account, data, stats)
            stats["hydrated"] += 1
            conn.commit()
            if stats["hydrated"] % 50 == 0:
                print(f"  …{stats['hydrated']} hydrated")

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
