# Source scouting — untapped platforms (probed live 2026-07-22)

Every claim below was verified against the live site on 2026-07-22 (robots
fetch + endpoint probes with a generic browser UA, 1-2s pacing). Ranked by
build priority at the end. Project rules applied throughout: no bot-wall
circumvention, no signed-API reverse engineering, no gray-market scrapers;
robots + content signals honored.

## VGen — vgen.co (BUILD FIRST: the western Skeb)

- **Access**: `User-agent: * → Allow: /` with Cloudflare Content-Signal
  `search=yes, ai-train=no, use=reference` — a reference directory is the
  explicitly permitted use. Server-rendered Next.js; no bot wall.
- **Per-profile data** (`__NEXT_DATA__` on `vgen.co/{username}`):
  - `user.userID` (stable UUID → native_id), `bio`, `displayName`,
    `avatarURL`, `languages`
  - `user.socials[]` — **registered social links** (twitter/bsky/lit.link…)
    → profile_field edges, exactly like the pixiv social block
  - `user.servicesStatus` OPEN/CLOSED — **platform-authoritative commission
    status** (a third comms facet alongside skeb/pixiv)
  - `user.badges.twitter100K` etc. — follower-scale badges
  - per-service: `tags[]` (fandom/genre — artist evidence + facet
    potential), `containsMatureContent` + `contentWarnings{}` (nsfw
    platform flags), `basePrice`/`currency`, `artistReviewStats`
    (totalReviews/averageRating — the prominence/quality signal)
- **Rosters**: `/category/…` listing pages (from
  `sitemap-searchCategories-1.xml`, e.g. `/category/character-illustrations`
  and per-subject/style variants) are server-rendered, cursor-paginated
  20 services/page with full user objects inline. Sitemaps also enumerate
  **all 358,332 users** (`sitemap-users-1..8.xml`, 50k/shard) — mostly
  clients, so don't fetch blind; use category listings + a
  totalReviews>=N threshold, or the services sitemaps (active offerings
  only).
- **Scale**: thousands of active commission artists; ~1s/page pacing means
  a 2k-artist harvest is an overnight run. Free.
- **Notes**: we already hold 168 vgen accounts from bio links —
  cross-hydration rule applies on landing.

## Itaku — itaku.ee (BUILD SECOND: aligned + open API)

- **Access**: robots allows `*` (only AI-training bots blocked). **Open
  public JSON API, no auth**: `itaku.ee/api/galleries/images/?ordering=
  -num_likes` (cursor-paginated), `api/user_profiles/{username}/`.
- **Alignment**: Itaku requires AI-generated work to be tagged and the
  community is explicitly anti-gen-AI — culturally the closest platform to
  the project's mission after Cara.
- **Per-profile data**: `num_followers`, `country`, per-user `tags[]`
  (with maturity ratings — nsfw evidence), `user_sites[]` (**registered
  external links**), `num_commissions`, badges. Per-image: tags +
  `maturity_rating` (SFW/Questionable/NSFW).
- **Rosters**: images ordered by likes (all-time or date-filtered) →
  distinct owners; commissions marketplace endpoints exist too.
- **Scale**: tens of thousands of users; API is fast JSON. Needs a new
  `itaku` platform row + `_LINK_PATTERNS` entry (itaku.ee/profile/… URLs
  already appear in bios).

## Misskey / Fediverse — 598 accounts already in DB (FREE ENRICHMENT)

- **Access**: per-instance open API. Verified on misskey.io:
  `POST /api/users/show {"username": …}` returns full profile unauthenticated
  — name, avatar, and (detail view) `description` + `fields[]` (the profile
  link table → profile_field edges). Even surfaces a Skeb verification badge
  role on misskey.io.
- **Use**: cross-hydration of the 598 misskey accounts we already hold =
  free bios + registered links; no discovery harvest needed to justify it.
  Discovery later via instance channels/hashtag timelines (misskey.io art
  channels; pawoo.net is the pixiv-adjacent Mastodon instance — same
  pattern via the Mastodon public API).

## Tumblr — LANDED 2026-07-23 (OFFICIAL API, enrichment-only)

- **Status: DONE.** User registered the free API key (tumblr.com/oauth/apps);
  `discover_tumblr --hydrate-known` drained the full held backlog (~2,470
  blogs hydrated). Enrichment-only, exactly as scouted — no roster facet.
- **Access**: official API v2. `GET /v2/blog/{name}/info?api_key=…` returns
  the blog description (→ bio links), title, avatar, and whether it's adult-
  flagged.
