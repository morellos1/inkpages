"""Dead-link checker: verify that account profile pages still exist.

Fetches profile URLs on platforms we're allowed to touch (never Twitter,
Instagram, or Bluesky — those have their own policies/APIs; Discord and
Telegram links can't be validated by status code). 404/410 marks the account
deleted, which drops it from the publish view; blocks and throttles are
inconclusive and left alone. Prioritizes accounts that appear in the
directory, least-recently-checked first.

Usage: uv run python -m inkpages.check_links [--limit 300]
"""
import argparse
import time
from collections import Counter

import httpx
from psycopg.rows import dict_row

from . import db
from .crawl_links import UA, fetch_page

NEVER_CHECK = ("twitter", "instagram", "bluesky", "discord", "telegram")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--recheck-days", type=int, default=30)
    args = parser.parse_args()

    stats: Counter = Counter()
    with httpx.Client(headers=UA) as client, db.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """select a.id, a.profile_url, p.slug as platform,
                          exists (select 1 from artist_accounts aa
                                  join artists ar on ar.id = aa.artist_id
                                  where aa.account_id = a.id and aa.removed_at is null
                                    and ar.status = 'active') as listed
                   from accounts a join platforms p on p.id = a.platform_id
                   where p.slug <> all(%s) and a.profile_url is not null
                     and a.status <> 'deleted'
                     and (a.link_checked_at is null
                          or a.link_checked_at < now() - make_interval(days => %s))
                   order by listed desc, a.link_checked_at asc nulls first, a.id
                   limit %s""",
                (list(NEVER_CHECK), args.recheck_days, args.limit),
            )
            targets = cur.fetchall()

        for row in targets:
            time.sleep(0.4)
            resp = fetch_page(client, row["profile_url"])
            with conn.cursor() as cur:
                if resp is None:
                    stats["fetch_failed"] += 1
                    cur.execute("update accounts set link_checked_at = now() where id = %s",
                                (row["id"],))
                    continue
                if resp.status_code in (404, 410):
                    cur.execute(
                        """update accounts set status = 'deleted', link_checked_at = now()
                           where id = %s""", (row["id"],))
                    stats["dead"] += 1
                elif resp.status_code == 200:
                    # A live page revives unknown/deleted rows but never lifts
                    # the admin-side 'hidden' verification cull.
                    cur.execute(
                        """update accounts
                           set status = case when status = 'hidden'
                                             then 'hidden' else 'active' end,
                               link_checked_at = now()
                           where id = %s""", (row["id"],))
                    stats["alive"] += 1
                else:
                    # Blocked/throttled/etc — inconclusive, note the check.
                    cur.execute("update accounts set link_checked_at = now() where id = %s",
                                (row["id"],))
                    stats[f"inconclusive_{resp.status_code}"] += 1
            conn.commit()

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
