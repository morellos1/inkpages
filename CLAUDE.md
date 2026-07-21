# inkpages — project guide

A public directory of digital artists who self-attest they don't use generative
AI, linking each artist's accounts across platforms. This is an
**entity-resolution + labeling** system: artists publish their own cross-platform
links (bios, Skeb/Pixiv profiles, Linktree/Carrd/potofu hubs) and we resolve
those self-published claims into identity clusters with full provenance. "No AI"
is displayed strictly as the artist's own attestation, never our classification.

Full brief: `~/Desktop/artist-directory-brief.md` (outside the repo). Design
rationale: `docs/schema.md` and `docs/pipeline.md`.

## Current state (2026-07-21)

- **~2,070 artists**, ~11k accounts. Languages: ~1,319 ja / ~648 en / ~80 zh / ~20 ko.
- **Discovery live**: Bluesky (free), Skeb (free), Pixiv (free), Twitter
  (paid — ~$24.59 of a $100 budget spent).
- **Not built yet**: stratified ranking runs, Bluesky list/starter-pack
  expansion, Graphtreon/Patreon, ArtStation/Cara/DeviantArt/Tumblr, the public
  site.
- Review queue: a handful of one-directional merge conflicts + ~20 anomaly flags.

## Non-negotiable rules (from the brief)

1. **Identity claims are edges with provenance, never merged truth.** Every edge
   points at the `account_snapshots` row it was extracted from.
2. **Boorus are discovery hints only** — `discovery_hints` has no join path into
   the `directory_entries` publish view (asserted structurally in
   `scripts/smoke.sql` via `pg_depend`). Never put booru data in lineage.
3. **`same_handle` never auto-merges** (impersonation).
4. **Twitter only via the official paid API**; Instagram is `display_only`
   (never fetched, handle shown). No gray-market scrapers, ever.
5. **Never publish an AI-use accusation as fact.** Accepted `ai_use` corrections
   → badge removal or quiet suppression only.
6. **Default-list + opt-out**: `suppressions` rows persist independently so
   re-discovery can never re-add an opted-out artist.

## Architecture

Python + Postgres, plain-SQL migrations. Local DB: `docker compose up -d`
(Postgres 16 on **port 5433**). Apply migrations: `uv run scripts/migrate.py`.
Everything runs under `uv`.

### Pipeline stages (each an idempotent worker in `src/inkpages/`)

1. **Discovery** — `discover_bluesky.py`, `discover_skeb.py`, `discover_pixiv.py`,
   `harvest_twitter.py`. Write accounts + snapshots + edges + attestations.
2. **Link crawling** — `crawl_links.py`: resolves shorteners (t.co, x.gd) and
   crawls hub pages (Linktree/Carrd/potofu/lit.link/bio.site) for inner links.
   Linktree TLS-fingerprints Python → curl fallback on 403.
3. **Dead-link check** — `check_links.py`: 404/410 → status `deleted`.
4. **Hydration** — `hydrate_twitter.py` (paid): `users_by_ids` (rename-proof)
   for native-id rows + `users/by` for handle-only. **Always run after any new
   discovery source** to fetch follower counts/bios for surfaced Twitter handles.
5. **Re-extraction** — `reextract.py`: pure re-parse of stored snapshots (free);
   retracts edges the current parser no longer reproduces, heals downstream.
6. **Clustering** — `cluster.py`: union-find over reciprocal same-person edges.
7. **Region + language** — `classify_region.py`: script detection + platform
   fingerprint → `artists.language` (ja/en/ko/zh/unknown) + `region`.
8. **Publish** — `directory_entries` view is the only publish surface.

**`pipeline.py`** chains crawl_links → check_links → cluster. **Run it after
every discovery/hydration run** (new bios mint new hubs whose contents only
exist after a crawl).

### Review UI

`review_ui.py` — Flask on `127.0.0.1:8322`. `uv run python -m inkpages.review_ui`
(a `.claude/launch.json` config named `review-ui` exists for the preview pane;
the browser pane reaches it at `127.0.0.1`, not `localhost`). Directory browse
with avatars/badges/sources, per-artist evidence pages with per-account
**detach**, review queue (merges / anomalies / attaches, bulk select), demoted
page, suppress/unsuppress.

## Clustering model — the load-bearing logic (`cluster.py`)

