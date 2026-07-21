"""Skeb discovery worker (free) — the Eastern-column engine.

Enumerates top art creators from Skeb's public Algolia search index (the same
index and public search-only key the site's own frontend ships), then fetches
each creator's detail JSON from skeb.jp's API. Detail responses carry:

- linked services: twitter_uid (OAuth-verified numeric Twitter id — near-proof
  identity), pixiv/fanbox/fantia/booth/dlsite/nijie/skima/coconala/patreon/
  youtube ids, a personal url, and user_service_links entries;
- native stats: acceptable (authoritative commission status), complete_rate,
  received_works_count, received_nsfw_works_count, nsfw_acceptable.

skeb.jp answers first requests with a 429 challenge page embedding a
request_key cookie; the client extracts it and retries.

Usage: uv run python -m inkpages.discover_skeb --top 1000
"""
import argparse
import re
import time
from collections import Counter

import httpx

from . import db
from .extract import (find_attestations, find_email, find_nsfw_flags,
                      find_platform_links, find_website_links)

ALGOLIA_APP = "HB1JT3KRE9"
ALGOLIA_KEY = "9a4ce7d609e71bf29e977925e4c6740c"  # public search-only key from skeb's frontend
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# skeb detail field -> our platform slug (values are handles unless noted)
ID_FIELDS = {
    "pixiv_id": "pixiv",        # numeric -> native_id
    "fanbox_id": "fanbox",
    "fantia_id": "fantia",      # numeric -> native_id
    "booth_id": "booth",
    "dlsite_id": "dlsite",
    "nijie_id": "nijie",        # numeric -> native_id
    "skima_id": "skima",        # numeric -> native_id
    "coconala_id": "coconala",  # numeric -> native_id
    "patreon_id": "patreon",
    "youtube_id": "youtube",
}
NUMERIC_NATIVE = {"pixiv", "fantia", "nijie", "skima", "coconala"}

# Known shared defaults people leave in Skeb's user-entered service fields —
# never an artist's own account. (The youtube id is DLsite's official channel.)
FIELD_VALUE_BLOCKLIST = {
    ("youtube", "UCQEN3LsNnqottC2mXx3tAjA"),
    ("fanbox", "www"),
}
_FIELD_JUNK = {"www", "http", "https", "none", "null"}

URL_TEMPLATES = {
    "pixiv": "https://www.pixiv.net/users/{}",
    "fanbox": "https://{}.fanbox.cc",
    "fantia": "https://fantia.jp/fanclubs/{}",
    "booth": "https://{}.booth.pm",
    "nijie": "https://nijie.info/members.php?id={}",
    "skima": "https://skima.jp/profile?id={}",
    "coconala": "https://coconala.com/users/{}",
    "patreon": "https://www.patreon.com/{}",
}

STAT_KEYS = ("received_works_count", "received_nsfw_works_count",
             "complete_rate", "acceptable", "busy", "nsfw_acceptable")

_REQUEST_KEY = re.compile(r'request_key=([^;"]+)')


class SkebClient:
    def __init__(self) -> None:
        self.calls: Counter = Counter()
        self._client = httpx.Client(
            timeout=30,
            headers={"User-Agent": UA, "Accept": "application/json",
                     "Authorization": "Bearer null"},
        )

    def _skeb_get(self, path: str) -> dict | list | None:
        for attempt in (1, 2, 3):
            self.calls["skeb_api"] += 1
            resp = self._client.get(f"https://skeb.jp{path}")
            if resp.status_code == 429:
                if m := _REQUEST_KEY.search(resp.text):
                    self._client.cookies.set("request_key", m.group(1), domain="skeb.jp")
                    continue
                time.sleep(8 * attempt)
                continue
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        return None

    def top_creators(self, genre: str, top: int) -> list[dict]:
        """Ranked creators from the public Algolia index (max ~1000)."""
        hits: list[dict] = []
        page = 0
        while len(hits) < top:
            self.calls["algolia"] += 1
            resp = self._client.post(
                f"https://{ALGOLIA_APP}-dsn.algolia.net/1/indexes/User/query",
                headers={"X-Algolia-Application-Id": ALGOLIA_APP,
                         "X-Algolia-API-Key": ALGOLIA_KEY},
                json={"query": "", "hitsPerPage": 100, "page": page,
                      "filters": f"creator:true AND genre:{genre}"},
            )
            resp.raise_for_status()
            data = resp.json()
            hits += data.get("hits", [])
            page += 1
            if page >= data.get("nbPages", 0):
                break
        return hits[:top]

    def user_detail(self, screen_name: str) -> dict | None:
        detail = self._skeb_get(f"/api/users/{screen_name}")
        return detail if isinstance(detail, dict) else None


