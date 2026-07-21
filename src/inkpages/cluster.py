"""Clustering worker: identity edges -> artist clusters.

Rules (docs/schema.md tradeoffs 3, 5; docs/pipeline.md stage 5):
- Mutual directed edge pairs (reciprocal bio links) are near-proof and merge
  automatically via union-find.
- Roster-sourced accounts with no edges become singleton artists — this keeps
  high-follower, Twitter-only artists with no external links.
- A one-directional edge attaches its target at 'strong' confidence, unless
  the target is prominent (policy.REVIEW_FOLLOWER_THRESHOLD) — those become
  review_items for a human, since impersonators link *to* famous accounts.
- Components containing two existing artists are never auto-merged; they
  become 'cluster_merge' review items.
- Human decisions are never overridden: memberships closed by a human stay
  closed, and clustering only ever *adds*.

Usage: uv run python -m inkpages.cluster
"""
import json
import re
from collections import Counter, defaultdict

from . import db, policy
from .extract import looks_like_artist

# bio_mention same-person claims (e.g. "nsfw alt: @x") count as strong; the
# claim filter in load_state keeps 'related' mentions out entirely.
STRONG_EVIDENCE = ("bio_link", "link_hub", "profile_field", "pinned_post", "bio_mention")


class UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def slugify(handle: str, platform_slug: str, account_id: int) -> str:
    base = handle.split(".")[0] if platform_slug == "bluesky" else handle
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", base.lower())).strip("-")
    return slug or f"artist-{account_id}"


def unique_slug(cur, slug: str) -> str:
    candidate, n = slug, 1
    while True:
        cur.execute("select 1 from artists where public_slug = %s", (candidate,))
        if cur.fetchone() is None:
            return candidate
        n += 1
        candidate = f"{slug}-{n}"


def create_artist(conn, account: dict, actor: str = "pipeline") -> int:
    """New artist anchored on one account; membership at near_proof (it is
    the account itself)."""
    with conn.cursor() as cur:
        slug = unique_slug(cur, slugify(account["handle"], account["platform_slug"], account["id"]))
        cur.execute(
            """insert into artists (public_slug, display_name, primary_account_id)
               values (%s, %s, %s) returning id""",
            (slug, account["display_name"] or account["handle"], account["id"]),
        )
        artist_id = cur.fetchone()[0]
        cur.execute(
            """insert into artist_accounts (artist_id, account_id, confidence, added_by)
               values (%s, %s, 'near_proof', 'clustering')""",
            (artist_id, account["id"]),
        )
        cur.execute(
            """insert into artist_events (artist_id, event, actor, details)
               values (%s, 'created', %s, %s)""",
            (artist_id, actor, json.dumps({"anchor_account_id": account["id"]})),
        )
    return artist_id


def add_member(conn, artist_id: int, account_id: int, confidence: str,
               details: dict, actor: str = "pipeline") -> None:
    with conn.cursor() as cur:
        # Never reopen a membership a human closed.
        cur.execute(
            """select 1 from artist_accounts
               where artist_id = %s and account_id = %s and removed_at is not null""",
            (artist_id, account_id),
        )
        if cur.fetchone():
            return
        cur.execute(
            """insert into artist_accounts (artist_id, account_id, confidence, added_by)
               values (%s, %s, %s, 'clustering')""",
            (artist_id, account_id, confidence),
        )
        cur.execute(
            """insert into artist_events (artist_id, event, actor, details)
               values (%s, 'account_added', %s, %s)""",
            (artist_id, actor, json.dumps({"account_id": account_id, **details})),
        )


def pending_review_exists(conn, kind: str, key: str, value) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """select 1 from review_items
               where kind = %s and status = 'pending' and payload ->> %s = %s""",
            (kind, key, str(value)),
        )
        return cur.fetchone() is not None


