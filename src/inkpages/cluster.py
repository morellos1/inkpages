"""Clustering worker: identity edges -> artist clusters.

Rules (docs/schema.md tradeoffs 3, 5; docs/pipeline.md stage 5):
- Mutual directed edge pairs (reciprocal bio links) are near-proof and merge
  automatically via union-find.
- Roster-sourced accounts with no edges become singleton artists — this keeps
  high-follower, Twitter-only artists with no external links.
- A one-directional edge attaches its target at 'strong' confidence, unless
  the target is prominent (policy.REVIEW_FOLLOWER_THRESHOLD) — those become
  review_items for a human, since impersonators link *to* famous accounts.
- Two existing artists that reference each other — a reciprocal component, or
  artist-level cyclical claims through any member accounts — auto-merge
  (cap-guarded); ambiguous conflicts become 'cluster_merge' review items.
- Human decisions are never overridden: memberships closed by a human stay
  closed, and clustering only ever *adds*.

Usage: uv run python -m inkpages.cluster
"""
import json
import re
from collections import Counter, defaultdict

from . import db, policy
from .extract import looks_like_artist, looks_like_project, project_account

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
               details: dict, actor: str = "pipeline") -> bool:
    """Returns False (no insert) when a human closed this membership —
    callers must not count or record a no-op as an attach."""
    with conn.cursor() as cur:
        # Never reopen a membership a HUMAN closed; pipeline-closed rows
        # (repairs, retraction healing) may re-form when evidence supports it.
        cur.execute(
            """select 1 from artist_events
               where artist_id = %s and event = 'account_removed'
                 and (details ->> 'account_id')::bigint = %s
                 and actor like 'admin%%'""",
            (artist_id, account_id),
        )
        if cur.fetchone():
            return False
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
    return True


def merge_artists(conn, keeper: int, losers: list[int], actor: str = "pipeline") -> None:
    """Fold losers into keeper: memberships move (history preserved), losers
    get merged_into pointers so their slugs can redirect."""
    with conn.cursor() as cur:
        for loser in losers:
            cur.execute(
                """select account_id, confidence from artist_accounts
                   where artist_id = %s and removed_at is null""", (loser,))
            admin_blocked: list[int] = []
            for account_id, confidence in cur.fetchall():
                cur.execute(
                    """update artist_accounts set removed_at = now()
                       where artist_id = %s and account_id = %s and removed_at is null""",
                    (loser, account_id))
                if not actor.startswith("admin"):
                    # Same guard as add_member: a membership a HUMAN closed on
                    # the keeper never reopens via an automatic merge.
                    cur.execute(
                        """select 1 from artist_events
                           where artist_id = %s and event = 'account_removed'
                             and (details ->> 'account_id')::bigint = %s
                             and actor like 'admin%%'""",
                        (keeper, account_id))
                    if cur.fetchone():
                        admin_blocked.append(account_id)
                        continue
                cur.execute(
                    """insert into artist_accounts (artist_id, account_id, confidence, added_by)
                       select %s, %s, %s, %s
                       where not exists (select 1 from artist_accounts
                                         where account_id = %s and removed_at is null)""",
                    (keeper, account_id, confidence,
                     "human" if actor.startswith("admin") else "clustering", account_id))
            cur.execute("update artists set merged_into = %s, updated_at = now() where id = %s",
                        (keeper, loser))
            # Collapse chains: anything that redirected to the loser now
            # redirects straight to the keeper (slug redirects stay one hop).
            cur.execute("update artists set merged_into = %s where merged_into = %s and id <> %s",
                        (keeper, loser, keeper))
            details = {"into": keeper}
            if admin_blocked:
                details["admin_blocked_accounts"] = admin_blocked
            cur.execute(
                """insert into artist_events (artist_id, event, actor, details)
                   values (%s, 'merged', %s, %s)""",
                (loser, actor, json.dumps(details)))
            # A pending anomaly flag about the absorbed artist is moot the
            # moment it stops existing — resolve NOW rather than leaving a
            # ghost item until the next cluster run's 5c recheck (an admin
            # merge from the review UI would otherwise strand it).
            cur.execute(
                """update review_items
                   set status = 'rejected', resolved_at = now(),
                       decided_by = 'pipeline:merged_away'
                   where status = 'pending' and kind = 'other'
                     and payload ->> 'type' = 'anomaly'
                     and (payload ->> 'artist_id')::bigint = %s""", (loser,))


def flip_to_connection(conn, edge_id: int, hint: str) -> None:
    """Downgrade a same-person claim to a related connection — best-effort
    display without blocking on review; re-extraction restores same_person if
    the evidence later strengthens (e.g. the link becomes reciprocal)."""
    with conn.cursor() as cur:
        cur.execute(
            """update identity_edges set claim = 'related', relation_hint = %s
               where id = %s and claim = 'same_person'""",
            (hint, edge_id))


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


