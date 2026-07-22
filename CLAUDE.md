# inkpages — project guide

A public directory of digital artists who self-attest they don't use generative
AI, linking each artist's accounts across platforms. This is an
**entity-resolution + labeling** system: artists publish their own cross-platform
links (bios, Skeb/Pixiv profiles, Linktree/Carrd/potofu hubs) and we resolve
those self-published claims into identity clusters with full provenance. "No AI"
is displayed strictly as the artist's own attestation, never our classification.

Full brief: `~/Desktop/artist-directory-brief.md` (outside the repo). Design
rationale: `docs/schema.md` and `docs/pipeline.md`. Untapped-source scouting
(vgen/itaku/misskey/tumblr next; cara+mihuashi+instagram no-go, with reasons):
`docs/source-scouting.md` (probed live 2026-07-22).

## Current state (2026-07-22 end of day — three sources landed, zines purged)

**Directory 5,655 artists** (lang_en 3,969 / ja 2,075 / zh 236), ~32k
account rows, **1,103 vgen-anchored**. Paid X spend **$78.92 of $100**.
Review queue ~135 pending. Migrations at **0032**. Smoke green.

Today's session in order: review-UI bugfixes -> zine/project purge ->
DeviantArt -> source scouting -> VGen -> sources-page provenance ->
UI polish -> TikTok -> vgen tier-1/2 cull. Key durable facts:

- **Project accounts are parsed out entirely** (migration 0029,
  `accounts.project` + `flag_project_accounts` sweep every cluster run):
  zine/bang-suffixed handles, project-titled display names, or
  self-describing bios ("A <fandom> zine", 合同誌です). Precision-tuned —
  contributor mentions ("creating zines", アンソロ寄稿) never match;
  human-attached members exempt. ~2,700 flagged: excluded from union-find
  AND flip-rescue, hidden from Connections, barred from the paid twitter
  backlog (and worthless as vouchers), singleton_gate items auto-reject,
  all-project artists auto-demote (text-less display-only members can't
  vouch). **Referrer-gated twitter hydration**
  (`hydrate_twitter.gated_handle_backlog`): paid reads need an
  artist-flavored voucher (listed-artist member / roster-discovered /
  art-keyword referrer, one hop through hubs); ~1,000 zine-chain handles
  sit gated at zero cost. This fixed frontier economics — every wave since
  converged profitably.
- **DeviantArt live** (`discover_deviantart.py`, migration 0030): official
  RSS backend popular feeds (overall+category+search variants, ~6 pages
  each, rotate daily — reruns accrete; feed recorded in discovery_details).
  About pages open (1.5s pace, 90s/abort backoff on 403). state ->
  userId/watchers/socialLinks/bio. **DA->DA about links are `related`
  (same_platform_mention)** — feature dumps, not identity.
  `deviantart:rss/about` snapshots excluded from reextract (edges derive
  from full markup; snapshot stores tagline+excerpt).
- **VGen live and tier-1/2 only** (`discover_vgen.py`, migration 0031):
  robots Content-Signal `use=reference` permits; ~124k of 358k sitemap
  users are artists. No native top-N sort — harvest walks the
  server-rendered category listing heads (top-20 relevance each), ranks
  distinct artists by client totalReviews, mints top N. **Default walk =
  the 42 tier-1/2 `ARTIST_ROOTS`** (601 listings, ~3.5k artists/walk;
  docs/vgen-categories.md has all 147 roots tiered); `--all-categories`
  overrides, `--max-new` bounds accretion. Profile `__NEXT_DATA__` ->
  userID native_id, registered socials (profile_field), servicesStatus
  OPEN/CLOSED -> authoritative comms (`vgen:services_status` .95), tags +
  ratings in platform_stats (DB-only, not rendered; ratings saturated at
  ~5.0 — reviews is the metric). vgen->vgen bio links = related mentions.
  vgen:profile snapshots STAY in reextract. **Cull executed**: 666
  tier-3-5-only artists demoted (`vgen_non_artist_category`), 103
  resurrected after a fresh walk merged full categories
  (`vgen_tier12_resurfaced`) — **walk-first before culling on capped
  category data, always**. Listing cursors are client-API-only: scale via
  listing heads + rotation, not depth.
- **TikTok = display-only platform** (migration 0032, like instagram/
  weibo): `tiktok.com/@handle` pattern, vm/vt.tiktok.com shorteners, 158
  website rows reclassified.
