# inkpages

A public directory of digital artists who self-attest that they don't use
generative AI, linking each artist's accounts across platforms (Twitter/X,
Bluesky, Skeb, Pixiv, ArtStation, Patreon, VGen, Cara, Ko-fi, …).

Built as an **entity-resolution + labeling** system, not a scraper: artists
publish their own cross-platform links (bios, Linktree/Carrd, potofu.me);
we resolve those self-published claims into identity clusters with full
provenance.

## Principles

1. Identity claims are edges with provenance, never merged truth.
2. "No AI" is the artist's self-attestation, never our classification, and we
   never publish an AI-use accusation as fact.
3. Third-party assertions (boorus etc.) are discovery hints only — quarantined
   from published lineage, re-verified against the artist's own bios.
4. Twitter only via the paid official API; Instagram display-only, never
   fetched; no gray-market scrapers, ever.
5. Default-list + opt-out: suppression records guarantee an opted-out artist
   is never re-added by re-discovery.

## Layout

- [migrations/](migrations/) — plain-SQL schema (Postgres 16)
- [src/inkpages/](src/inkpages/) — pipeline workers (Bluesky discovery so far)
- [docs/schema.md](docs/schema.md) — ERD + design-tradeoff walkthrough
- [docs/pipeline.md](docs/pipeline.md) — the pipeline plan and budget
- [scripts/migrate.py](scripts/migrate.py) — minimal migration runner
- [scripts/smoke.sql](scripts/smoke.sql) — fixture smoke test (self-rolls-back)

## Quickstart

```sh
docker compose up -d          # Postgres 16 on localhost:5433
cp .env.example .env
uv run scripts/migrate.py     # apply migrations
docker compose exec -T db psql -U inkpages -d inkpages < scripts/smoke.sql
```

The smoke test inserts fixtures (a badged artist, an opted-out artist, a
quarantined booru hint), asserts the publish rules — including, via
`pg_depend`, that the `directory_entries` view has no dependency on
`discovery_hints` — prints the resulting directory entry, and rolls back.

### Bluesky discovery

```sh
# bootstrap from popular art feeds, or point at curated sources:
uv run python -m inkpages.discover_bluesky --bootstrap-query art --bootstrap-top 2
uv run python -m inkpages.discover_bluesky --feed at://… --starter-pack https://bsky.app/starter-pack/… --list at://…
```

Enumerates rosters (feeds, starter packs, lists), hydrates profiles in free
`getProfiles` batches, snapshots them, and extracts cross-platform bio links
(→ `identity_edges`) and no-AI signals (→ `attestations`). All calls are
recorded in `api_usage`.

### Clustering

```sh
uv run python -m inkpages.cluster
```

Reciprocal edges merge automatically; roster-sourced accounts with no edges
become singleton artists; one-directional links to prominent accounts and
artist-merge proposals go to the review queue instead of auto-applying.

### Review UI (local)

```sh
uv run python -m inkpages.review_ui   # http://127.0.0.1:8322
```

Browse the directory (badges, 18+ flags, follower ordering), inspect any
artist's accounts with bio evidence, work the review queue
(approve/reject attaches and merges), and suppress/unsuppress artists.

### Link crawling & Twitter

```sh
uv run python -m inkpages.crawl_links --max-hubs 100     # t.co resolution + hub pages (free)
uv run python -m inkpages.harvest_twitter --max-posts 1000 --top 300   # paid, budget-guarded
uv run python -m inkpages.hydrate_twitter --limit 200                  # paid, budget-guarded
```

Paid workers check the `api_usage` ledger against `X_SPEND_CAP_CENTS`
(default $100) before any call and ledger every request.

## Status

Running end to end: Bluesky + Twitter discovery, shortener/hub crawling,
extraction (no-AI + NSFW signals, alt-vs-related mentions, activity),
clustering with review queue, and the local review UI. Next: full-scale
#PortfolioDay harvest, Eastern column (Skeb/Pixiv), region classification,
ranking.
