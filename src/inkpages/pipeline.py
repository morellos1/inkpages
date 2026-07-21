"""Run the free post-discovery stages in order:
crawl_links -> check_links -> cluster.

Every discovery or hydration run must be followed by these — new bios mint
new hub accounts whose inner links only exist after a crawl, dead profiles
should drop out, and new edges only become artists after clustering.

Usage: uv run python -m inkpages.pipeline
"""
import sys

from . import check_links, cluster, crawl_links


def main() -> None:
    sys.argv = ["crawl_links"]
    crawl_links.main()
    sys.argv = ["check_links", "--limit", "200"]
    check_links.main()
    sys.argv = ["cluster"]
    cluster.main()


if __name__ == "__main__":
    main()
