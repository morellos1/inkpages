"""Bluesky discovery worker.

Enumerates artist rosters (feed generators, starter packs, curated lists),
hydrates profiles via the free public AppView, writes accounts + snapshots,
and extracts identity edges and no-AI attestations from bios.

Usage:
  uv run python -m inkpages.discover_bluesky --bootstrap-query art --bootstrap-top 2
  uv run python -m inkpages.discover_bluesky --feed at://did:plc:.../app.bsky.feed.generator/aaal...
  uv run python -m inkpages.discover_bluesky --starter-pack https://bsky.app/starter-pack/<handle>/<rkey>
  uv run python -m inkpages.discover_bluesky --list at://did:plc:.../app.bsky.graph.list/...
  uv run python -m inkpages.discover_bluesky --hydrate-known --limit 2400
"""
import argparse
from collections import Counter

import httpx

from . import db
from .bluesky import Bluesky
from .extract import (BSKY_NSFW_SELF_LABELS, find_attestations,
                      find_commission_status, find_email, find_mentions,
                      find_nsfw_flags, find_platform_links, find_website_links)


def collect_actors(bsky: Bluesky, args) -> dict[str, dict]:
    """did -> {'via': discovered_via, 'details': {...}} from all sources;
    first source wins."""
    actors: dict[str, dict] = {}

    def add(did: str, via: str, details: dict) -> None:
        actors.setdefault(did, {"via": via, "details": details})

    feeds = [{"uri": uri, "displayName": uri} for uri in args.feed]
    if args.bootstrap_query:
        found = bsky.popular_feeds(args.bootstrap_query, limit=args.bootstrap_top)
        print(f"bootstrap: {len(found)} popular feeds for {args.bootstrap_query!r}: "
              + ", ".join(f.get("displayName", "?") for f in found))
        feeds += found

    for feed in feeds:
        authors = bsky.feed_authors(feed["uri"], max_posts=args.posts_per_feed)
        print(f"feed {feed.get('displayName', feed['uri'])}: {len(authors)} authors")
        for a in authors:
            add(a["did"], "bsky_feed", {"uri": feed["uri"], "name": feed.get("displayName")})

    for uri in args.starter_pack:
        members = bsky.starter_pack_members(uri)
        print(f"starter pack {uri}: {len(members)} members")
        for m in members:
            add(m["did"], "bsky_starter_pack", {"uri": uri})

    for uri in args.list:
        members = bsky.list_members(uri)
        print(f"list {uri}: {len(members)} members")
        for m in members:
            add(m["did"], "bsky_list", {"uri": uri})

    return actors


