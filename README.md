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

## Status

Schema done; Bluesky discovery worker running. Next: clustering (including
singleton-artist creation for roster-sourced accounts — see
`src/inkpages/policy.py`), then the Twitter column (needs paid X API access).