def merge_rejected(conn, a: int, b: int) -> bool:
    """A human already ruled this pair is NOT the same person — that decision
    is sacred, so automation must never merge the pair again."""
    with conn.cursor() as cur:
        cur.execute(
            """select 1 from review_items
               where kind = 'cluster_merge' and status = 'rejected'
                 and payload ->> 'artist_ids' = %s""",
            (json.dumps(sorted((a, b))),),
        )
        return cur.fetchone() is not None


def resolve_pending_merges(conn, a: int, b: int, decided_by: str) -> None:
    """An auto-merge answers any pending human question for the same pair."""
    with conn.cursor() as cur:
        cur.execute(
            """update review_items
               set status = 'approved', resolved_at = now(), decided_by = %s
               where kind = 'cluster_merge' and status = 'pending'
                 and payload ->> 'artist_ids' = %s""",
            (decided_by, json.dumps(sorted((a, b)))),
        )


def add_review_item(conn, kind: str, payload: dict, stats: Counter) -> None:
    with conn.cursor() as cur:
        cur.execute("insert into review_items (kind, payload) values (%s, %s)",
                    (kind, json.dumps(payload)))
    stats[f"review:{kind}"] += 1


def purge_junk_website_accounts(conn, stats: Counter) -> None:
    """Standing sweep: link-artifact accounts that page markup minted —
    website rows on a blocklisted host (_NON_WEBSITE_DOMAINS), static
    asset files (_ASSET_EXT), glued double-URLs, and reserved path words
    captured as handles on any platform (_RESERVED_HANDLES: vgen.co/
    uploads, deviantart.com/users…). The extraction guards stop new ones,
    but edges evidenced by hub crawls are out of reextract's reach — so
    growing the guard lists in extract.py auto-cleans historical rows here
    on the next pipeline run. Retract their edges, close any memberships,
    hide the accounts. Idempotent."""
    from .extract import _ASSET_EXT, _NON_WEBSITE_DOMAINS, _RESERVED_HANDLES

    with conn.cursor() as cur:
        # Select junk regardless of current status: an already-hidden or
        # -deleted junk account can still carry PRESENT edges (a re-crawled
        # hub re-adds them; edges are out of reextract's reach), which keep
        # rendering in the Connections table. Retracting those lingering
        # edges every run is the fix — the status guard used to skip them.
        cur.execute(
            """select a.id from accounts a
               join platforms p on p.id = a.platform_id
               where (p.slug = 'website'
                      and (split_part(a.handle::text, '/', 1) ~* ('(^|\\.)('
                             || array_to_string(%(domains)s::text[], '|') || ')$')
                           or a.handle::text ~* %(asset_re)s
                           -- glued URLs: an embedded second scheme means two
                           -- links fused into one handle
                           or a.handle::text ~* 'https?:/'))
                  -- reserved path words captured as handles on any platform
                  -- (vgen.co/uploads, deviantart.com/stash, cara.app/production)
                  or (p.slug not in ('website', 'bluesky')
                      and lower(a.handle::text) = any(%(reserved)s))""",
            {"domains": [re.escape(d) for d in sorted(_NON_WEBSITE_DOMAINS)],
             "asset_re": _ASSET_EXT.pattern,
             "reserved": sorted(_RESERVED_HANDLES)})
        junk = [row[0] for row in cur.fetchall()]
        if not junk:
            return
        cur.execute(
            """update identity_edges set status = 'retracted'
               where status = 'present'
                 and (source_account_id = any(%(ids)s)
                      or target_account_id = any(%(ids)s))""", {"ids": junk})
        stats["junk_site_edges_retracted"] += cur.rowcount
        cur.execute(
            """insert into artist_events (artist_id, event, actor, details)
               select aa.artist_id, 'account_removed', 'pipeline',
                      jsonb_build_object('account_id', aa.account_id,
                                         'reason', 'junk_website_purge')
               from artist_accounts aa
               where aa.removed_at is null and aa.account_id = any(%s)""",
            (junk,))
        cur.execute(
            """update artist_accounts set removed_at = now()
               where removed_at is null and account_id = any(%s)""", (junk,))
        # Hide active/unknown junk; leave a 'deleted' (404'd) account deleted —
        # both are already out of the publish view, and the status carries a
        # distinct signal worth keeping.
        cur.execute(
            """update accounts set status = 'hidden'
               where id = any(%s) and status not in ('hidden', 'deleted')""",
            (junk,))
        stats["junk_site_accounts_hidden"] += cur.rowcount


# A YouTube channel-id handle (youtube.com/channel/UC…, 24 chars) is the same
# channel as its youtube.com/@handle form, but the two mint separate accounts
# (different native_id, no reciprocal edge). Real @-handles never reach this
# length, so it cleanly identifies the url-form duplicate.
_YT_CHANNEL_ID = r"^UC[A-Za-z0-9_-]{20,}$"


