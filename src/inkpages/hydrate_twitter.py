"""Hydrate known-but-unhydrated Twitter handles (bio-link and hub targets)
via users/by (~$0.01/user, 100 handles per request), with the budget guard.

Usage: uv run python -m inkpages.hydrate_twitter --limit 200
"""
import argparse
import math
from collections import Counter

from . import db
from .twitter import USER_READ_CENTS, XApi, ensure_budget, process_user


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--refresh", action="store_true",
                        help="re-hydrate already-hydrated accounts by stable id "
                             "(rename-proof); used for field backfills and the "
                             "quarterly refresh")
    args = parser.parse_args()

    stats: Counter = Counter()
    api = XApi()
    with db.connect() as conn:
        platforms = db.platform_ids(conn)
        if args.refresh:
            with conn.cursor() as cur:
                cur.execute(
                    """select native_id from accounts
                       where platform_id = %s and native_id is not null
                       order by last_hydrated asc nulls first limit %s""",
                    (platforms["twitter"], args.limit),
                )
                ids = [r[0] for r in cur.fetchall()]
            if not ids:
                print("nothing to refresh")
                return
            ensure_budget(conn, len(ids) * USER_READ_CENTS)
            found, missing = api.users_by_ids(ids)
            db.log_api_usage(conn, "x_api", "users(ids)", len(ids),
                             math.ceil(len(ids) * USER_READ_CENTS), note="refresh")
            conn.commit()
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
            # human says otherwise.
            cur.execute(
                """select handle::text from accounts
                   where platform_id = %s and native_id is null and last_hydrated is null
                     and status = 'unknown' and discovered_via <> 'bio_mention'
                   order by id limit %s""",
                (platforms["twitter"], max(args.limit - len(ids), 0)),
            )
            handles = [r[0] for r in cur.fetchall()]
        if not ids and not handles:
            print("nothing to hydrate")
            return

        ensure_budget(conn, (len(ids) + len(handles)) * USER_READ_CENTS)
        found, missing_ids = (api.users_by_ids(ids) if ids else ([], []))
        found2, missing_handles = (api.users_by(handles) if handles else ([], []))
        db.log_api_usage(conn, "x_api", "users_by+ids", len(ids) + len(handles),
                         math.ceil((len(ids) + len(handles)) * USER_READ_CENTS))
        conn.commit()

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
