"""Hydrate known-but-unhydrated Twitter handles (bio-link and hub targets)
via users/by (~$0.01/user, 100 handles per request), with the budget guard.

Usage: uv run python -m inkpages.hydrate_twitter --limit 200
"""
import argparse
from collections import Counter, defaultdict

from . import db, policy
from .extract import looks_like_artist, looks_like_project
from .twitter import USER_READ_CENTS, XApi, ensure_budget, process_user

# Referrer texts and vouching facts for the handle-only backlog, one row per
# (target, referrer). Hub referrers (carrd/linktree/…) carry no self-
# description, so the hub's own referrers are pulled in as a second hop —
# the artist behind the hub is the voucher, not the hub page.
_BACKLOG_SQL = """
with targets as (
  select a.id, a.handle::text as handle, a.discovered_via
  from accounts a
  where a.platform_id = %(tw)s and a.native_id is null and a.last_hydrated is null
    and a.status = 'unknown' and a.discovered_via <> 'bio_mention'
    and not a.project
),
direct as (
  select t.id as target_id, e.source_account_id as ref_id
  from targets t
  join identity_edges e on e.target_account_id = t.id and e.status = 'present'
),
hub_up as (
  select d.target_id, e2.source_account_id as ref_id
  from direct d
  join accounts h on h.id = d.ref_id
  join platforms hp on hp.id = h.platform_id and hp.kind = 'link_hub'
  join identity_edges e2 on e2.target_account_id = h.id and e2.status = 'present'
),
refs as (select * from direct union select * from hub_up)
select t.id, t.handle, t.discovered_via, r.ref_id, ra.project as ref_project,
       ra.discovered_via as ref_via,
       exists (select 1 from artist_accounts aa
               join artists ar on ar.id = aa.artist_id
               where aa.account_id = r.ref_id and aa.removed_at is null
                 and ar.merged_into is null and ar.status = 'active') as ref_is_member,
       concat_ws(' ', ra.handle::text, ra.display_name, s.bio_text) as ref_text
from targets t
left join refs r on r.target_id = t.id
left join accounts ra on ra.id = r.ref_id
left join lateral (
    select bio_text from account_snapshots
    where account_id = r.ref_id order by captured_at desc limit 1) s on true
order by t.id
"""


