"""Run the free post-discovery stages in order: crawl_links -> cluster.

Every discovery or hydration run must be followed by these — new bios mint
new hub accounts whose inner links only exist after a crawl, and new edges
only become artists after clustering.

Usage: uv run python -m inkpages.pipeline
"""
import sys

from . import cluster, crawl_links


def main() -> None:
    sys.argv = ["crawl_links"]
    crawl_links.main()
    sys.argv = ["cluster"]
    cluster.main()


if __name__ == "__main__":
    main()