def review_exists(conn, kind: str, key: str, value) -> bool:
    """Any status — a decided item must not be re-asked every run."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from review_items where kind = %s and payload ->> %s = %s",
            (kind, key, str(value)),
        )
        return cur.fetchone() is not None


def add_review_item(conn, kind: str, payload: dict, stats: Counter) -> None:
    with conn.cursor() as cur:
        cur.execute("insert into review_items (kind, payload) values (%s, %s)",
                    (kind, json.dumps(payload)))
    stats[f"review:{kind}"] += 1


def load_state(conn):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select a.id, a.handle::text, a.display_name, a.followers_count,
                      a.discovered_via, a.status, p.slug as platform_slug,
                      p.kind as platform_kind, ls.bio_text as latest_bio
               from accounts a
               join platforms p on p.id = a.platform_id
               left join lateral (select s.bio_text from account_snapshots s
                                  where s.account_id = a.id
                                  order by s.captured_at desc limit 1) ls on true"""
        )
        accounts = {row["id"]: row for row in cur.fetchall()}
        cur.execute(
            """select id, source_account_id, target_account_id, evidence_type
               from identity_edges
               where status = 'present' and claim = 'same_person'
                 and evidence_type = any(%s)""",
            (list(STRONG_EVIDENCE),),
        )
        edges = cur.fetchall()
        cur.execute(
            """select account_id, artist_id from artist_accounts where removed_at is null"""
        )
        membership = {row["account_id"]: row["artist_id"] for row in cur.fetchall()}
    return accounts, edges, membership


