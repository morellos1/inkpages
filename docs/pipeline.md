# Pipeline plan

Discovery → hint verification → hydration → extraction → clustering → region +
ranking → publish, with a quarterly re-verification loop. Every stage is an
idempotent worker writing to the schema in `docs/schema.md`; every paid call is
ledgered in `api_usage`.

Scope: ~10k Twitter artists (stratified Eastern/Western) + ~5k Bluesky, from a
candidate pool of ~50k handles.

## Budget

| Item | Volume | Unit cost | Estimate |
|---|---|---|---|
| #PortfolioDay harvest (one-time) | 50–100k post reads | ~$0.005/post | $250–500 |
| Candidate hydration (one-time) | ~50k user reads, `users/by` ×100/req | ~$0.01/user | ~$500 |
| Quarterly refresh | ~15k included + border zone | ~$0.01/user | ~$150–200/qtr |
| Twitter List member reads | small | user reads | minor |
| Bluesky (all operations) | unbounded | free | $0 |

Hard constraints: 2M post reads/month cap on the X API; **no gray-market
scrapers** (twitterapi.io, Apify, etc.) under any circumstances. Workers check
`api_usage` totals against configured caps before spending.

## Stage 1 — Discovery

Per-source workers append `accounts` (with `discovered_via`), artist-published
`identity_edges`, or quarantined `discovery_hints`. The distinction that
matters: **who published the link?**

- Artist-published (→ `identity_edges` directly): Skeb profiles displaying X
  handles, Pixiv profile social links, potofu.me / lit.link / Linktree / Carrd
  hub pages, Bluesky bios linking back to Twitter.
- Third-party-asserted (→ `discovery_hints` only): Danbooru artist entries,
  convention exhibitor lists, curated rec lists. Boorus additionally never
  appear in any published provenance (see schema tradeoff 7).

Sources by column:

- **Eastern**: Skeb creator rankings; Pixiv rankings + profile links; XFolio
  membership (doubles as an attestation signal — the platform is no-AI by
  policy); potofu.me / lit.link hubs; booru hints.
- **Western**: one-time bounded **#PortfolioDay harvest** via paid X API search
  (the linchpin for Twitter-native Western artists — posts are self-published,
  and bios arrive at hydration); curated Twitter Lists (members via cheap user
  reads); Patreon art-category rankings (via Graphtreon — *verify it still
  exists before building this worker*); convention artist-alley exhibitor
  lists; Bluesky-exodus accounts whose bios link back to Twitter; Cara,
  ArtStation, DeviantArt (official API + noai flags), Tumblr (public API),
  VGen, Ko-fi, Gumroad, INPRNT.
- **Bluesky**: fully open AT Protocol — enumerate via art feeds, starter
  packs, and anti-AI labeler subscriber lists; `getProfiles` in batches of 25,
  free.

Discovery workers consult `suppressions` before inserting: an opted-out
artist's accounts are never re-added.

## Stage 2 — Hint verification

For each pending `discovery_hints` row: fetch the **artist's own** bio or hub
page (respecting per-platform read policy and budget — a Twitter fetch costs
money, a Pixiv/hub fetch doesn't), and only if the artist's own published
content confirms the link, write an `identity_edge` with the artist's snapshot
as evidence. Mark the hint verified/rejected either way; hints older than a
configured window expire. Instagram is never fetched — hinted Instagram
handles stay display-only.

## Stage 3 — Hydration

Batch-fetch profiles for the deduped candidate pool and write
`account_snapshots` (full raw JSON kept for re-extraction):

- Twitter: `users/by` at 100 handles/request (~$500 one-time for ~50k),
  ledgered per request in `api_usage`; resolves handles → stable numeric
  `native_id`, catching renames.
- Bluesky: `getProfiles` batches of 25, free; DIDs are the native ids.
- Other platforms: official APIs (DeviantArt, Tumblr) or public profile pages
  at polite rates.

## Stage 4 — Extraction

A pure function of stored snapshots (re-runnable when parsers improve):

- Outbound links in bios/hubs → `identity_edges` (and new candidate accounts).
- No-AI signals → `attestations`: #NoAI variants, AI学習禁止 / AI使用禁止,
  Glaze/Nightshade mentions, Cara/XFolio membership, DeviantArt noai flags,
  Bluesky anti-AI labeler subscriptions. Refresh `last_seen`; deactivate
  signals that disappeared (badges drop, never linger).
- Bio language detection → `accounts.bio_langs` (input to region
  classification). Use `lingua` or equivalent — must be reliable on short
  mixed-language bios, especially ja/en.

## Stage 5 — Clustering

Connected components over near-proof evidence: reciprocal directed-edge pairs,
including hub-mediated reciprocity. Then:

- One-directional strong edges attach to a cluster at `strong` confidence,
  except when the target is high-prominence (top-N by followers) — those go to
  a human review queue, since impersonators link *to* famous accounts.
- `same_handle` edges never merge anything; they surface as review
  suggestions only.
- All merges/splits/attachments emit `artist_events`; clustering never
  overrides a `removed_at` membership closed by a human.

New clusters get `artists` rows with generated slugs; membership changes to
existing artists preserve slugs and moderation state.

## Stage 6 — Region classification + stratified ranking

Region (pipeline-critical, not optional): `bio_langs` + platform fingerprint
(Skeb/Pixiv/XFolio lean Eastern; ArtStation/Patreon lean Western) → per-artist
`region` with confidence; `region_source = 'manual'` overrides always win.
Low-confidence artists surface in a review queue rather than guessing.

Ranking: within-region follower ranking on the primary platform. Separate
cuts recorded as `ranking_runs`: top 10k Twitter split Eastern/Western, top 5k
Bluesky on its own — never normalized across platforms. Entries near the
threshold get `border_zone = true` and join the quarterly refresh set.
Auxiliary free signals (Skeb rank, Pixiv followers, Patreon patron counts)
are stored in run `params`/`discovery_details` for tie-breaking and sanity
checks, not cross-platform normalization.

## Stage 7 — Publish

Export `directory_entries` (the only publish surface) to static JSON for the
future site. The view already enforces: suppressions honored, merged artists
excluded, display-only handles unlinked, badge derived from live attestations
only. Published provenance shows artist-published evidence exclusively.

## Stage 8 — Re-verification loop (quarterly)

1. Re-hydrate the ~15k included artists + border zone (~$150–200/qtr).
2. Re-run extraction: refresh `last_seen` on attestations (drop badges whose
   signals vanished), mark `identity_edges` stale/retracted when links
   disappear from bios.
3. Re-run clustering + ranking as new runs; compare against previous run for
   anomalies (mass drops usually mean a parser broke, not a mass exodus).
4. Work the `corrections` queue: accepted `ai_use` → badge removal or quiet
   suppression (never a published accusation); `wrong_link`/`impersonation` →
   membership closes + events; `opt_out` → suppression.

## Open items before building

- Verify Graphtreon still exists (Patreon rankings source).
- Confirm XFolio profile enumeration mechanics (no public API assumed).
- Curate the initial set of Bluesky anti-AI labelers and art feeds.
- Decide the "high-prominence" review threshold for one-directional attaches.
