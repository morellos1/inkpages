# inkpages x-tag

Chrome extension: one-click tagging of artist profiles on X into the
[inkpages](../) directory. Tagging is free (it queues a handle-only account
with `discovered_via='manual_tag'`); the popup's **Hydrate now** button runs
the paid `users/by` fetch (~1¢/profile, exact cost shown on the button) and
optionally a pipeline pass so the new artists actually list.

## Setup

1. Build: `npm install && npm run build` (watch mode: `npm run watch`).
2. Load: `chrome://extensions` → Developer mode → **Load unpacked** → select
   the `xtag/extension/` directory.
3. Connect: start the review UI (`uv run python -m inkpages.review_ui`) — it
   prints the x-tag API token (also in `.env` as `INKPAGES_TAG_TOKEN`).
   Open the extension popup, paste the token, **Save & test**.

## What you get on x.com

- **Badges** next to names everywhere: green `INK` = in the directory,
  amber `INK…` = tagged/awaiting hydration, gray `✕` = removed.
- **Hover card button**: hover any name → tag/untag without visiting the
  profile.
- **Profile header button** next to Follow/More.
- **Follower/following lists**: checkbox per row + a bottom bar with
  Select all / Add to inkpages / Remove. Selection is keyed by handle, so it
  survives scrolling through virtualized rows and accumulates across
  "Select all" clicks.

## Removal semantics

- queued, never hydrated → the account row is deleted outright.
- known but unlisted → account hidden (data kept).
- **listed artist → the artist is suppressed** (whole artist leaves the
  directory; re-discovery can never re-add it; reversible from the review
  UI's Removed page). The button asks for confirmation first.

Suppressed artists are never silently re-added by tagging — the extension
tells you to lift the suppression in the review UI instead.