def main() -> None:
    stats: Counter = Counter()
    with db.connect() as conn:
        # Self-heal: close clustering-added memberships whose justifying edge
        # has since been retracted (parser fixes, hub re-crawls).
        with conn.cursor() as cur:
            cur.execute(
                """select ev.artist_id, (ev.details ->> 'account_id')::bigint as account_id,
                          (ev.details ->> 'edge_id')::bigint as edge_id
                   from artist_events ev
                   join identity_edges e on e.id = (ev.details ->> 'edge_id')::bigint
                   join artist_accounts aa on aa.artist_id = ev.artist_id
                        and aa.account_id = (ev.details ->> 'account_id')::bigint
                        and aa.removed_at is null and aa.added_by = 'clustering'
                   where ev.event = 'account_added' and e.status = 'retracted'"""
            )
            for artist_id, account_id, edge_id in cur.fetchall():
                cur.execute(
                    """update artist_accounts set removed_at = now()
                       where artist_id = %s and account_id = %s and removed_at is null""",
                    (artist_id, account_id),
                )
                cur.execute(
                    """insert into artist_events (artist_id, event, actor, details)
                       values (%s, 'account_removed', 'pipeline', %s)""",
                    (artist_id, json.dumps({"account_id": account_id,
                                            "reason": "evidence_retracted",
                                            "edge_id": edge_id})),
                )
                stats["healed_memberships"] += 1

        accounts, edges, membership = load_state(conn)

        directed = {(e["source_account_id"], e["target_account_id"]) for e in edges}
        mutual = {frozenset(pair) for pair in directed if (pair[1], pair[0]) in directed}

        # 1. Union-find over reciprocal (near-proof) pairs.
        uf = UnionFind()
        for pair in mutual:
            a, b = tuple(pair)
            uf.union(a, b)

        components: dict[int, set[int]] = defaultdict(set)
        for account_id in uf.parent:
            components[uf.find(account_id)].add(account_id)

        # 2. Materialize components as artists (or flag conflicts).
        for members in components.values():
            artist_ids = {membership[m] for m in members if m in membership}
            if len(artist_ids) > 1:
                if not pending_review_exists(conn, "cluster_merge", "artist_ids",
                                             json.dumps(sorted(artist_ids))):
                    add_review_item(conn, "cluster_merge", {
                        "artist_ids": json.dumps(sorted(artist_ids)),
                        "account_ids": sorted(members),
                    }, stats)
                continue
            if artist_ids:
                artist_id = artist_ids.pop()
            else:
                anchor = max((accounts[m] for m in members),
                             key=lambda a: a["followers_count"] or 0)
                if db.is_suppressed(conn, anchor["id"]):
                    continue
                artist_id = create_artist(conn, anchor)
                membership[anchor["id"]] = artist_id
                stats["artists_created"] += 1
            for m in members:
                if m not in membership and not db.is_suppressed(conn, m):
                    add_member(conn, artist_id, m, "near_proof", {"via": "reciprocal"})
                    membership[m] = artist_id
                    stats["members_added"] += 1

        # 2b. Reverse-attach on platform-verified links: a profile_field edge
        # is emitted only from platform APIs (e.g. Skeb's OAuth-verified
        # twitter_uid), so when its source is unclustered and its target
        # already belongs to an artist, the source joins that artist at
        # near_proof. This is what cross-links Skeb creators into artists we
        # already have — and it is safe precisely because the platform, not a
        # copyable bio, asserts the link. Runs before singletons so a Skeb
        # account joins its existing artist instead of spawning a duplicate.
        for edge in edges:
            if edge["evidence_type"] != "profile_field":
                continue
            src, tgt = edge["source_account_id"], edge["target_account_id"]
            if src in membership or tgt not in membership:
                continue
            if db.is_suppressed(conn, src):
                continue
            add_member(conn, membership[tgt], src, "near_proof",
                       {"via": "platform_verified_link", "edge_id": edge["id"]})
            membership[src] = membership[tgt]
            stats["reverse_attached"] += 1

        # 3. Singletons: roster-sourced accounts with no membership survive on
        # their own — no edges required for existence (docs/pipeline.md stage 5).
        # Open-harvest arrivals (anyone can post a hashtag) additionally need
        # artist evidence: an art-flavored bio or their own outbound links.
        edge_sources = {e["source_account_id"] for e in edges}

        def has_artist_evidence(account) -> bool:
            if account["discovered_via"] not in policy.HARVEST_NEEDS_EVIDENCE:
                return True
            return (account["id"] in edge_sources
                    or looks_like_artist("\n".join(filter(None, [
                        account["latest_bio"], account["display_name"]]))))

        for account in accounts.values():
            if (account["id"] not in membership
                    and account["discovered_via"] in policy.ROSTER_SOURCES
                    and account["platform_kind"] != "link_hub"
                    and account["status"] in ("active", "unknown")
                    and not db.is_suppressed(conn, account["id"])):
                if not has_artist_evidence(account):
                    # Suspected non-artist: a human decides, not a silent skip.
                    if not review_exists(conn, "singleton_gate", "account_id", account["id"]):
                        add_review_item(conn, "singleton_gate", {
                            "account_id": account["id"],
                            "handle": account["handle"],
                            "platform": account["platform_slug"],
                            "followers": account["followers_count"],
                            "discovered_via": account["discovered_via"],
                        }, stats)
                    stats["skipped_no_artist_evidence"] += 1
                    continue
                artist_id = create_artist(conn, account)
                membership[account["id"]] = artist_id
                stats["singleton_artists"] += 1

        # 3b. Retroactive: demote existing single-account artists from open
        # harvests that fail the evidence test (never overrides human actions).
        with conn.cursor() as cur:
            cur.execute(
                """select ar.id as artist_id, min(aa.account_id) as account_id
                   from artists ar
                   join artist_accounts aa on aa.artist_id = ar.id and aa.removed_at is null
                   where ar.status = 'active' and ar.merged_into is null
                     and not exists (select 1 from artist_events e
                                     where e.artist_id = ar.id and e.actor like 'admin%%')
                   group by ar.id
                   having count(*) = 1""")
            singles = cur.fetchall()
        for artist_id, account_id in singles:
            account = accounts.get(account_id)
            if (account is not None
                    and account["discovered_via"] in policy.HARVEST_NEEDS_EVIDENCE
                    and not has_artist_evidence(account)):
                with conn.cursor() as cur:
                    cur.execute("update artists set status = 'needs_review', updated_at = now() where id = %s",
                                (artist_id,))
                    cur.execute(
                        """insert into artist_events (artist_id, event, actor, details)
                           values (%s, 'suppressed', 'pipeline', %s)""",
                        (artist_id, json.dumps({"reason": "no_artist_evidence",
                                                "account_id": account_id})),
                    )
                stats["demoted_no_artist_evidence"] += 1

        # 4. One-directional strong edges: attach targets, or queue for review
        # when the target is prominent (impersonation risk).
        #
        # Shared-resource guard: a target linked one-directionally by two or
        # more *different* artists (a discord server, an event site, a
        # collective page) is nobody's alt — never attach it and don't spam
        # review. Also close any earlier clustering attach of such a target.
        indegree: dict[int, set[int]] = defaultdict(set)
        for edge in edges:
            src, tgt = edge["source_account_id"], edge["target_account_id"]
            if frozenset((src, tgt)) in mutual:
                continue
            if (a := membership.get(src)) is not None:
                indegree[tgt].add(a)
        shared_targets = {t for t, artists in indegree.items() if len(artists) >= 2}

        with conn.cursor() as cur:
            for tgt in shared_targets:
                cur.execute(
                    """update artist_accounts set removed_at = now()
                       where account_id = %s and removed_at is null
                         and added_by = 'clustering' and confidence = 'strong'
                       returning artist_id""",
                    (tgt,),
                )
                for (artist_id,) in cur.fetchall():
                    cur.execute(
                        """insert into artist_events (artist_id, event, actor, details)
                           values (%s, 'account_removed', 'pipeline', %s)""",
                        (artist_id, json.dumps({"account_id": tgt,
                                                "reason": "shared_target"})),
                    )
                    membership.pop(tgt, None)
                    stats["shared_detached"] += 1

        for edge in edges:
            src, tgt = edge["source_account_id"], edge["target_account_id"]
            if frozenset((src, tgt)) in mutual or src not in membership:
                continue
            if tgt in shared_targets:
                stats["skipped_shared_target"] += 1
                continue
            src_artist = membership[src]
            tgt_artist = membership.get(tgt)
            if tgt_artist == src_artist:
                continue
            if tgt_artist is not None:
                if not pending_review_exists(conn, "cluster_merge", "edge_id", edge["id"]):
                    add_review_item(conn, "cluster_merge", {
                        "edge_id": edge["id"],
                        "artist_ids": json.dumps(sorted({src_artist, tgt_artist})),
                        "account_ids": [src, tgt],
                    }, stats)
                continue
            if db.is_suppressed(conn, tgt):
                continue
            followers = accounts[tgt]["followers_count"] or 0
            # Mention-based alt claims ("nsfw alt: @x") are the weakest
            # same-person evidence — always a human call, never an auto-attach.
            if edge["evidence_type"] == "bio_mention":
                if not review_exists(conn, "one_directional_attach", "edge_id", edge["id"]):
                    add_review_item(conn, "one_directional_attach", {
                        "edge_id": edge["id"],
                        "artist_id": src_artist,
                        "source_account_id": src,
                        "target_account_id": tgt,
                        "target_followers": followers,
                        "evidence": "bio_mention",
                    }, stats)
                continue
            if followers >= policy.REVIEW_FOLLOWER_THRESHOLD:
                if not pending_review_exists(conn, "one_directional_attach", "edge_id", edge["id"]):
                    add_review_item(conn, "one_directional_attach", {
                        "edge_id": edge["id"],
                        "artist_id": src_artist,
                        "source_account_id": src,
                        "target_account_id": tgt,
                        "target_followers": followers,
                    }, stats)
            else:
                add_member(conn, src_artist, tgt, "strong",
                           {"via": "one_directional", "edge_id": edge["id"]})
                membership[tgt] = src_artist
                stats["members_added"] += 1

        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
