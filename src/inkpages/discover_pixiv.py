"""Pixiv discovery worker (free).

Two jobs:
1. Hydrate pixiv accounts we already reference (Skeb profile fields, bios,
   hubs) via the public `ajax/user/{id}?full=1` endpoint — bio, registered
   social links (user-entered profile fields), webpage, avatar, JP-region
   signal, commission acceptance.
2. Discover new artists from the public SFW illust rankings
   (`ranking.php?format=json`, 50/page) — discovered_via='pixiv_ranking'
   (roster source), rank kept as an auxiliary signal.

Pixiv bios are dense with AI学習禁止-style attestations; the social block and
bio links cross-link to Twitter for clustering. R18 rankings need auth and
are skipped. All calls free and ledgered.

Usage:
  uv run python -m inkpages.discover_pixiv --hydrate-known --rank-pages 6
"""
import argparse
import time
from collections import Counter

import httpx

from . import db
from .extract import (find_attestations, find_commission_status, find_email,
                      find_nsfw_flags, find_platform_links, find_website_links)

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.pixiv.net/",
    "Accept": "application/json",
}
RANK_MODES = ("weekly", "monthly", "original")
# R18 ranking pages require an authenticated pixiv session (PIXIV_SESSION cookie)
# with "display R-18 works" enabled on the account.
R18_RANK_MODES = ("weekly_r18", "monthly_r18")

# pixiv's registered social block -> our platform slugs
SOCIAL_PLATFORMS = {"twitter": "twitter", "instagram": "instagram"}


class Pixiv:
    def __init__(self, session: str | None = None) -> None:
        self.calls: Counter = Counter()
        headers = dict(UA)
        if session:
            headers["Cookie"] = f"PHPSESSID={session}"
        self._client = httpx.Client(timeout=25, headers=headers)

    def _get(self, url: str, **params):
        for attempt in (1, 2):
            self.calls["requests"] += 1
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError:
                return None
            if resp.status_code == 429 and attempt == 1:
                time.sleep(20)
                continue
            if resp.status_code != 200:
                return None
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    def user(self, user_id: str) -> dict | None:
        data = self._get(f"https://www.pixiv.net/ajax/user/{user_id}",
                         full=1, lang="ja")
        if data is None:
            return None
        if data.get("error"):
            return {"_deleted": True}
        return data.get("body")

    def ranking_user_ids(self, mode: str, pages: int) -> dict[str, int]:
        """user_id -> best rank for a mode."""
        found: dict[str, int] = {}
        for page in range(1, pages + 1):
            data = self._get("https://www.pixiv.net/ranking.php",
                             mode=mode, content="illust", format="json", p=page)
            if not data or not data.get("contents"):
                break
            for item in data["contents"]:
                uid = str(item.get("user_id"))
                rank = item.get("rank") or 999
                if uid not in found or rank < found[uid]:
                    found[uid] = rank
            time.sleep(0.5)
        return found