- **Limits**: default keys are rate-limited (~300 req/min, ~5k/day) — the
  backfill fits in a day, refreshes are cheap.
- **Discovery**: weak (no popularity sort on /tagged since 2018) — tumblr
  stays enrichment of existing accounts, never a roster source. Held rows
  reappear as new bios reference new blogs; a routine `--hydrate-known`
  each round keeps them current.

## Cara — cara.app (BLOCKED, WATCH)

- **Status**: Cloudflare-walled site-wide — `/`, profile pages, and
  `/api/*` all 403 to non-browser clients. Same class as ArtStation:
  **we do not circumvent bot protection.** (Ironic: their Content-Signal
  grants `use=reference`, but the wall doesn't distinguish.)
- **Watch for**: Cara has publicly discussed an official API; the moment
  one exists it's the most mission-aligned source on this list (the
  platform exists because of no-AI attestation). Until then: cara accounts
  stay bio-link edges + display; organic Wayback snapshots could be
  spot-checked like the artstation `--wayback-enrich` lottery.

## Instagram — 1,936 accounts in DB (POLICY-LOCKED, PERMANENT)

- Meta's Graph API only serves data for business accounts you own; oEmbed
  needs app review and returns no profile data. There is **no compliant
  third-party harvest**, which is why the project rule says display_only.
  Nothing to build; the 1,936 handles remain display chips.

## Mihuashi — mihuashi.com (SIGNED API, NO-GO FOR HARVEST)

- **Status**: the site is a client-rendered SPA; **every JSON endpoint we
  probed — including plain user GETs — rejects unsigned requests**
  (`{"error":"signature invalid","msg":"签名错误"}`). Only
  `/api/v1/users/search` answered, and it's name-search over all roles,
  not a painter roster. Reproducing the client's request signing is
  reverse engineering = out by project rules.
- **Do instead**: add `mihuashi` as a **display-only platform** (like
  weibo/facebook) with a `_LINK_PATTERNS` entry — CN artists link 米画师
  stalls (`mihuashi.com/stalls/{id}`) and profiles (`/profiles/{id}`) in
  bios, and right now those parse as junk `website` rows. Zero fetching,
  pure edge/display value for the zh cohort (146 artists).

## Quick verdicts on the rest

- **Xfolio (xfolio.jp)** — 93 accounts in DB. **NO-GO (probed live
  2026-07-22)**: robots `*` looks permissive, but every portfolio page
  303s fresh clients to `/system/recaptcha?creator_code=…` regardless of
  UA (plain and full browser headers both bounce) — a sitewide CAPTCHA
  wall we never circumvent. Wayback is no escape hatch: the archive's own
  crawls got the same 303s, and the few archived 200s are client-rendered
  shells (generic og:title "トップページ - Portfolio", zero external
  links, no profile data). Stays a display/link platform like instagram;
  revisit only if xfolio ships an API or drops the wall.
- **FurAffinity** — robots allows `*` with Crawl-delay 1 (only AI bots
  blocked), fully server-rendered profiles with contact fields. But their
  ToS has historically banned scraping — resolve that contradiction before
  building; niche (furry) skew.
- **Fantia (fantia.jp)** — robots **disallows `/profiles/*`** (the bio
  pages). Fanclub top pages are technically allowed but low-yield; skip.
- **Newgrounds** — robots.txt itself returns 403 (can't even read the
  policy) and there's no official API; skip.
- **Lofter (CN)** — unprobed; tag pages are reportedly server-rendered.
  Next scout if the zh cohort becomes a priority.

## Priority order (roster value × alignment × effort)

VGen, Misskey and Tumblr all LANDED (2026-07-23). Remaining:

1. **Skeb sliced harvest** — escape the 1,200-row Algolia page cap with
   `numericFilters` on `received_works_count` (≥100 ≈ 4.5k creators); paid
   Twitter follow-on, so gate on real X credit. See `[[skeb-algolia-headroom]]`.
2. **VGen sitemap-scale re-walk** — the ~124k permitted sitemap artist URLs
   are mostly untouched; harvest with a `totalReviews` floor.
3. **Itaku** — full worker on the public API; aligned community, rich tags.
   (Currently skipped by user directive — revisit if reprioritized.)
4. **Mihuashi display-only platform row** — one migration + pattern, no
   fetching.
6. Cara: wait for the official API. Instagram: permanently display-only.
