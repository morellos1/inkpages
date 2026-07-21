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

- **2,607 listed artists** (+89 hidden by the sub-50-follower cull), ~13.7k
  accounts, 1,049 flagged 18+, 120 no-AI badged. Languages: ~1,847 ja /
  ~637 en / ~101 zh / ~22 ko.
- **Discovery live**: Bluesky (free), Skeb (free — Algolia ranking +
  `--hydrate-known`), Pixiv (free — SFW rankings + **tag-search harvest**:
  `--tag オリジナル --tag-mode r18 --tag-order popular_d --new-only --max-new N`;
  the `PIXIV_SESSION` is **premium** so popularity sort works; `ai_type=1`
  excludes author-flagged AI works — discovery filter only, never an
  attestation), Twitter (paid — ~$31.17 of a $100 budget spent).
- The old pixiv R18-ranking throttle (~99 artists) is moot: R18 tag search
  reaches millions of works. 2026-07-21 harvest: +250 SFW / +200 R18 artists
  via オリジナル + 原創.
- **Auto-hydration**: `pipeline.py` now runs skeb+pixiv hydrate-known before
  crawl/check/cluster and classify_region after, then prints the paid twitter
  backlog + est. cost (never spends by itself). Repeat pipeline → hydrate_twitter
  rounds converge in ~2 iterations after a big discovery run.
- **Not built yet**: stratified ranking runs, Bluesky list/starter-pack
  expansion, Graphtreon/Patreon, ArtStation/Cara/DeviantArt/Tumblr, the public
  site.
- Review queue: **34 pending** — 12 `cluster_merge` + 22 anomalies/other.
  Artist-level cyclical references now auto-merge (see clustering model), which
  drained 10 of the old 21 merge conflicts.
- **Next up (in priority order):**
  1. **Work the 12 `cluster_merge` + 22 anomaly reviews** in the review UI.
  2. **Bluesky list/starter-pack expansion** (free discovery breadth).
  3. Consider deeper tag-search harvests (more pages, more tags, e.g.
     `オリジナル10000users入り` as a curated tier) — each round is ~free.
- **Verification cull**: Twitter/Bluesky accounts under 50 followers set to
  account status `hidden` (migration 0016); reversible with
  `update accounts set status='active' where status='hidden'`.

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
   `discover_skeb --hydrate-known` fetches skeb accounts referenced but never
   fetched (bio-link targets) to pull their OAuth `twitter_uid`; `discover_pixiv
   --r18-pages N` harvests R18 rankings (needs `PIXIV_SESSION`, flags nsfw).
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

**`pipeline.py`** chains hydrate-known (skeb, pixiv) → crawl_links →
check_links → cluster → classify_region, then prints the paid twitter backlog.
**Run it after every discovery/hydration run** (new bios mint new hubs whose
contents only exist after a crawl).

### Review UI

`review_ui.py` — Flask on `127.0.0.1:8322`. `uv run python -m inkpages.review_ui`
(a `.claude/launch.json` config named `review-ui` exists for the preview pane;
the browser pane reaches it at `127.0.0.1`, not `localhost`). Directory browse
with avatars/badges/sources, **faceted filters** (flags incl. `no X/bsky`;
conjunctive platform; disjunctive `source`; conjunctive `comms` open —
skeb/pixiv authoritative vs bio-attested; language), **sortable columns +
pagination**, id-slug pixiv artists shown by name with a `no X/bsky` flag chip,
per-artist
evidence pages with per-account **detach** and per-connection **confirm** (the
inverse of detach — vouch a `related` connection is same-person: merges the
other artist in or attaches the floating account, and promotes the edge to
`same_person`), review queue (merges / anomalies / attaches, bulk select),
demoted page, suppress/unsuppress. pixiv/youtube accounts are labelled by
`display_name` (id kept as handle/native_id). Hotlink-protected pixiv avatars
(`i.pximg.net`) are served through a host-whitelisted `/img` referer proxy.
Collapsible long bios. Connections already members of the artist are hidden
(that link is internal to a merge, not an external connection).