def gated_handle_backlog(conn, twitter_platform_id: int,
                         limit: int | None = None) -> tuple[list[str], int]:
    """The handle-only hydration backlog, minus zine/big-bang/writers-circle
    chains. Each paid read must be vouched for by at least one referrer that
    reads like an artist (or IS one): zines and fic events link participant
    rosters exactly like artists link their own alts, and hydrating those
    participants mints the next ring of hub crawls — the measured
    frontier-collapse loop. A target passes when its own handle carries art
    hints, or some referrer is a listed artist's account / roster-discovered /
    art-flavored in its own words — and that referrer isn't itself
    project-flavored. Skipped targets stay in `unknown` costing nothing; a
    later artful referrer (or a manual add) lifts them naturally."""
    with conn.cursor() as cur:
        cur.execute(_BACKLOG_SQL, {"tw": twitter_platform_id})
        rows = cur.fetchall()
    by_target: dict[int, dict] = {}
    refs: defaultdict[int, list] = defaultdict(list)
    for target_id, handle, via, ref_id, ref_project, ref_via, ref_is_member, ref_text in rows:
        by_target[target_id] = {"handle": handle, "via": via}
        if ref_id is not None and not ref_project:
            refs[target_id].append((ref_via, ref_is_member, ref_text))

    passing: list[str] = []
    skipped = 0
    for target_id, target in by_target.items():
        # Roster-discovered targets vouch for themselves: a human tag or a
        # curated roster IS the artist evidence (they arrive with no edges,
        # so the referrer gate below would otherwise starve them forever).
        vouched = (target["via"] in policy.ROSTER_SOURCES
                   or looks_like_artist(target["handle"]))
        for ref_via, ref_is_member, ref_text in refs[target_id]:
            if vouched:
                break
            if looks_like_project(ref_text):
                continue
            if (ref_is_member or ref_via in policy.ROSTER_SOURCES
                    or looks_like_artist(ref_text)):
                vouched = True
        if vouched:
            passing.append(target["handle"])
        else:
            skipped += 1
    return (passing[:limit] if limit is not None else passing), skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--refresh", action="store_true",
                        help="re-hydrate already-hydrated accounts by stable id "
                             "(rename-proof); used for field backfills and the "
                             "quarterly refresh")
    args = parser.parse_args()

    stats: Counter = Counter()
    with db.connect() as conn:
        api = XApi(conn)  # bills each batch into api_usage as it is fetched
        platforms = db.platform_ids(conn)
        if args.refresh:
            with conn.cursor() as cur:
                # 'hidden' (verification-culled) and 'deleted' accounts are
                # excluded: a refresh must never spend on them or resurrect
                # the cull. (A returning suspended account needs an explicit
                # admin status lift first.)
                cur.execute(
                    """select native_id from accounts
                       where platform_id = %s and native_id is not null
                         and status not in ('hidden', 'deleted')
                       order by last_hydrated asc nulls first limit %s""",
                    (platforms["twitter"], args.limit),
                )
                ids = [r[0] for r in cur.fetchall()]
            if not ids:
                print("nothing to refresh")
                return
            ensure_budget(conn, len(ids) * USER_READ_CENTS)
            found, missing = api.users_by_ids(ids, note="refresh")
            for user in found:
                process_user(conn, platforms, user, "hydration", {}, stats)
            with conn.cursor() as cur:
                for native_id in missing:
                    cur.execute(
                        """update accounts set status = 'deleted', last_hydrated = now()
                           where platform_id = %s and native_id = %s""",
                        (platforms["twitter"], native_id),
                    )
            conn.commit()
            stats["missing"] = len(missing)
            print("done:", dict(stats))
            return
        with conn.cursor() as cur:
            # Never-hydrated accounts that already have a native id (e.g.
            # Skeb's OAuth-verified twitter_uid) — fetched by stable id.
            cur.execute(
                """select native_id from accounts
                   where platform_id = %s and native_id is not null and last_hydrated is null
                   order by id limit %s""",
                (platforms["twitter"], args.limit),
            )
            ids = [r[0] for r in cur.fetchall()]
        # bio_mention targets are deliberately excluded: mentions are
        # mostly friends/credits, not worth a paid read until an edge or
        # human says otherwise. The rest go through the referrer gate —
        # zine/fic-event chains never earn a paid read.
        handles, gated = gated_handle_backlog(
            conn, platforms["twitter"], max(args.limit - len(ids), 0))
        if gated:
            print(f"gated {gated} backlog handles (no artist-flavored referrer)")
        if not ids and not handles:
            print("nothing to hydrate")
            return

        ensure_budget(conn, (len(ids) + len(handles)) * USER_READ_CENTS)
        found, missing_ids = (api.users_by_ids(ids) if ids else ([], []))
        found2, missing_handles = (api.users_by(handles) if handles else ([], []))

        for user in found + found2:
            process_user(conn, platforms, user, "hydration", {}, stats)
        with conn.cursor() as cur:
            for native_id in missing_ids:
                cur.execute(
                    """update accounts set status = 'deleted', last_hydrated = now()
                       where platform_id = %s and native_id = %s""",
                    (platforms["twitter"], native_id),
                )
            for handle in missing_handles:
                cur.execute(
                    """update accounts set status = 'deleted', last_hydrated = now()
                       where platform_id = %s and handle = %s and native_id is null""",
                    (platforms["twitter"], handle),
                )
        conn.commit()
        stats["missing"] = len(missing_ids) + len(missing_handles)

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