- **/sources shows exact derivation per source**: `SOURCE_DERIVATION`
  recipe line + `SOURCE_BREAKDOWN_SQL` live chips (pixiv tags/modes,
  graphtreon categories, bsky feeds, DA feeds, vgen listing heads). Keep
  both dicts in sync when adding a source. `db.set_platform_stats`
  **merges** jsonb (was replace — second writer used to wipe the first).
- **UI**: unbroken-run wrapping scoped to `.bio`/`.wrapany` only (global
  td wrapping broke handles mid-word); td.nowrap on dates/platform/
  confidence/followers; td.trunc ellipsis+tooltip on names/emails/slugs;
  main 1400px; select-all checkboxes on artist-page bulk forms; stats
  macro renders scalars only (a raw list chip once stretched pages).
  **Restart the review UI after committing code the running server hasn't
  imported** — a stale process mixing old modules with new imports was
  the entire "merge/approve 500" mystery (ImportError on lazy import).

**Next up (in priority order — user directive 2026-07-22 evening):**
1. **Misskey cross-hydration** (598 held accounts, open per-instance API,
   free edges from profile fields[]).
2. **Xfolio enrichment** (87 held accounts; see docs/source-scouting.md —
   robots only blocks /fanbox paths, portfolio pages open).
3. **Tumblr enrichment** — blocked on user registering a free API key
   (tumblr.com/oauth/apps); 1,568 held accounts waiting.
4. **Cara exploration** (aligned community; re-probe for an official API
   or openly served endpoints — never circumvent bot protection).
5. **Work the review queues** (~135 pending) + the Demoted page (the 563
   vgen culls and 48 zine-sweep demotions may hide a few real artists).
6. Recurring skims: DA popular rotation, vgen tier-1/2 re-walks
   (--max-new bounded), pixiv tag rounds, bluesky list/starter-pack
   expansion.

**Itaku: skipped entirely** (user directive 2026-07-22 — do not build the
itaku worker despite the favorable scouting notes).

**Candidate paid X harvest (user-proposed, undecided)**: @Artistreccs
follows ~65k accounts, est. 80-90% artists. True top-500-by-followers
needs all 65k user reads (~$650 at 1¢/read) — over budget. Cheaper cuts:
most-recent-N follows ($1/100), or timeline RT-harvest (posts 0.5¢,
authors free via expansion). Needs a new `following()`/timeline method in
`twitter.py` XApi. Awaiting user's budget call.

## Previous state (2026-07-22, end of session — western discovery expansion)

**Directory 3,983 artists** (2,601 → 3,983 today; lang_en ~2,039 vs ~635
yesterday — the western cohort tripled). ~17.5k accounts. Paid X spend
**$52.24 of $100**. Review queue **96 pending** (62 singleton_gate + 29
anomalies/other + 5 cluster_merge) + **67 artists in needs_review** (48 from
the component-gate retro sweep + 19 older). **383-account twitter backlog
(~$3.83) intentionally left** — see frontier note below. Smoke green,
migrations at 0028.

Two new discovery sources + one enrichment channel landed today:

- **Patreon via Graphtreon** (`discover_patreon.py`): `--harvest` crawls
  Graphtreon's per-category top-50 lists (4 metrics × drawing-painting/
  comics/animation, SFW+adult; robots-permitted; `--max-new N` caps new
  creators — use it when the user gives a number). Adult categories → nsfw
  platform_flag. Stats (paid_members → followers_count,
  monthly_earnings_usd, category/rank) in `platform_stats`; stat cells
  PRECEDE the creator anchor on every list template. `--hydrate-known`
  fetches patreon.com pages (never `/api/`) and parses ProfilePage JSON-LD
  (`sameAs` → profile_field edges; Patreon's own Organization block +
  footer socials excluded). `--graphtreon-enrich` backfills category/stats
  for never-charted patreons via creator pages (closed category-name list;
  404 → `graphtreon_tracked:false`, never refetched). 857 creators, ~1,000
  pages hydrated, 955 categorized.