def dedup_youtube_channel_accounts(conn, stats: Counter) -> None:
    """Standing sweep: when an artist has both a named YouTube account
    (youtube.com/@name) and a channel-id-only one (youtube.com/channel/UC…)
    for effectively the same channel, keep the named account and retire the
    url-form duplicate — retract its edges, close the membership, hide it.
    An artist whose only YouTube account is a channel-id keeps it (nothing
    named to prefer). Idempotent."""
    with conn.cursor() as cur:
        cur.execute(
            """select uc.id
               from artist_accounts aa
               join accounts uc on uc.id = aa.account_id
               join platforms p on p.id = uc.platform_id and p.slug = 'youtube'
               where aa.removed_at is null and uc.status in ('active', 'unknown')
                 and uc.handle::text ~ %(uc)s
                 -- same artist also has a NAMED youtube member to keep
                 and exists (
                   select 1 from artist_accounts aa2
                   join accounts n on n.id = aa2.account_id
                   join platforms p2 on p2.id = n.platform_id and p2.slug = 'youtube'
                   where aa2.artist_id = aa.artist_id and aa2.removed_at is null
                     and n.status in ('active', 'unknown')
                     and n.handle::text !~ %(uc)s)""",
            {"uc": _YT_CHANNEL_ID})
        dupes = [row[0] for row in cur.fetchall()]
        if not dupes:
            return
        cur.execute(
            """update identity_edges set status = 'retracted'
               where status = 'present'
                 and (source_account_id = any(%(ids)s)
                      or target_account_id = any(%(ids)s))""", {"ids": dupes})
        cur.execute(
            """insert into artist_events (artist_id, event, actor, details)
               select aa.artist_id, 'account_removed', 'pipeline',
                      jsonb_build_object('account_id', aa.account_id,
                                         'reason', 'youtube_channel_id_duplicate')
               from artist_accounts aa
               where aa.removed_at is null and aa.account_id = any(%s)""",
            (dupes,))
        cur.execute(
            """update artist_accounts set removed_at = now()
               where removed_at is null and account_id = any(%s)""", (dupes,))
        cur.execute("update accounts set status = 'hidden' where id = any(%s)",
                    (dupes,))
        stats["youtube_channel_dupes_hidden"] += len(dupes)


