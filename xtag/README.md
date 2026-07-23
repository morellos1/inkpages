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

Run the review UI in its **own dedicated terminal**, not inside a Claude
Code session — those stop the server to run pipeline commands, so it flaps
up/down and the extension goes stateless every time it's down.

## What you get on x.com

- **Badges** next to names everywhere: green `INK` = in the directory,
  amber `INK…` = tagged/awaiting hydration, gray `✕` = removed.
- **Hover card button**: hover any name → tag/untag without visiting the
  profile (sits immediately left of the Follow button).
- **Profile header button** next to Follow/More.
- **On-post button** in every tweet's action row (next to like/share) — no
  hover needed. Reposts ink the ORIGINAL poster (the reposter only appears
  in X's socialContext line, never as the article's User-Name).
- **Follower/following lists**: checkbox per row + a bottom bar with
  Select all / Add to inkpages / Remove. **Select all auto-scrolls the whole
  list** (X only mounts ~a window of rows, so the scan walks to the end
  harvesting handles, then jumps back; click Stop to end early). Selection is
  keyed by handle, survives reloads (chrome.storage.local), and Add/Remove
  only deselect what the server confirmed.

## Using from a second PC (shared pool)

All state lives in the dev machine's Postgres behind the review UI — the
extension is a thin client, so any number of machines tag into the same
pool. On the second PC:

1. **Get the extension**: copy the `xtag/extension/` folder (with `dist/`
   built) to the PC and Load-unpack it — no repo/node needed there. Rebuild
   + recopy when the extension changes.
2. **Reach the server**, either:
   - **SSH tunnel** (zero config): `ssh -N -L 8322:127.0.0.1:8322 you@devmac`,
     keep base URL `http://127.0.0.1:8322`; or
   - **Tailscale**: put both machines on your tailnet, set
     `INKPAGES_HOST=<tailscale-ip>` in the dev machine's `.env`, restart the
     review UI, and set the popup's base URL to
     `http://<devmac-magicdns-name>.ts.net:8322` (the manifest already has
     `*.ts.net` host permissions).
3. **Token**: paste the same `INKPAGES_TAG_TOKEN` into the popup there.

Never bind `INKPAGES_HOST` to anything internet-reachable — the review UI's
admin routes have CSRF but no login. Tailnet/VPN only.

Concurrent tagging from both machines is safe (idempotent upserts, per-
request DB connections, chunked writes); checkbox selections are per-browser
(chrome.storage.local) and never shared — only the resulting pool is.

## Removal semantics

- queued, never hydrated → the account row is deleted outright.
- known but unlisted → account hidden (data kept).
- **listed artist → the artist is suppressed** (whole artist leaves the
  directory; re-discovery can never re-add it; reversible from the review
  UI's Removed page). The button asks for confirmation first.

Suppressed artists are never silently re-added by tagging — the extension
tells you to lift the suppression in the review UI instead.

## Troubleshooting

**All my tags vanished / everyone shows "+ ink".** The review UI server is
down (or unreachable). The extension holds no state of its own — every badge
and button is derived from a live `/api/x/status` call, so with the server
down the whole page renders untracked. Your tags are safe (they live in
Postgres); start the server and reload the X tab. A *transient* outage (the
server briefly starved mid-pipeline) no longer strips badges: a failed
lookup resolves to an internal `unknown` state that leaves the existing UI
in place and retries on the next scan. A *sustained* outage (server not
running at all) still shows everything as untracked — there's nothing to
preserve on a fresh page.
