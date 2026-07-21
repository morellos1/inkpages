"""Bluesky discovery worker.

Enumerates artist rosters (feed generators, starter packs, curated lists),
hydrates profiles via the free public AppView, writes accounts + snapshots,
and extracts identity edges and no-AI attestations from bios.

Usage:
  uv run python -m inkpages.discover_bluesky --bootstrap-query art --bootstrap-top 2
  uv run python -m inkpages.discover_bluesky --feed at://did:plc:.../app.bsky.feed.generator/aaal...
  uv run python -m inkpages.discover_bluesky --starter-pack https://bsky.app/starter-pack/<handle>/<rkey>
  uv run python -m inkpages.discover_bluesky --list at://did:plc:.../app.bsky.graph.list/...
"""
import argparse
from collections import Counter

from . import db
from .bluesky import Bluesky
from .extract import find_attestations, find_platform_links


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

    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        stats["attestations"] += 1

    for link in find_platform_links(bio):
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
        )
        stats["edges"] += 1


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
    args = parser.parse_args()

    if not (args.feed or args.starter_pack or args.list or args.bootstrap_query):
        parser.error("no sources given (use --feed/--starter-pack/--list or --bootstrap-query)")

    bsky = Bluesky()
    stats: Counter = Counter()

    with db.connect() as conn:
        platforms = db.platform_ids(conn)
        actors = collect_actors(bsky, args)
        print(f"collected {len(actors)} unique actors; hydrating…")

        profiles = bsky.get_profiles(list(actors))
        for profile in profiles:
            meta = actors[profile["did"]]
            process_profile(conn, platforms, profile, meta["via"], meta["details"], stats)
        conn.commit()

        for endpoint, units in sorted(bsky.calls.items()):
            db.log_api_usage(conn, "bluesky", endpoint, units, 0)
        conn.commit()

    print("done:", dict(stats))
    print("api calls:", dict(bsky.calls))


if __name__ == "__main__":
    main()