def process_profile(conn, platforms: dict[str, int], profile: dict,
                    via: str, details: dict, stats: Counter) -> None:
    did = profile["did"]
    handle = profile.get("handle") or did
    bio = profile.get("description")

    account_id = db.get_or_create_account(
        conn, platforms["bluesky"],
        native_id=did,
        handle=handle,
        display_name=profile.get("displayName"),
        profile_url=f"https://bsky.app/profile/{handle}",
        status="active",
        followers_count=profile.get("followersCount"),
        discovered_via=via,
        discovery_details=details,
        hydrated=True,
    )
    stats["accounts"] += 1

    if db.is_suppressed(conn, account_id):
        stats["skipped_suppressed"] += 1
        return

    snapshot_id = db.insert_snapshot(
        conn, account_id,
        bio_text=bio,
        display_name=profile.get("displayName"),
        followers_count=profile.get("followersCount"),
        following_count=profile.get("followsCount"),
        raw=profile,
        fetch_source="bsky:getProfiles",
    )
    stats["snapshots"] += 1

    db.set_avatar(conn, account_id, profile.get("avatar"))
    # Only write signals the current bio yields — never wipe a known value
    # with None/unknown because a rephrased bio dropped the marker.
    if email := find_email(bio):
        db.set_contact_email(conn, account_id, email)
    if comm := find_commission_status(
            "\n".join(filter(None, [bio, profile.get("displayName")]))):
        db.set_commission(conn, account_id, comm, None)
        stats["commission_signals"] += 1

    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        stats["attestations"] += 1

    for signal, matched in find_nsfw_flags(bio):
        db.upsert_content_flag(conn, account_id, "nsfw", signal, matched, snapshot_id)
        stats["nsfw_flags"] += 1
    # Account-level self-labels declared on the profile record itself.
    for label in profile.get("labels", []):
        if label.get("val") in BSKY_NSFW_SELF_LABELS and label.get("src") == did:
            db.upsert_content_flag(conn, account_id, "nsfw", "self_label",
                                   label["val"], snapshot_id)
            stats["nsfw_flags"] += 1

    for link in find_platform_links(bio) + find_website_links(bio):
        platform_id = platforms.get(link.platform)
        if platform_id is None:
            continue
        target_id = db.get_or_create_account(
            conn, platform_id,
            native_id=link.native_id,
            handle=link.handle or link.native_id,
            profile_url=link.url,
            discovered_via="bio_link",
            discovery_details={"source_account_id": account_id},
        )
        db.upsert_edge(
            conn, account_id, target_id,
            evidence_type="bio_link",
            evidence_snapshot_id=snapshot_id,
            evidence_url=link.url,
            matched_text=None,
            claim="related" if link.platform == "website" else "same_person",
            relation_hint="website" if link.platform == "website" else None,
        )
        stats["edges"] += 1

    # @mentions: alt-account claims cluster; related accounts (partner, pfp
    # artist, bare mentions) are recorded but never merge.
    for mention in find_mentions(bio, "bluesky"):
        target_id = db.get_or_create_account(
            conn, platforms["bluesky"],
            handle=mention.handle,
            profile_url=f"https://bsky.app/profile/{mention.handle}",
            discovered_via="bio_mention",
            discovery_details={"source_account_id": account_id},
        )
        db.upsert_edge(
            conn, account_id, target_id,
            evidence_type="bio_mention",
            evidence_snapshot_id=snapshot_id,
            evidence_url=None,
            matched_text=mention.matched_text,
            claim=mention.claim,
            relation_hint=mention.relation_hint,
        )
        stats[f"mentions_{mention.claim}"] += 1