def linked_accounts(detail: dict) -> list[tuple[str, str | None, str | None, str | None]]:
    """(platform, native_id, handle, url) claims from the skeb profile."""
    out = []
    if uid := detail.get("twitter_uid"):
        handle = next((l.get("screen_name") for l in detail.get("user_service_links") or []
                       if l.get("provider") == "twitter"), None)
        # "oauth" marks the one platform-VERIFIED link — the other id fields
        # are user-entered and get no special exemptions at clustering.
        out.append(("twitter", str(uid), handle,
                    f"https://x.com/{handle}" if handle else None, "oauth"))
    for field, platform in ID_FIELDS.items():
        value = detail.get(field)
        if value in (None, "", 0):
            continue
        value = str(value)
        if (len(value) < 2 or value.lower() in _FIELD_JUNK
                or (platform, value) in FIELD_VALUE_BLOCKLIST):
            continue
        url = URL_TEMPLATES.get(platform, "").format(value) or None
        if platform in NUMERIC_NATIVE:
            out.append((platform, value, value, url, None))
        elif platform == "youtube" and value.startswith("UC"):
            out.append((platform, value, value,
                        f"https://www.youtube.com/channel/{value}", None))
        else:
            out.append((platform, None, value, url, None))
    return out


def process_creator(conn, platforms: dict, detail: dict, rank: int, stats: Counter) -> None:
    screen_name = detail["screen_name"]
    bio = detail.get("description") or ""

    account_id = db.get_or_create_account(
        conn, platforms["skeb"],
        native_id=str(detail["id"]),
        handle=screen_name,
        display_name=detail.get("name"),
        profile_url=f"https://skeb.jp/@{screen_name}",
        status="active",
        discovered_via="skeb_ranking",
        discovery_details={"algolia_rank": rank},
        hydrated=True,
    )
    stats["accounts"] += 1
    if db.is_suppressed(conn, account_id):
        stats["skipped_suppressed"] += 1
        return

    snapshot_id = db.insert_snapshot(
        conn, account_id, bio_text=bio, display_name=detail.get("name"),
        followers_count=None, following_count=None,
        raw=detail, fetch_source="skeb:api/users",
    )
    stats["snapshots"] += 1

    db.set_platform_stats(conn, account_id,
                          {k: detail.get(k) for k in STAT_KEYS})
    db.set_avatar(conn, account_id, detail.get("avatar_url"))
    # Skeb's acceptance flag is the platform's own state — authoritative.
    accepting = detail.get("acceptable")
    db.set_commission(conn, account_id,
                      ("open", 0.95, "skeb:acceptable") if accepting
                      else ("closed", 0.95, "skeb:not accepting"), None)
    if detail.get("nsfw_acceptable"):
        db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                               "skeb:nsfw_acceptable", snapshot_id)
        stats["nsfw_flags"] += 1
    db.set_contact_email(conn, account_id, find_email(bio))

    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        stats["attestations"] += 1
    for signal, matched in find_nsfw_flags(bio):
        db.upsert_content_flag(conn, account_id, "nsfw", signal, matched, snapshot_id)
        stats["nsfw_flags"] += 1

    # Platform-registered ids (OAuth twitter_uid, pixiv_id, fanbox_id, …) are
    # the artist's own accounts => profile_field, same_person. Everything else
    # on the profile — bio links, the url field, service-link extras — is
    # weaker: the OAuth link IS their twitter, so any OTHER twitter found here
    # is a project/collab and becomes a related connection; generic websites
    # likewise.
    emitted: set[int] = set()

    def emit(platform, native_id, handle, url, evidence_type, claim, hint):
        platform_id = platforms.get(platform)
        if platform_id is None or not (handle or native_id):
            return
        target_id = db.get_or_create_account(
            conn, platform_id, native_id=native_id,
            handle=handle or native_id, profile_url=url,
            discovered_via="bio_link",
            discovery_details={"source_account_id": account_id, "via": "skeb_profile"},
        )
        if target_id in emitted or target_id == account_id:
            return
        emitted.add(target_id)
        db.upsert_edge(conn, account_id, target_id,
                       evidence_type=evidence_type,
                       evidence_snapshot_id=snapshot_id,
                       evidence_url=url or f"https://skeb.jp/@{screen_name}",
                       matched_text=None, claim=claim, relation_hint=hint)
        stats[f"edges_{claim}"] += 1

    for platform, native_id, handle, url, hint in linked_accounts(detail):
        emit(platform, native_id, handle, url, "profile_field", "same_person", hint)

    weak: list = []
    for link in (detail.get("user_service_links") or []):
        if link.get("provider") == "twitter":
            continue  # handled via twitter_uid
        weak += find_platform_links(link.get("url") or "") + find_website_links(link.get("url") or "")
    for text in filter(None, [detail.get("url"), bio]):
        weak += find_platform_links(text) + find_website_links(text)
    for found in weak:
        if found.platform == "twitter":
            claim, hint = "related", "skeb_secondary"
        elif found.platform == "website":
            claim, hint = "related", "website"
        else:
            claim, hint = "same_person", None
        emit(found.platform, found.native_id, found.handle, found.url,
             "bio_link", claim, hint)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=1000,
                        help="Algolia ranked-creator harvest depth (0 to skip)")
    parser.add_argument("--hydrate-known", action="store_true",
                        help="fetch skeb accounts referenced but never fetched "
                             "(bio-link targets) — pulls their OAuth twitter_uid etc.")
    parser.add_argument("--genre", default="art")
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    stats: Counter = Counter()
    client = SkebClient()
    with db.connect() as conn:
        platforms = db.platform_ids(conn)

        with conn.cursor() as cur:
            cur.execute(
                """select handle::text from accounts
                   where platform_id = %s and last_hydrated > now() - interval '1 day'""",
                (platforms["skeb"],),
            )
            recently_done = {r[0].lower() for r in cur.fetchall()}

        # 1. Referenced-but-unhydrated skeb accounts (e.g. a pixiv/bio link to
        # skeb.jp/@x that we never fetched, so its twitter_uid was never seen).
        if args.hydrate_known:
            with conn.cursor() as cur:
                cur.execute(
                    """select handle::text from accounts
                       where platform_id = %s and last_hydrated is null
                         and status <> 'deleted'""",
                    (platforms["skeb"],),
                )
                known = [r[0] for r in cur.fetchall()]
            print(f"hydrate-known: {len(known)} referenced skeb accounts")
            for i, screen_name in enumerate(known, start=1):
                time.sleep(args.delay)
                detail = client.user_detail(screen_name)
                if detail is None:
                    stats["detail_failed"] += 1
                    continue
                process_creator(conn, platforms, detail, 0, stats)
                if i % 100 == 0:
                    conn.commit()
                    print(f"  …{i} hydrated")
            conn.commit()

        # 2. Top ranked creators from the public Algolia index.
        creators = client.top_creators(args.genre, args.top) if args.top else []
        print(f"algolia: {len(creators)} ranked creators")
        for rank, hit in enumerate(creators, start=1):
            screen_name = hit["screen_name"]
            if screen_name.lower() in recently_done:
                stats["skipped_recent"] += 1
                continue
            time.sleep(args.delay)
            detail = client.user_detail(screen_name)
            if detail is None:
                stats["detail_failed"] += 1
                continue
            process_creator(conn, platforms, detail, rank, stats)
            if rank % 100 == 0:
                conn.commit()
                print(f"  …{rank} processed")
        conn.commit()
        for endpoint, units in sorted(client.calls.items()):
            db.log_api_usage(conn, "skeb", endpoint, units, 0)
        conn.commit()

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