Edges carry a **`claim`**: `same_person` (can cluster) vs `related` (graph
connection, shown in UI, never merges — partners, pfp artists, project credits,
websites, secondary same-platform links).

- **Reciprocal same-person edges** (incl. hub-mediated) → union-find components →
  near-proof merge. **Two existing artists in one reciprocal component
  auto-merge** (cap-guarded); 3+ artists or cap breach → `cluster_merge` review.
- **One-directional edges never queue for review** (best-effort policy, user
  directive): OAuth-verified links (Skeb `twitter_uid`, `relation_hint='oauth'`)
  and regexed alt mentions auto-attach; doubtful cases (prominent unreciprocated
  target, second same-platform, cap overflow) **flip to `related` connections**.
  If a connection later reciprocates, reextract restores `same_person` and the
  mutual path auto-merges.
- **`MAX_SAME_PLATFORM = 3`**: no artist accumulates >3 accounts on one identity
  platform via clustering; components exceeding it never auto-merge (guards
  against the mega-cluster chain reaction — see git 639 autopsy).
- **Shared-target guard**: an account linked one-directionally by 2+ different
  artists is a community resource (Discord, event page) — never attached. OAuth
  edges override this; user-entered profile fields do not.
- **Roster singletons**: accounts from `policy.ROSTER_SOURCES` become artists
  with no edges. Open-harvest sources (`HARVEST_NEEDS_EVIDENCE`) additionally
  need artist evidence (art-keyword bio or own links) or go to `singleton_gate`
  review.
- **Anomaly pass** (end of clustering): flags credits-dump graphs (hub fanout
  ≥12, hub-attached ≥10, related ≥15) for manual review. Never auto-acts.
- **Human decisions are sacred**: memberships closed by `admin:*` events never
  auto-reattach; pipeline-closed ones may re-form.

Key distinction: **OAuth/platform-verified links** (only Skeb `twitter_uid` so
far, `relation_hint='oauth'`) get exemptions from prominence/shared-target
guards. **User-entered profile fields** (pixiv social block, other Skeb ids) get
no exemptions — shared defaults like DLsite's YouTube channel are caught by the
normal guards. See `discover_skeb.py` `FIELD_VALUE_BLOCKLIST`.

## Schema essentials

- `accounts` — one per (platform, native identity). `native_id` is truth
  (handles mutate). `platform_stats`/`avatar_url`/`commission_*`/`last_post_at`/
  `contact_email`/`link_checked_at`.
- `identity_edges` — directed claims; `claim`, `relation_hint`, `evidence_type`,
  `evidence_snapshot_id`, `status`.
- `artists` — stable slug, `merged_into` pointer, `language`, `region`, `status`.
- `artist_accounts` — membership with history (`removed_at`, never deleted).
- `attestations` (no-AI) / `content_flags` (nsfw) — per-account self-signals,
  `first_seen`/`last_seen`/`active` so removal is detectable; badge derived.
- `review_items` — kinds: `cluster_merge`, `one_directional_attach` (legacy,
  drained), `singleton_gate`, `other` (anomalies/giant components).
- `suppressions`, `corrections`, `ranking_runs`/`ranking_entries`, `api_usage`.

## Conventions

- Commit messages end with the Claude co-author trailer. Commit after each
  coherent feature; never commit `.env` (gitignored, holds X API creds — **user
  should rotate; they were pasted in chat**).
- Paid workers check `X_SPEND_CAP_CENTS` (default 10000) against `api_usage`
  before any call and ledger every request. **Never make a paid X API call
  without explicit user approval of the spend.**
- Extraction (`extract.py`) is pure functions of text → re-runnable via
  `reextract.py`. When adding a platform: pattern in `_LINK_PATTERNS`, domain in
  `_NON_WEBSITE_DOMAINS`, row in a seed migration, maybe `display_rank`.
- URL matching is ASCII-only (bios decorate links with emoji). Handles are
  truncation-guarded (ellipsis) and hex-junk-guarded (CDN hashes).
- Verify against live data before declaring done: run the worker, check the DB,
  screenshot the UI. Tune thresholds against real false-positive rates.

## Verify

```sh
docker compose up -d && uv run scripts/migrate.py
docker compose exec -T db psql -U inkpages -d inkpages < scripts/smoke.sql  # publish-rule asserts
uv run python -m inkpages.review_ui   # http://127.0.0.1:8322
```