def process_user(conn, platforms, pixiv: Pixiv, user_id: str, via: str,
                 details: dict, stats: Counter) -> None:
    body = pixiv.user(user_id)
    if body is None:
        stats["fetch_failed"] += 1
        return
    if body.get("_deleted"):
        with conn.cursor() as cur:
            cur.execute(
                """update accounts set status = 'deleted', last_hydrated = now()
                   where platform_id = %s and native_id = %s""",
                (platforms["pixiv"], user_id),
            )
        stats["deleted"] += 1
        return

    bio = body.get("comment") or ""
    account_id = db.get_or_create_account(
        conn, platforms["pixiv"],
        native_id=user_id,
        handle=user_id,
        display_name=body.get("name"),
        profile_url=f"https://www.pixiv.net/users/{user_id}",
        status="active",
        discovered_via=via,
        discovery_details=details,
        hydrated=True,
    )
    stats["accounts"] += 1
    if db.is_suppressed(conn, account_id):
        stats["skipped_suppressed"] += 1
        return

    raw = {k: body.get(k) for k in
           ("userId", "name", "comment", "webpage", "social", "region",
            "commission", "official", "premium", "imageBig")}
    snapshot_id = db.insert_snapshot(
        conn, account_id, bio_text=bio, display_name=body.get("name"),
        followers_count=None, following_count=body.get("following"),
        raw=raw, fetch_source="pixiv:ajax/user",
    )
    stats["snapshots"] += 1

    db.set_avatar(conn, account_id, body.get("imageBig"))
    db.set_contact_email(conn, account_id, find_email(bio))
    region = (body.get("region") or {}).get("region")
    db.set_platform_stats(conn, account_id, {
        "region": region,
        "official": body.get("official"),
        "premium": body.get("premium"),
    })

    commission = body.get("commission") or {}
    if commission.get("acceptRequest") is True:
        db.set_commission(conn, account_id, ("open", 0.9, "pixiv:accept_request"), None)
    elif comm := find_commission_status(bio):
        db.set_commission(conn, account_id, comm, None)

    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        stats["attestations"] += 1
    for signal, matched in find_nsfw_flags(bio):
        db.upsert_content_flag(conn, account_id, "nsfw", signal, matched, snapshot_id)
        stats["nsfw_flags"] += 1
    # Surfacing via an R18 ranking is itself a platform nsfw signal.
    if details.get("r18"):
        db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                               "pixiv:r18_ranking", snapshot_id)
        stats["nsfw_flags"] += 1

    emitted: set[int] = set()

    def emit(platform, native_id, handle, url, evidence_type, claim, hint):
        platform_id = platforms.get(platform)
        if platform_id is None or not (handle or native_id):
            return
        target_id = db.get_or_create_account(
            conn, platform_id, native_id=native_id,
            handle=handle or native_id, profile_url=url,
            discovered_via="bio_link",
            discovery_details={"source_account_id": account_id, "via": "pixiv_profile"},
        )
        if target_id in emitted or target_id == account_id:
            return
        emitted.add(target_id)
        db.upsert_edge(conn, account_id, target_id,
                       evidence_type=evidence_type, evidence_snapshot_id=snapshot_id,
                       evidence_url=url, matched_text=None,
                       claim=claim, relation_hint=hint)
        stats[f"edges_{claim}"] += 1

    # Registered social block: user-entered profile fields (no oauth
    # exemptions — normal clustering rules apply).
    for key, platform in SOCIAL_PLATFORMS.items():
        if url := ((body.get("social") or {}).get(key) or {}).get("url"):
            for found in find_platform_links(url):
                emit(found.platform, found.native_id, found.handle, found.url,
                     "profile_field", "same_person", None)
    if webpage := body.get("webpage"):
        for found in find_platform_links(webpage):
            emit(found.platform, found.native_id, found.handle, found.url,
                 "profile_field", "same_person", None)
        for found in find_website_links(webpage):
            emit(found.platform, found.native_id, found.handle, found.url,
                 "profile_field", "related", "website")
    for found in find_platform_links(bio):
        emit(found.platform, found.native_id, found.handle, found.url,
             "bio_link", "same_person", None)
    for found in find_website_links(bio):
        emit(found.platform, found.native_id, found.handle, found.url,
             "bio_link", "related", "website")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hydrate-known", action="store_true",
                        help="hydrate already-referenced pixiv accounts")
    parser.add_argument("--rank-pages", type=int, default=6,
                        help="SFW ranking pages per mode (50 users/page)")
    parser.add_argument("--r18-pages", type=int, default=0,
                        help="R18 ranking pages per mode (needs PIXIV_SESSION); "
                             "10 pages ≈ top 500 per mode")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--delay", type=float, default=0.6)
    args = parser.parse_args()

    session = db.env_var("PIXIV_SESSION")
    if args.r18_pages and not session:
        raise SystemExit("--r18-pages needs PIXIV_SESSION set (logged-in PHPSESSID)")

    stats: Counter = Counter()
    pixiv = Pixiv(session=session)
    with db.connect() as conn:
        platforms = db.platform_ids(conn)

        todo: dict[str, tuple[str, dict]] = {}
        if args.rank_pages:
            for mode in RANK_MODES:
                for uid, rank in pixiv.ranking_user_ids(mode, args.rank_pages).items():
                    if uid not in todo:
                        todo[uid] = ("pixiv_ranking", {"mode": mode, "rank": rank})
            print(f"SFW rankings: {len(todo)} unique artists")
        if args.r18_pages:
            for mode in R18_RANK_MODES:
                for uid, rank in pixiv.ranking_user_ids(mode, args.r18_pages).items():
                    # R18 wins the flag even if the uid also charted SFW.
                    todo[uid] = ("pixiv_ranking", {"mode": mode, "rank": rank, "r18": True})
            print(f"with R18 rankings: {len(todo)} unique artists")
        if args.hydrate_known:
            with conn.cursor() as cur:
                cur.execute(
                    """select native_id from accounts
                       where platform_id = %s and native_id is not null
                         and last_hydrated is null and status <> 'deleted'""",
                    (platforms["pixiv"],),
                )
                known = [r[0] for r in cur.fetchall()]
            for uid in known:
                todo.setdefault(uid, ("hydration", {}))
            print(f"with known: {len(todo)} total")

        for n, (uid, (via, details)) in enumerate(list(todo.items())[:args.limit], 1):
            time.sleep(args.delay)
            process_user(conn, platforms, pixiv, uid, via, details, stats)
            if n % 100 == 0:
                conn.commit()
                print(f"  …{n} processed")
        conn.commit()
        db.log_api_usage(conn, "pixiv", "ajax+ranking", pixiv.calls["requests"], 0)
        conn.commit()

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