def flag_project_accounts(conn, stats: Counter) -> None:
    """Standing sweep: any account whose handle ends in zine or whose
    name/bio reads as a collective project (zine, big bang, anthology, fic
    event) gets `accounts.project = true` — parsed out of clustering, the
    connections table, the paid hydration backlog and singleton review,
    with no human decision needed. Text-based and idempotent. Accounts a
    human attached to an artist are exempt (human decisions are sacred);
    an admin can clear a false positive in SQL, but the sweep re-flags
    while the text still matches — widen the exemption here instead.
    Pending singleton_gate items anchored on a flagged account resolve
    themselves as rejected."""
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, a.handle::text, a.display_name, ls.bio_text
               from accounts a
               left join lateral (select s.bio_text from account_snapshots s
                                  where s.account_id = a.id
                                  order by s.captured_at desc limit 1) ls on true
               where not a.project
                 and not exists (select 1 from artist_accounts aa
                                 where aa.account_id = a.id
                                   and aa.removed_at is null
                                   and aa.added_by = 'human')""")
        flagged = [row[0] for row in cur.fetchall()
                   if project_account(row[1], row[2], row[3])]
        if flagged:
            cur.execute("update accounts set project = true where id = any(%s)",
                        (flagged,))
            stats["project_flagged"] += len(flagged)
        cur.execute(
            """update review_items ri
               set status = 'rejected', resolved_at = now(),
                   decided_by = 'pipeline:project_gate'
               where ri.kind = 'singleton_gate' and ri.status = 'pending'
                 and exists (select 1 from accounts a
                             where a.id = (ri.payload ->> 'account_id')::bigint
                               and a.project)""")
        stats["project_reviews_rejected"] += cur.rowcount
        # An artist whose EVERY member is a project account is a zine that
        # slipped into the directory before the flag existed — demote it
        # (visible on the Demoted page, restorable). Mixed clusters (a real
        # artist plus a wrongly-attached zine account) are left for a human.
        cur.execute(
            """select ar.id from artists ar
               where ar.status = 'active' and ar.merged_into is null
                 and not exists (select 1 from artist_events e
                                 where e.artist_id = ar.id and e.actor like 'admin%%')
                 and exists (select 1 from artist_accounts aa
                             join accounts a on a.id = aa.account_id
                             where aa.artist_id = ar.id and aa.removed_at is null
                               and a.project)
                 and not exists (select 1 from artist_accounts aa
                                 join accounts a on a.id = aa.account_id
                                 where aa.artist_id = ar.id and aa.removed_at is null
                                   and not a.project
                                   -- display-only rows with no text at all
                                   -- (an instagram handle) can't vouch that
                                   -- the cluster is a person
                                   and (coalesce(a.display_name, '') <> ''
                                        or exists (select 1 from account_snapshots s
                                                   where s.account_id = a.id
                                                     and coalesce(s.bio_text, '') <> '')))""")
        for (artist_id,) in cur.fetchall():
            cur.execute("""update artists set status = 'needs_review', updated_at = now()
                           where id = %s""", (artist_id,))
            cur.execute(
                """insert into artist_events (artist_id, event, actor, details)
                   values (%s, 'suppressed', 'pipeline', %s)""",
                (artist_id, json.dumps({"reason": "project_account"})))
            stats["project_artists_demoted"] += 1


def load_state(conn):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select a.id, a.handle::text, a.display_name, a.followers_count,
                      a.discovered_via, a.status, a.project, a.last_hydrated,
                      p.slug as platform_slug,
                      p.kind as platform_kind, ls.bio_text as latest_bio
               from accounts a
               join platforms p on p.id = a.platform_id
               left join lateral (select s.bio_text from account_snapshots s
                                  where s.account_id = a.id
                                  order by s.captured_at desc limit 1) ls on true"""
        )
        accounts = {row["id"]: row for row in cur.fetchall()}
        cur.execute(
            """select id, source_account_id, target_account_id, evidence_type,
                      relation_hint
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
        # has since been retracted (parser fixes, hub re-crawls). Only the
        # LATEST add event per membership counts — a membership re-added later
        # on fresh evidence must not be re-healed forever because of an old
        # event whose edge died (that oscillated remove/re-add every run).
        with conn.cursor() as cur:
            cur.execute(
                """select latest.artist_id, latest.account_id, latest.edge_id
                   from (select distinct on (ev.artist_id, (ev.details ->> 'account_id')::bigint)
                                ev.artist_id,
                                (ev.details ->> 'account_id')::bigint as account_id,
                                (ev.details ->> 'edge_id')::bigint as edge_id
                         from artist_events ev
                         where ev.event = 'account_added'
                         order by ev.artist_id, (ev.details ->> 'account_id')::bigint,
                                  ev.created_at desc) latest
                   join identity_edges e on e.id = latest.edge_id
                   join artist_accounts aa on aa.artist_id = latest.artist_id
                        and aa.account_id = latest.account_id
                        and aa.removed_at is null and aa.added_by = 'clustering'
                   where e.status = 'retracted' or e.claim = 'related'"""
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

        purge_junk_website_accounts(conn, stats)
        dedup_youtube_channel_accounts(conn, stats)
        flag_project_accounts(conn, stats)

        accounts, edges, membership = load_state(conn)

        # Project accounts never cluster: an edge touching a zine is a
        # participant-roster wire, not an identity claim.
        n_edges = len(edges)
        edges = [e for e in edges
                 if not accounts[e["source_account_id"]]["project"]
                 and not accounts[e["target_account_id"]]["project"]]
        stats["project_edges_excluded"] += n_edges - len(edges)

        directed = {(e["source_account_id"], e["target_account_id"]) for e in edges}
        mutual = {frozenset(pair) for pair in directed if (pair[1], pair[0]) in directed}

        # Per-artist platform tallies for the same-platform cap.
        platform_counts: dict[int, Counter] = defaultdict(Counter)
        for account_id, artist_id in membership.items():
            if account_id in accounts:
                platform_counts[artist_id][accounts[account_id]["platform_slug"]] += 1

        def cap_ok(artist_id: int, account_id: int) -> bool:
            slug = accounts[account_id]["platform_slug"]
            return platform_counts[artist_id][slug] < policy.MAX_SAME_PLATFORM

        def note_attach(artist_id: int, account_id: int) -> None:
            platform_counts[artist_id][accounts[account_id]["platform_slug"]] += 1

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
            # Giant-component guard: 4+ accounts on one identity platform in a
            # single "mutual" component means chained project/collective links,
            # not one person — a human decides, nothing auto-merges.
            comp_platforms = Counter(accounts[m]["platform_slug"] for m in members
                                     if m in accounts)
            if comp_platforms and max(comp_platforms.values()) > policy.MAX_SAME_PLATFORM:
                key = f"{min(members)}:{len(members)}"
                if not review_exists(conn, "other", "component_key", key):
                    add_review_item(conn, "other", {
                        "type": "giant_component",
                        "component_key": key,
                        "account_ids": sorted(members),
                        "platform_counts": dict(comp_platforms),
                    }, stats)
                continue
            artist_ids = {membership[m] for m in members if m in membership}
            if len(artist_ids) > 1:
                # Components are built from RECIPROCAL links only, so two
                # existing artists in one component are bidirectionally
                # connected (possibly through a hub) — auto-merge, unless the
                # combined cluster would breach the same-platform cap or more
                # than two artists are involved.
                ids = sorted(artist_ids)
                combined: Counter = Counter()
                for aid in ids:
                    combined += platform_counts[aid]
                for m in members:
                    if m not in membership and m in accounts:
                        combined[accounts[m]["platform_slug"]] += 1
                if (len(ids) == 2 and not merge_rejected(conn, *ids)
                        and (not combined or
                             max(combined.values()) <= policy.MAX_SAME_PLATFORM)):
                    keeper, loser = ids
                    merge_artists(conn, keeper, [loser])
                    resolve_pending_merges(conn, keeper, loser,
                                           "pipeline:reciprocal_component")
                    for acc, aid in list(membership.items()):
                        if aid == loser:
                            membership[acc] = keeper
                    platform_counts[keeper] = combined
                    stats["auto_merged"] += 1
                    artist_id = keeper
                else:
                    if not review_exists(conn, "cluster_merge", "artist_ids",
                                         json.dumps(ids)):
                        add_review_item(conn, "cluster_merge", {
                            "artist_ids": json.dumps(ids),
                            "account_ids": sorted(members),
                        }, stats)
                    continue
            elif artist_ids:
                artist_id = artist_ids.pop()
            else:
                anchor = max((accounts[m] for m in members),
                             key=lambda a: a["followers_count"] or 0)
                if db.is_suppressed(conn, anchor["id"]):
                    continue
                # Follow-on-only components (no roster source vouched for any
                # member) need artist evidence before becoming an artist:
                # zines/big bangs/anthologies publish reciprocal twitter↔carrd
                # links exactly like a person and used to sail through here.
                # Project-flavored or evidence-free components go to
                # singleton_gate review (approve creates the anchor's artist;
                # the rest of the component attaches next run).
                component_text = "\n".join(
                    "\n".join(filter(None, [accounts[m]["latest_bio"],
                                            accounts[m]["display_name"],
                                            accounts[m]["handle"]]))
                    for m in members if m in accounts)
                follow_on_only = not any(
                    accounts[m]["discovered_via"] in policy.ROSTER_SOURCES
                    or accounts[m]["discovered_via"] == "manual"
                    for m in members if m in accounts)
                if follow_on_only and (looks_like_project(component_text)
                                       or not looks_like_artist(component_text)):
                    if not review_exists(conn, "singleton_gate", "account_id",
                                         anchor["id"]):
                        add_review_item(conn, "singleton_gate", {
                            "account_id": anchor["id"],
                            "handle": anchor["handle"],
                            "platform": anchor["platform_slug"],
                            "followers": anchor["followers_count"],
                            "discovered_via": anchor["discovered_via"],
                            "component_account_ids": sorted(members),
                            "reason": ("project_like"
                                       if looks_like_project(component_text)
                                       else "no_artist_evidence"),
                        }, stats)
                    stats["components_gated"] += 1
                    continue
                artist_id = create_artist(conn, anchor)
                membership[anchor["id"]] = artist_id
                stats["artists_created"] += 1
            for m in members:
                if (m not in membership and not db.is_suppressed(conn, m)
                        and add_member(conn, artist_id, m, "near_proof",
                                       {"via": "reciprocal"})):
                    membership[m] = artist_id
                    note_attach(artist_id, m)
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
            if edge["evidence_type"] != "profile_field" or edge["relation_hint"] != "oauth":
                continue
            src, tgt = edge["source_account_id"], edge["target_account_id"]
            if src in membership or tgt not in membership:
                continue
            if db.is_suppressed(conn, src) or not cap_ok(membership[tgt], src):
                continue
            add_member(conn, membership[tgt], src, "near_proof",
                       {"via": "platform_verified_link", "edge_id": edge["id"]})
            membership[src] = membership[tgt]
            note_attach(membership[src], src)
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
                    # x-tag queue: never mint a skeleton artist for a tag the
                    # paid flush hasn't hydrated yet — blank directory rows,
                    # and the extension's amber "queued" must stay queued.
                    and not (account["discovered_via"] == "manual_tag"
                             and account["last_hydrated"] is None)
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

        # Reciprocity rescue support: what each account links out to (strong
        # same-person edges), and the accounts of each artist. A prominent
        # one-directional target that links back to >=2 of the source artist's
        # own distinctive downstream targets is the same person, not an
        # impersonator — see policy.RECIPROCITY_SHARED_MIN.
        out_targets: dict[int, set[int]] = defaultdict(set)
        for e in edges:
            out_targets[e["source_account_id"]].add(e["target_account_id"])
        members_by_artist: dict[int, set[int]] = defaultdict(set)
        for account_id, artist_id in membership.items():
            members_by_artist[artist_id].add(account_id)

        def shared_reciprocity(src_artist: int, tgt: int) -> int:
            """Count distinctive downstream targets shared between tgt and the
            src artist. Excludes community shared-targets and prominent accounts
            (both are things unrelated artists coincidentally co-link)."""
            src_links: set[int] = set()
            for m in members_by_artist[src_artist]:
                src_links |= out_targets.get(m, set())
            shared = out_targets.get(tgt, set()) & src_links
            return sum(
                1 for s in shared
                if s not in shared_targets
                and (accounts.get(s, {}).get("followers_count") or 0)
                    < policy.REVIEW_FOLLOWER_THRESHOLD
            )

        # Edges the guards flipped to `related` are still directed same-person
        # claims — demoted only because a condition (missing reciprocity, a
        # second same-platform account, a full cap) held at the time. Loaded
        # once: step 4 counts them as artist-level back-links, step 4b
        # re-checks each against its own original condition.
        from psycopg.rows import dict_row

        with conn.cursor(row_factory=dict_row) as fcur:
            fcur.execute(
                """select id, source_account_id, target_account_id, relation_hint
                   from identity_edges
                   where status = 'present' and claim = 'related'
                     and relation_hint in ('unreciprocated_prominent',
                                           'secondary_link', 'over_platform_cap')"""
            )
            reflips = fcur.fetchall()
        # Same exclusion as the union-find edge set: zines don't get rescued.
        reflips = [e for e in reflips
                   if not accounts[e["source_account_id"]]["project"]
                   and not accounts[e["target_account_id"]]["project"]]
        flipped_out: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for e in reflips:
            flipped_out[e["source_account_id"]].append(
                (e["target_account_id"], e["id"]))

        def artist_directed_claims(from_artist: int, to_artist: int):
            """(has_live_same_person_edge, flipped_edge_ids) for directed
            claims from any account of from_artist to any of to_artist."""
            same, flipped = False, []
            for m in members_by_artist.get(from_artist, ()):
                for t in out_targets.get(m, ()):
                    if membership.get(t) == to_artist:
                        same = True
                for t, eid in flipped_out.get(m, ()):
                    if membership.get(t) == to_artist:
                        flipped.append(eid)
            return same, flipped

        def artist_suppressed(artist_id: int) -> bool:
            with conn.cursor() as scur:
                scur.execute("""select 1 from suppressions
                                where lifted_at is null and artist_id = %s limit 1""",
                             (artist_id,))
                return scur.fetchone() is not None

        def try_reciprocal_artist_merge(a: int, b: int) -> bool:
            """Two existing artists whose clusters reference each other — in
            either direction, through any member account, counting flipped
            prominent claims — are cyclically linked: the same near-proof as a
            mutual account pair. Merge them, restore the flipped edges, and
            answer any pending human question for the pair."""
            if merge_rejected(conn, a, b):
                return False
            fwd_same, fwd_flipped = artist_directed_claims(a, b)
            back_same, back_flipped = artist_directed_claims(b, a)
            if not (fwd_same or fwd_flipped) or not (back_same or back_flipped):
                return False
            combined = platform_counts[a] + platform_counts[b]
            if combined and max(combined.values()) > policy.MAX_SAME_PLATFORM:
                return False
            if artist_suppressed(a) or artist_suppressed(b):
                return False
            keeper, loser = sorted((a, b))
            merge_artists(conn, keeper, [loser])
            for acc, aid in list(membership.items()):
                if aid == loser:
                    membership[acc] = keeper
            members_by_artist[keeper] |= members_by_artist.pop(loser, set())
            platform_counts[keeper] = combined
            with conn.cursor() as ecur:
                for eid in fwd_flipped + back_flipped:
                    ecur.execute(
                        """update identity_edges set claim = 'same_person',
                               relation_hint = 'artist_reciprocity'
                           where id = %s and claim = 'related'""", (eid,))
            resolve_pending_merges(conn, keeper, loser, "pipeline:artist_reciprocity")
            stats["artist_reciprocity_merged"] += 1
            return True

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
            src_artist = membership[src]
            tgt_artist = membership.get(tgt)
            if tgt_artist == src_artist:
                continue
            # Popularity makes an account a shared TARGET of many bios, but an
            # OAuth-verified claim overrides that — the platform says this
            # specific one is theirs. User-entered profile fields get no such
            # exemption (shared defaults like DLsite's youtube id are exactly
            # what the guard exists to catch).
            if tgt in shared_targets and edge["relation_hint"] != "oauth":
                stats["skipped_shared_target"] += 1
                continue
            if tgt_artist is not None:
                # Cyclical cross-artist references (this edge points
                # src_artist -> tgt_artist; anything points back) auto-merge.
                if try_reciprocal_artist_merge(src_artist, tgt_artist):
                    continue
                # Dedupe by artist PAIR — the same two artists conflicting via
                # several edges must ask the human exactly once.
                pair = json.dumps(sorted({src_artist, tgt_artist}))
                if not review_exists(conn, "cluster_merge", "artist_ids", pair):
                    add_review_item(conn, "cluster_merge", {
                        "edge_id": edge["id"],
                        "artist_ids": pair,
                        "account_ids": [src, tgt],
                    }, stats)
                continue
            if db.is_suppressed(conn, tgt):
                continue
            followers = accounts[tgt]["followers_count"] or 0
            # One-directional resolution — never a review item. Best effort:
            # strong-enough evidence attaches; anything doubtful becomes a
            # related connection (visible on the artist page), which
            # re-extraction upgrades back to same_person if the link later
            # reciprocates — at which point the mutual path auto-merges.
            if edge["evidence_type"] == "profile_field" and edge["relation_hint"] == "oauth":
                # OAuth-verified: the platform vouches; prominence irrelevant.
                if cap_ok(src_artist, tgt) and add_member(
                        conn, src_artist, tgt, "near_proof",
                        {"via": "platform_verified_link", "edge_id": edge["id"]}):
                    membership[tgt] = src_artist
                    note_attach(src_artist, tgt)
                    stats["members_added"] += 1
                continue
            if edge["evidence_type"] == "bio_mention":
                # Confidently-regexed alt claims (サブ垢▶@x etc.) auto-attach;
                # alts are legitimately a second same-platform account, so
                # only the hard cap applies.
                if cap_ok(src_artist, tgt) and add_member(
                        conn, src_artist, tgt, "strong",
                        {"via": "alt_mention", "edge_id": edge["id"]}):
                    membership[tgt] = src_artist
                    note_attach(src_artist, tgt)
                    stats["alt_mentions_attached"] += 1
                else:
                    flip_to_connection(conn, edge["id"], "over_platform_cap")
                    stats["flipped_over_cap"] += 1
                continue
            second_same_platform = \
                platform_counts[src_artist][accounts[tgt]["platform_slug"]] >= 1
            if followers >= policy.REVIEW_FOLLOWER_THRESHOLD:
                # Prominent target: normally an impersonation risk → flip. But if
                # it links back to >=2 of this artist's own distinctive hubs, the
                # reciprocity proves same-person; attach instead (cap-guarded).
                if (shared_reciprocity(src_artist, tgt) >= policy.RECIPROCITY_SHARED_MIN
                        and not second_same_platform and cap_ok(src_artist, tgt)
                        and add_member(conn, src_artist, tgt, "strong",
                                       {"via": "shared_hub_reciprocity",
                                        "edge_id": edge["id"]})):
                    membership[tgt] = src_artist
                    note_attach(src_artist, tgt)
                    stats["reciprocity_merged"] += 1
                else:
                    flip_to_connection(conn, edge["id"], "unreciprocated_prominent")
                    stats["flipped_prominent"] += 1
            elif second_same_platform or not cap_ok(src_artist, tgt):
                flip_to_connection(conn, edge["id"], "secondary_link")
                stats["flipped_secondary"] += 1
            elif add_member(conn, src_artist, tgt, "strong",
                            {"via": "one_directional", "edge_id": edge["id"]}):
                membership[tgt] = src_artist
                note_attach(src_artist, tgt)
                stats["members_added"] += 1

        # 4b. Re-evaluate previously flipped connections. Those live as
        # `related` (so they're absent from load_state's same_person set) and
        # would otherwise stay stuck forever. Each hint self-heals against its
        # own original condition: prominence needs shared-hub reciprocity to
        # appear (a hub gets crawled, the target's own links get extracted);
        # secondary_link / over_platform_cap clear when a detach frees the
        # slot. A flipped edge whose endpoints now sit in two different artists
        # is the cyclical-reference case again (both directions may have been
        # flipped) — try the artist-level merge first.
        def restore_and_attach(edge, src_artist, tgt, confidence, via,
                               restored_hint, stat_key) -> None:
            if not add_member(conn, src_artist, tgt, confidence,
                              {"via": via, "edge_id": edge["id"]}):
                return  # human closed this membership — leave the flip alone
            with conn.cursor() as ecur:
                ecur.execute(
                    """update identity_edges set claim = 'same_person',
                           relation_hint = %s
                       where id = %s""", (restored_hint, edge["id"]))
            membership[tgt] = src_artist
            note_attach(src_artist, tgt)
            stats[stat_key] += 1

        for edge in reflips:
            src, tgt = edge["source_account_id"], edge["target_account_id"]
            src_artist = membership.get(src)
            tgt_artist = membership.get(tgt)
            if (src_artist is not None and tgt_artist is not None
                    and tgt_artist != src_artist):
                try_reciprocal_artist_merge(src_artist, tgt_artist)
                continue
            if (src_artist is None or tgt not in accounts
                    or tgt_artist is not None or tgt in shared_targets):
                continue
            if edge["relation_hint"] == "over_platform_cap":
                # A bio_mention alt claim: only the hard cap blocked it.
                if cap_ok(src_artist, tgt):
                    restore_and_attach(edge, src_artist, tgt, "strong",
                                       "alt_mention", None, "alt_mentions_attached")
                continue
            if platform_counts[src_artist][accounts[tgt]["platform_slug"]] >= 1:
                continue  # would be a second same-platform account — stay cautious
            if not cap_ok(src_artist, tgt):
                continue
            if (accounts[tgt]["followers_count"] or 0) >= policy.REVIEW_FOLLOWER_THRESHOLD:
                # Prominent target (whatever hint got it flipped): still needs
                # the shared-hub reciprocity proof against impersonation.
                if shared_reciprocity(src_artist, tgt) >= policy.RECIPROCITY_SHARED_MIN:
                    restore_and_attach(edge, src_artist, tgt, "strong",
                                       "shared_hub_reciprocity",
                                       "shared_hub_reciprocity",
                                       "reciprocity_merged")
            else:
                # The original blocking condition has cleared (freed cap slot,
                # or a prominence flag the latest follower count no longer
                # supports) and the target is not prominent — the plain
                # one-directional attach that would have happened originally
                # applies now.
                restore_and_attach(edge, src_artist, tgt, "strong",
                                   "one_directional", None, "members_added")

        # 5. Anomaly flags: artists whose link graph looks like a credits
        # dump — surfaced for manual review, no automatic action.

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """select ar.id as artist_id, ar.public_slug,
                       coalesce((select max(cnt) from (
                           select count(*) cnt from identity_edges e
                           join artist_accounts ha on ha.account_id = e.source_account_id
                                and ha.removed_at is null and ha.artist_id = ar.id
                           join accounts sa on sa.id = e.source_account_id
                           join platforms sp on sp.id = sa.platform_id
                           where sp.kind = 'link_hub' and e.status = 'present'
                           group by e.source_account_id) f), 0) as hub_fanout,
                       (select count(*) from artist_accounts aa
                        join accounts a on a.id = aa.account_id
                        where aa.artist_id = ar.id and aa.removed_at is null
                          and a.discovered_via = 'link_hub') as hub_attached,
                       (select count(distinct e2.id) from identity_edges e2
                        where e2.claim = 'related' and e2.status = 'present'
                          and exists (select 1 from artist_accounts aa2
                                      where aa2.removed_at is null and aa2.artist_id = ar.id
                                        and aa2.account_id in (e2.source_account_id,
                                                               e2.target_account_id))
                       ) as related_count
                   from artists ar
                   where ar.status = 'active' and ar.merged_into is null"""
            )
            currently_flagged: set[int] = set()
            for row in cur.fetchall():
                reasons = {}
                if row["hub_fanout"] >= policy.ANOMALY_HUB_FANOUT:
                    reasons["hub_fanout"] = row["hub_fanout"]
                if row["hub_attached"] >= policy.ANOMALY_HUB_ATTACHED:
                    reasons["hub_attached"] = row["hub_attached"]
                if row["related_count"] >= policy.ANOMALY_RELATED_CONNECTIONS:
                    reasons["related_connections"] = row["related_count"]
                if reasons:
                    currently_flagged.add(row["artist_id"])
                    if not review_exists(conn, "other", "artist_id",
                                         row["artist_id"]):
                        add_review_item(conn, "other", {
                            "type": "anomaly",
                            "artist_id": row["artist_id"],
                            "public_slug": row["public_slug"],
                            "reasons": reasons,
                        }, stats)

        # 5b. Cross-artist reference anomaly: a MEMBER account whose edges
        # touch several OTHER artists. Two shapes hide here and only a human
        # can tell them apart: a junk shared target that slipped into a
        # cluster before the guards existed (tumblr.com/contact), or one
        # person's unmerged alt-artists all pointing at each other.
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """select aa.artist_id, ar.public_slug, aa.account_id,
                          p.slug || ':' || a.handle as account_label,
                          count(distinct oaa.artist_id) as other_artists,
                          array_agg(distinct oar.public_slug) as referencing_slugs
                   from artist_accounts aa
                   join artists ar on ar.id = aa.artist_id
                        and ar.status = 'active' and ar.merged_into is null
                   join accounts a on a.id = aa.account_id
                   join platforms p on p.id = a.platform_id
                   join identity_edges e on e.status = 'present'
                        and aa.account_id in (e.source_account_id, e.target_account_id)
                   join artist_accounts oaa on oaa.removed_at is null
                        and oaa.account_id = case when e.source_account_id = aa.account_id
                                                  then e.target_account_id
                                                  else e.source_account_id end
                        and oaa.artist_id <> aa.artist_id
                   join artists oar on oar.id = oaa.artist_id
                   where aa.removed_at is null
                   group by 1, 2, 3, 4
                   having count(distinct oaa.artist_id) >= %s""",
                (policy.ANOMALY_CROSS_ARTIST_REFS,),
            )
            current_xref_accounts: set[int] = set()
            for row in cur.fetchall():
                current_xref_accounts.add(row["account_id"])
                if review_exists(conn, "other", "account_id", row["account_id"]):
                    continue
                add_review_item(conn, "other", {
                    "type": "anomaly",
                    "artist_id": row["artist_id"],
                    "public_slug": row["public_slug"],
                    "account_id": row["account_id"],
                    "reasons": {"cross_artist_refs": row["other_artists"],
                                "account": row["account_label"],
                                "referenced_by": ", ".join(row["referencing_slugs"])},
                }, stats)

        # 5c. Anomalies heal: a pending flag whose artist/account no longer
        # trips ANY current threshold is stale — cleanups (edge retractions,
        # junk purges, threshold raises) fix the underlying shape, and the
        # queue must not keep asking a human about it. Auto-resolve, never
        # touching human-decided items.
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""select id, payload from review_items
                           where status = 'pending' and kind = 'other'""")
            stale = []
            for r in cur.fetchall():
                payload = r["payload"] or {}
                if payload.get("type") != "anomaly":
                    continue  # giant components etc. have no recheck rule
                if payload.get("account_id") is not None:
                    if payload["account_id"] not in current_xref_accounts:
                        stale.append(r["id"])
                elif payload.get("artist_id") is not None:
                    if payload["artist_id"] not in currently_flagged:
                        stale.append(r["id"])
            if stale:
                cur.execute(
                    """update review_items
                       set status = 'rejected', resolved_at = now(),
                           decided_by = 'pipeline:anomaly_cleared'
                       where id = any(%s) and status = 'pending'""", (stale,))
                stats["anomalies_cleared"] += len(stale)

        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