- **ArtStation** (`discover_artstation.py`): only the community trending
  feed is openly served (`--max-new`, dimension=2d first) — **profiles/
  project JSON/HTML are Cloudflare-bot-walled and we never circumvent bot
  protection** (also: no mass Save-Page-Now requests — that's proxy-
  fetching). Roster rows carry id/name/avatar/position only. `--enrich-
  known` = full-depth sweep refreshing existing rows; `--wayback-enrich`
  pulls organically archived `users/{u}.json` from the Internet Archive
  (~6% coverage, famous-name skew; 34 profiles → 88 social edges; misses
  remembered). 576 artstation accounts; migration 0028 puts 'artstation' in
  directory_entries.sources + SOURCE_OPTIONS facet.
- **Component evidence gate** (`cluster.py` step 2 + `looks_like_project`):
  reciprocal components with NO roster-sourced member must read like an
  artist — zine/big bang/anthology/合同誌/アンソロ text or zero art evidence
  → `singleton_gate` review instead of auto-creation (approve creates the
  anchor's artist; the component attaches next run). Zines publish
  reciprocal twitter↔carrd exactly like a person; graph shape can't tell
  them apart, self-description can. Retro sweep demoted 48.

**Frontier economics (measured)**: hydrating the twitter backlog regrows it
(each ring's bios reference the next ring, ~330-380/round at ~$3.3). Yield
collapsed by ring 3: last round = 0 artists, 0 members, 62 gated components.
Deep-frontier hydration is low-value; prefer roster sources and work the
singleton_gate queue instead.

Review-UI additions (all live): manual add-account (paste URL → human-added
membership), dismissable connections (migration 0027 — `status='dismissed'`
edges that upsert_edge can never resurrect + admin event blocks re-attach),
bulk detach/attach-merge/remove with checkboxes, /removed page (suppressed
artists+accounts, hidden accounts with unhide, invisible artists),
localStorage filter persistence (query string + panel open state), website
facet removed, humanized stat chips. Standing sub-50 cull is a pipeline step
(`policy.CULL_MIN_FOLLOWERS`). Patreon reserved paths (`/creation?hid=`,
`/collection/`…) and hub-infra domains (fonts.googleapis.com etc.) are
blocked; bare `handle.bsky.social` parses as bluesky.

**Next up (in priority order):**
1. **Work the review queues**: 62 singleton_gate (one click legitimizes a
   whole component), Demoted page restores (retro sweep caught a few real
   artists, e.g. andramion), 29 anomalies, 5 merges, and the older 132
   artists with unresolved same-person claims.
2. **DeviantArt discovery** (official free OAuth API — full profiles, no
   bot wall; will behave like the Patreon run). Remember the cross-
   hydration rule: enrich existing deviantart bio-link accounts in the same
   session the source lands.
3. **Twitter Lists harvest** (official API, 1¢/member — best paid
   precision) and periodic #PortfolioDay runs.
4. Bluesky list/starter-pack expansion (free); Cara (aligned, unofficial
   endpoints); VGen (needs a parseability scout).

## Previous state (2026-07-21, post bugfix/optimization pass)

Pre-discovery bugfix pass (commit 6c4ecb2): **t.co resolution was silently
broken** — t.co serves browser UAs a 200 interstitial, so all 3,554 cached
"resolutions" were self-referential; `resolve_url` now uses a plain-UA client,
treats same-host results as failures (never cached), and skips twitter bios'
own t.co links entirely (API entities expand those free). Poisoned cache rows
purged; genuine t.co links (skeb/fantia/carrd destinations) now resolve.
`_SHORTENER` got a left boundary (artist.co ≠ t.co); instagram/twitch patterns
exclude more reserved paths (reels, stories, drops…). **reextract no longer
wipes email/commission with None** (it reset 309 pixiv accept_request statuses
to unknown every run — backfilled) and honors pixiv `acceptRequest` as
platform state. `hydrate_twitter --refresh` skips deleted accounts. smoke.sql
output trimmed. Directory steady at 2,601; review queue steady at 26.

## Previous state (2026-07-21, post-audit)

A full app+DB audit ran on 2026-07-21; all fixes landed (5 commits: invariant
fixes, migration 0022/0023 hardening, edge-churn fixes, review-UI
CSRF/perf, cleanup). Key new behavior:

- **Migration 0024 (directory polish)**: `directory_entries.display_name` is
  derived from the top-`display_rank` visible account (twitter > bluesky >
  hubs > skeb > pixiv, fallback handle then `artists.display_name`); new
  `hydrated_at` column (newest snapshot of a member twitter account, else
  newest overall) backs the sortable one-line "updated" column. Directory is
  **SFW by default** — 18+ artists appear only via the single "show 18+"
  filter toggle (`show18=1`; the nsfw-only flag is URL-compatible but no
  longer a checkbox). `review_ui` honors `$PORT` (launch.json has
  `autoPort: true`).
- **Migration 0026**: same-platform ties in the name/avatar/accounts
  ordering break by followers desc (an artist's low-follower サブアカ was
  naming them over the main). Twitter pattern excludes reserved paths
  (`widgets`, `messages`, `settings`, `intent/` etc. — twitter.com/widgets.js
  on hub pages had wired a junk account to 35 artists). Avatars backfilled +
  262 pre-avatar-era accounts re-hydrated: only 12 directory artists lack a
  pfp. Platform filter checkboxes: display_rank order, most-common first
  within a rank. Review UI: scroll position survives decide/confirm POSTs;
  anomaly queue is one card per artist (bulk_decide takes comma-joined ids);
  artist pages have a per-account nsfw column (18+ chip only with evidence,
  else "safe" — artists keep 18+ alts).
- **profcard.info / twpf.jp / tsunagu.cloud are crawlable `link_hub`
  platforms** (migration 0025, display_rank 15 so an opaque profcard uid
  never wins the name derivation). `crawl_links.SERVICE_ACCOUNTS` blocklists
  hub services' own footer/social accounts (TSUNAGU's twitter + demo profile
  reciprocally auto-merged into a fake artist once — suppressed). Tumblr's
  pattern now excludes reserved paths (`tumblr.com/contact` etc.).
- **Artist page titles use the directory name rule** (top-display_rank
  visible account, any membership confidence). **Anomaly 5b**
  (`ANOMALY_CROSS_ARTIST_REFS=3`): member accounts whose edges touch ≥3
  other artists queue an `other` review item (junk shared targets or
  unmerged alt-artist groups — the deppa/sdns53 triangle, vat0uq/vatouq).
- **Shorteners are centralized in `extract.SHORTENER_DOMAINS`** (t.co, bit.ly,
  tinyurl, goo.gl, x.gd, onl.tw/sc, buly.kr, **pixiv.me**) — drives
  `find_short_links`, the `resolve_shorteners` SQL scan (x.gd was missing
  there: 152 junk website accounts, all edges retracted), and the
  `_NON_WEBSITE_DOMAINS` blocklist (also newly blocks dmm.co.jp,
  toranoana.jp, piccoma.com storefront pages). pixiv.me resolution recovered
  ~121 real pixiv identity edges; a one-off backfill re-resolved short links
  that arrived via structured profile fields (outside the bio scan).

- **Rejected `cluster_merge` pairs never auto-merge again** (`merge_rejected`
  checked in step 2 + `try_reciprocal_artist_merge`); step-2 auto-merges
  resolve pending items (`pipeline:reciprocal_component`).
- **`merge_artists` honors admin detaches on the keeper** (skips those
  accounts, logs `admin_blocked_accounts` in the merge event).
- **Paid X spend is ledgered per page/batch inside `XApi`** (crash-safe);
  callers no longer call `log_api_usage`. Search never reads past
  `--max-posts`.
- **`status='hidden'` survives everything** (hydration, `--refresh`, link
  checks, hub crawls) — only explicit admin SQL lifts a cull.
- **Skeb structured links are `profile_field`** via shared
  `emit_structured_edges()` (discover_skeb + reextract) — the
  retract/re-add churn is gone; the fix healed ~500 memberships and
  auto-merged 5 artist pairs on first run.
- **Step 4b re-checks all flipped hints** (`secondary_link`,
  `over_platform_cap`, `unreciprocated_prominent`) against their original
  conditions.
- **Migration 0022**: `evidence_snapshot_id NOT NULL` (edges+attestations);
  account-scoped suppressions hide only that account in
  `directory_entries` (artist-scoped still hides the artist); partial
  unique indexes on in-force suppressions; `artist_events` expression
  indexes for heal/guard/reextract paths; `merged_into` chains collapse on
  merge. Migration 0023: `resolved_links` shortener cache
  (`resolve_shorteners` is throttled + suppression-guarded).
- **Review UI**: per-process CSRF token on every POST (403 without);
  `/img` proxy no longer follows redirects; queue enrichment batched;
  index stats cached 60s (cleared on POST); anomaly/giant-component items
  use Acknowledge/Dismiss and never fake a merge; approving a stale
  `cluster_merge` skips already-merged artists.
- `smoke.sql` now runs against a populated dev DB (fixture-scoped asserts)
  and covers NOT NULL provenance, no merge chains, suppression scoping.

## Previous state (2026-07-21 morning)

- **2,601 listed artists** (post-audit healing merged duplicates; +89 hidden
  by the sub-50-follower cull; net down slightly from the 2026-07-21
  shortener-recovery, hub-crawl, and service-account cleanup passes),
  ~13.9k accounts, 1,046 flagged 18+, 118
  no-AI badged. Languages: ~1,843 ja /
  ~635 en / ~100 zh / ~23 ko. Paid X spend: $34.12 of $100.
- **Discovery live**: Bluesky (free), Skeb (free — Algolia ranking +
  `--hydrate-known`), Pixiv (free — SFW rankings + **tag-search harvest**:
  `--tag オリジナル --tag-mode r18 --tag-order popular_d --new-only --max-new N`;
  the `PIXIV_SESSION` is **premium** so popularity sort works; `ai_type=1`
  excludes author-flagged AI works — discovery filter only, never an
  attestation), Twitter (paid — $34.12 of a $100 budget spent).
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
- Review queue: **26 pending** — 5 `cluster_merge` + 21 anomalies/other
  (post-hub-crawl + re-hydration counts incl. the cross-artist-refs
  anomalies; 19 artists additionally sit in `needs_review`). Twitter
  hydration backlog is **clear** (user standing rule: auto-run when
  estimated spend < $10). Artist-level cyclical references auto-merge (see
  clustering model). **193 unresolved same-person claims across 132
  artists** are visible+attachable on artist pages (see Review UI) — mostly
  `no X/bsky` profiles whose linked X already belongs to another artist, or
  pixiv↔twitter links a guard held back; clear them by browsing, not just
  via the queue.
- **Next up (in priority order):**
  1. **Work the 132 artists with unresolved same-person claims** (attach/merge
     from artist pages) + the 5 `cluster_merge` + 21 anomaly reviews.
  2. **Bluesky list/starter-pack expansion** (free discovery breadth).
  3. Consider deeper tag-search harvests (more pages, more tags, e.g.
     `オリジナル10000users入り` as a curated tier) — each round is ~free.
- **Verify-against-live reminder**: the pane blanks on long scrolls; prefer
  `get_page_text` / SQL over screenshots for tall pages.
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
inverse of detach — vouch a connection is same-person: merges the other artist
in or attaches the floating account, and promotes the edge to `same_person`;
a manual merge also auto-resolves any pending `cluster_merge` for the pair),
review queue (merges / anomalies / attaches, bulk select),
demoted page, suppress/unsuppress. pixiv/youtube accounts are labelled by
`display_name` (id kept as handle/native_id). Hotlink-protected pixiv avatars
(`i.pximg.net`) are served through a host-whitelisted `/img` referer proxy.
Collapsible long bios. Connections already members of the artist are hidden
(that link is internal to a merge, not an external connection).