def hydrate_known(conn, bsky: Bluesky, platforms: dict[str, int], limit: int,
                  stats: Counter) -> list[dict]:
    """Cross-hydration backfill: fetch profiles for bluesky accounts that were
    referenced (bio links, hub crawls, mentions) but never hydrated. Returns
    the fetched profiles so the caller's activity pass can cover them."""
    pid = platforms["bluesky"]
    with conn.cursor() as cur:
        # Bluesky handles are lowercase; normalize held rows so
        # get_or_create's claim-by-handle path can find them (skip rows whose
        # lowercase twin already exists — the twin carries the identity).
        cur.execute(
            """update accounts a set handle = lower(handle)
               where platform_id = %s and last_hydrated is null
                 and handle <> lower(handle)
                 and not exists (select 1 from accounts b
                                 where b.platform_id = a.platform_id
                                   and b.handle = lower(a.handle) and b.id <> a.id)""",
            (pid,))
        cur.execute(
            """select id, handle, native_id, discovered_via from accounts
               where platform_id = %s and last_hydrated is null
                 and status <> 'deleted'
               order by id limit %s""",
            (pid, limit))
        held = cur.fetchall()
    print(f"hydrate-known: {len(held)} held bluesky accounts")

    # actor (did if known, else handle) -> held row. Handles are globally
    # unique on bluesky, so handle-keyed claiming is safe (unlike misskey).
    by_actor: dict[str, tuple] = {}
    for row in held:
        row_id, handle, native_id, via = row
        actor = native_id if (native_id or "").startswith("did:") else (handle or "").lower()
        if actor:
            by_actor.setdefault(actor, row)

    actors = list(by_actor)
    profiles: list[dict] = []
    failed: set[str] = set()  # transient errors — stay held for a later run
    for i in range(0, len(actors), 25):
        batch = actors[i:i + 25]
        try:
            profiles += bsky.get_profiles(batch)
        except httpx.HTTPStatusError:
            # One unresolvable actor 400s the whole batch — retry singly;
            # per-actor 400s are definitively gone (fall through to the
            # deleted sweep), other errors stay held.
            for actor in batch:
                try:
                    profiles.append(bsky.get_profile(actor))
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 400:
                        failed.add(actor)
                except httpx.HTTPError:
                    failed.add(actor)
        except httpx.HTTPError:
            failed.update(batch)
        if i and i % 500 == 0:
            print(f"  …{len(profiles)} profiles fetched")

    matched: set[str] = set()
    seen_dids: set[str] = set()
    for profile in profiles:
        did = profile.get("did")
        keys = [k for k in (did, (profile.get("handle") or "").lower())
                if k in by_actor]
        if not keys:
            continue
        matched.update(keys)
        if not did or did in seen_dids:
            continue
        seen_dids.add(did)
        row_id, _, _, via = by_actor[keys[0]]
        process_profile(conn, platforms, profile, via,
                        {"backfill": "hydrate_known"}, stats)
        stats["hydrated"] += 1
        with conn.cursor() as cur:
            # If get_or_create resolved to a pre-existing row for this DID
            # instead of claiming the held row, the held row is a stale
            # handle alias — retire it so it leaves the backlog.
            cur.execute(
                """update accounts set status = 'deleted', last_hydrated = now()
                   where id = %s and last_hydrated is null""",
                (row_id,))
            if cur.rowcount:
                stats["alias_retired"] += 1
        if stats["hydrated"] % 500 == 0:
            conn.commit()

    # Actors a successful batch simply didn't return (or per-actor 400s):
    # deactivated / suspended / handle no longer resolves.
    for actor in set(actors) - matched - failed:
        with conn.cursor() as cur:
            cur.execute(
                """update accounts set status = 'deleted', last_hydrated = now()
                   where id = %s and last_hydrated is null""",
                (by_actor[actor][0],))
        stats["deleted"] += 1
    stats["fetch_failed"] = len(failed)
    conn.commit()
    return [p for p in profiles if p.get("did") in seen_dids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", action="append", default=[],
                        help="feed generator at:// URI (repeatable)")
    parser.add_argument("--starter-pack", action="append", default=[],
                        help="starter pack at:// URI or bsky.app URL (repeatable)")
    parser.add_argument("--list", action="append", default=[],
                        help="curated list at:// URI (repeatable)")
    parser.add_argument("--bootstrap-query", default=None,
                        help="discover popular feeds matching this query")
    parser.add_argument("--bootstrap-top", type=int, default=2)
    parser.add_argument("--posts-per-feed", type=int, default=100)
    parser.add_argument("--no-activity", action="store_true",
                        help="skip per-account last-post lookups")
    parser.add_argument("--hydrate-known", action="store_true",
                        help="fetch profiles for held bluesky accounts "
                             "(bio-link/hub targets never hydrated)")
    parser.add_argument("--limit", type=int, default=2400,
                        help="max held accounts per hydrate-known run")
    args = parser.parse_args()

    has_sources = bool(args.feed or args.starter_pack or args.list or args.bootstrap_query)
    if not (has_sources or args.hydrate_known):
        parser.error("no sources given (use --feed/--starter-pack/--list, "
                     "--bootstrap-query, or --hydrate-known)")

    bsky = Bluesky()
    stats: Counter = Counter()

    with db.connect() as conn:
        platforms = db.platform_ids(conn)
        profiles: list[dict] = []
        if has_sources:
            actors = collect_actors(bsky, args)
            print(f"collected {len(actors)} unique actors; hydrating…")

            profiles = bsky.get_profiles(list(actors))
            for profile in profiles:
                meta = actors[profile["did"]]
                process_profile(conn, platforms, profile, meta["via"], meta["details"], stats)
        if args.hydrate_known:
            profiles += hydrate_known(conn, bsky, platforms, args.limit, stats)
        conn.commit()

        if not args.no_activity:
            from datetime import datetime

            # Network round-trips first, DB writes after — never hold a
            # cursor/transaction open across N sequential API calls.
            activity = {p["did"]: ts for p in profiles
                        if (ts := bsky.last_post_time(p["did"]))}
            with conn.cursor() as cur:
                for did, ts in activity.items():
                    cur.execute(
                        """update accounts set last_post_at = %s
                           where platform_id = %s and native_id = %s""",
                        (datetime.fromisoformat(ts.replace("Z", "+00:00")),
                         platforms["bluesky"], did),
                    )
                    stats["activity_updated"] += 1
            conn.commit()

        for endpoint, units in sorted(bsky.calls.items()):
            db.log_api_usage(conn, "bluesky", endpoint, units, 0)
        conn.commit()

    print("done:", dict(stats))
    print("api calls:", dict(bsky.calls))


if __name__ == "__main__":
    main()