## Clustering model — the load-bearing logic (`cluster.py`)

Edges carry a **`claim`**: `same_person` (can cluster) vs `related` (graph
connection, shown in UI, never merges — partners, pfp artists, project credits,
websites, secondary same-platform links).

- **Reciprocal same-person edges** (incl. hub-mediated) → union-find components →
  near-proof merge. **Two existing artists in one reciprocal component
  auto-merge** (cap-guarded); 3+ artists or cap breach → `cluster_merge` review.
- **Artist-level reciprocity** (`try_reciprocal_artist_merge`): two existing
  artists whose clusters reference each other through ANY member accounts —
  cyclically, e.g. skeb→pixiv + pixiv→twitter where twitter+skeb are already one
  artist — auto-merge instead of queueing review. Prominence-flipped
  (`unreciprocated_prominent`) edges count as back-links and get restored to
  `same_person` (`artist_reciprocity` hint) on merge; pending `cluster_merge`
  items for the pair auto-resolve (`decided_by='pipeline:artist_reciprocity'`).
  Guards: same-platform cap, suppression check.
- **Heal is latest-event-only**: the start-of-run self-heal only honors the
  most recent `account_added` event per membership; older retracted-edge events
  don't re-heal a membership that was re-added on fresh evidence (fixed a
  63-membership remove/re-add oscillation on every run).
- **One-directional edges never queue for review** (best-effort policy, user
  directive): OAuth-verified links (Skeb `twitter_uid`, `relation_hint='oauth'`)
  and regexed alt mentions auto-attach; doubtful cases (prominent unreciprocated
  target, second same-platform, cap overflow) **flip to `related` connections**.
  If a connection later reciprocates, reextract restores `same_person` and the
  mutual path auto-merges.
- **Shared-hub reciprocity rescue** (`cluster.py` step 4b,
  `policy.RECIPROCITY_SHARED_MIN=2`): a prominent one-directional target
  (pixiv→X etc.) normally flips to `related` (impersonation guard). But flips
  stored as `unreciprocated_prominent` are re-checked every run: if the target
  links back to ≥2 of the artist's OWN distinctive downstream targets (personal
  hubs, excluding community shared-targets and other prominent accounts), it's
  provably the same person → attach + restore the edge to `same_person`.
  Guards: not a second same-platform account, cap-guarded, not a shared-target.
  Solves the JS-rendered-hub gap (Carrd renders links client-side, so
  `crawl_links` gets `link_count: 0` and no hub back-edges form — the shared
  *outbound* targets are the reciprocity signal instead). Manual **confirm** in
  the review UI is the override for cases below the threshold.
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
  `contact_email`/`link_checked_at`. `status`: active/unknown are published;
  `hidden` (migration 0016) removes from the directory + roster-singleton
  creation without deleting (snapshots/edges/membership kept) — used for
  verification culls. `directory_entries` gates every per-account subquery on
  `status in ('active','unknown')` (migration 0017).
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
  coherent feature; never commit `.env` (gitignored, holds X API creds +
  `PIXIV_SESSION` — **both were pasted in chat; user should rotate**).
- Paid workers check `X_SPEND_CAP_CENTS` (default 10000) against `api_usage`
  before any call and ledger every request. **Never make a paid X API call
  without explicit user approval of the spend.**
- Extraction (`extract.py`) is pure functions of text → re-runnable via
  `reextract.py`. When adding a platform: pattern in `_LINK_PATTERNS`, domain in
  `_NON_WEBSITE_DOMAINS`, row in a seed migration, maybe `display_rank`. Aliases
  for an existing platform are just extra `_LINK_PATTERNS` rows mapping the same
  slug (e.g. `tr.ee`→linktree). `misskey` is a first-class platform now
  (migration 0018 also reclassifies old `website` rows that were misskey links).
  **After adding a pattern, run `reextract.py` to backfill stored snapshots**
  (e.g. ~87 `tr.ee` links still parsed as `website` until a reextract pass).
  `weibo` + `facebook` are first-class display-only platforms (migration 0020
  reclassified old `website` rows; never fetched, like Instagram).
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