The **Connections** table shows `related` edges AND **unresolved
`same_person` claims** — a same-person edge whose target is a NON-member (it
belongs to another artist, or a guard held it back). These were previously
invisible outside the review queue (the load-bearing bug behind "pixiv links
an X but it doesn't even show as a connection"). Each row shows the target's
owning artist (if any); the button is **merge** (target belongs to another
artist) or **attach** (floating). All confirmations use an in-page `<dialog>`
modal, not `confirm()` (base template intercepts `form[data-confirm]` /
`button[data-confirm]`, submitter-aware for bulk decisions).

Two explainer pages (nav-linked, plain-language + visual, for someone new to
the project): **/sources** (4-step Discover→Enrich→Cluster→Publish flow +
per-source volume bars from `SOURCE_META`, primary vs follow-on, cost/rule
chips) and **/rules** (card grid of every merge/guard/never rule as
node-and-arrow mini-diagrams with live counts pulled from the DB). Keep
`SOURCE_META` in sync when adding a `discovered_via`.

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

- **Cross-hydration rule (strict, user directive 2026-07-22)**: whenever a
  new discovery/enrichment source lands, ALL existing accounts on that
  platform must be enriched through it too — not just the accounts the new
  source discovers. Every source worker ships a backfill mode
  (`--hydrate-known`, `--graphtreon-enrich`) and it runs over the whole
  existing population as part of landing the source.

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
