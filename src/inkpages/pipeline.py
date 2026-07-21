"""Run the free post-discovery stages in order:
hydrate-known (skeb, pixiv) -> crawl_links -> check_links -> cluster
-> classify_region.

Every discovery or hydration run must be followed by these — newly referenced
accounts on free platforms get hydrated (their bios mint edges and hub
accounts), new hubs get crawled, dead profiles drop out, and new edges only
become artists after clustering.

Twitter hydration is paid and never runs from here — the tail of the run
prints how many twitter accounts await hydration and the estimated cost, so
a human can approve `uv run python -m inkpages.hydrate_twitter`.

Usage: uv run python -m inkpages.pipeline [--skip-hydrate]
"""
import argparse
import sys

from . import check_links, classify_region, cluster, crawl_links, db
from .twitter import USER_READ_CENTS


def hydrate_free_platforms() -> None:
    """Fetch referenced-but-never-fetched skeb and pixiv accounts (both free).
    Skeb first (its OAuth twitter_uid is the highest-value signal and it can
    mint pixiv references), then pixiv; anything either pass mints on the
    other platform is picked up by the next pipeline run."""
    from . import discover_pixiv, discover_skeb

    sys.argv = ["discover_skeb", "--hydrate-known", "--top", "0"]
    discover_skeb.main()
    sys.argv = ["discover_pixiv", "--hydrate-known", "--rank-pages", "0"]
    discover_pixiv.main()


def report_twitter_backlog() -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """select count(*) from accounts a
               join platforms p on p.id = a.platform_id
               where p.slug = 'twitter' and a.last_hydrated is null
                 and a.status <> 'deleted' and a.discovered_via <> 'bio_mention'""")
        n = cur.fetchone()[0]
    if n:
        print(f"twitter backlog: {n} accounts await paid hydration "
              f"(~{n * USER_READ_CENTS / 100:.2f}$) — run inkpages.hydrate_twitter "
              f"after approving the spend")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-hydrate", action="store_true",
                        help="skip the free skeb/pixiv hydrate-known passes")
    args = parser.parse_args()

    if not args.skip_hydrate:
        hydrate_free_platforms()
    sys.argv = ["crawl_links"]
    crawl_links.main()
    sys.argv = ["check_links", "--limit", "200"]
    check_links.main()
    sys.argv = ["cluster"]
    cluster.main()
    sys.argv = ["classify_region"]
    classify_region.main()
    report_twitter_backlog()


if __name__ == "__main__":
    main()
