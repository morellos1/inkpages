"""X (Twitter) official pay-per-use API client + shared processing.

Hard rules: official API only, never scrapers; every paid call is ledgered in
api_usage BEFORE results are used; a hard budget cap is checked before any
spend. Pricing assumptions (cents): post read 0.5, user read 1.0.
"""
import sys
import time
from datetime import datetime, timezone

import httpx

from . import db
from .extract import (find_attestations, find_commission_status, find_email,
                      find_mentions, find_nsfw_flags, find_platform_links,
                      find_website_links)

BASE = "https://api.x.com/2"
POST_READ_CENTS = 0.5
USER_READ_CENTS = 1.0
DEFAULT_CAP_CENTS = 10_000  # $100 — override with X_SPEND_CAP_CENTS

USER_FIELDS = ("created_at,description,entities,location,profile_image_url,"
               "public_metrics,protected,url,most_recent_tweet_id")

TWEPOCH_MS = 1288834974657


def snowflake_time(tweet_id: str | None) -> datetime | None:
    if not tweet_id:
        return None
    try:
        ms = (int(tweet_id) >> 22) + TWEPOCH_MS
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, OverflowError):
        return None


def spend_cap_cents() -> int:
    return int(db.env_var("X_SPEND_CAP_CENTS") or DEFAULT_CAP_CENTS)


def spent_cents(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("select coalesce(sum(est_cost_cents), 0) from api_usage where service = 'x_api'")
        return cur.fetchone()[0]


def ensure_budget(conn, planned_cents: float) -> None:
    cap, spent = spend_cap_cents(), spent_cents(conn)
    if spent + planned_cents > cap:
        sys.exit(f"budget guard: spent {spent}c + planned {planned_cents:.0f}c "
                 f"exceeds cap {cap}c — aborting before any paid call")
    print(f"budget: {spent}c spent, {planned_cents:.0f}c planned, {cap}c cap")


class XApi:
    def __init__(self) -> None:
        bearer = db.env_var("X_API_BEARER_TOKEN")
        if not bearer:
            sys.exit("X_API_BEARER_TOKEN not set")
        self._client = httpx.Client(
            timeout=30,
            headers={"Authorization": f"Bearer {bearer}",
                     "User-Agent": "inkpages/0.1 (no-AI artist directory)"},
        )

    def _get(self, path: str, **params) -> dict:
        for attempt in (1, 2):
            resp = self._client.get(f"{BASE}/{path}", params=params)
            if resp.status_code == 429 and attempt == 1:
                wait = int(resp.headers.get("x-rate-limit-reset", 0)) - int(time.time())
                time.sleep(min(max(wait, 5), 60))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")

    def search_recent(self, query: str, max_posts: int):
        """Returns (posts_read, users_by_id). Author profiles arrive via the
        expansion at no extra read cost."""
        posts_read = 0
        users: dict[str, dict] = {}
        next_token = None
        while posts_read < max_posts:
            params = {
                "query": query,
                "max_results": min(100, max(10, max_posts - posts_read)),
                "tweet.fields": "author_id,created_at",
                "expansions": "author_id",
                "user.fields": USER_FIELDS,
            }
            if next_token:
                params["next_token"] = next_token
            page = self._get("tweets/search/recent", **params)
            data = page.get("data", [])
            posts_read += len(data)
            for user in page.get("includes", {}).get("users", []):
                users.setdefault(user["id"], user)
            next_token = page.get("meta", {}).get("next_token")
            if not data or not next_token:
                break
        return posts_read, users

    def users_by(self, usernames: list[str]):
        """Returns (found_users, missing_usernames)."""
        found, missing = [], []
        for i in range(0, len(usernames), 100):
            batch = usernames[i:i + 100]
            page = self._get("users/by", usernames=",".join(batch),
                             **{"user.fields": USER_FIELDS})
            found += page.get("data", [])
            for err in page.get("errors", []):
                if err.get("parameter") == "usernames":
                    missing.append(err.get("value"))
        return found, missing

    def users_by_ids(self, ids: list[str]):
        """Refresh by stable numeric id — survives handle renames."""
        found, missing = [], []
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            page = self._get("users", ids=",".join(batch),
                             **{"user.fields": USER_FIELDS})
            found += page.get("data", [])
            for err in page.get("errors", []):
                if err.get("parameter") == "ids":
                    missing.append(err.get("value"))
        return found, missing


def expanded_urls(user: dict) -> list[str]:
    urls = []
    entities = user.get("entities") or {}
    for section in ("url", "description"):
        for item in (entities.get(section) or {}).get("urls", []):
            if url := item.get("expanded_url"):
                urls.append(url)
    return urls


def process_user(conn, platforms: dict, user: dict, via: str, details: dict,
                 stats) -> None:
    handle = user["username"]
    bio = user.get("description") or ""
    metrics = user.get("public_metrics") or {}

    account_id = db.get_or_create_account(
        conn, platforms["twitter"],
        native_id=str(user["id"]),
        handle=handle,
        display_name=user.get("name"),
        profile_url=f"https://x.com/{handle}",
        status="active",
        followers_count=metrics.get("followers_count"),
        discovered_via=via,
        discovery_details=details,
        hydrated=True,
        last_post_at=snowflake_time(user.get("most_recent_tweet_id")),
    )
    stats["accounts"] += 1

    if db.is_suppressed(conn, account_id):
        stats["skipped_suppressed"] += 1
        return

    snapshot_id = db.insert_snapshot(
        conn, account_id, bio_text=bio, display_name=user.get("name"),
        followers_count=metrics.get("followers_count"),
        following_count=metrics.get("following_count"),
        raw=user, fetch_source="x:" + via,
    )
    stats["snapshots"] += 1

    db.set_avatar(conn, account_id, user.get("profile_image_url"))
    db.set_contact_email(conn, account_id, find_email(
        "\n".join(filter(None, [bio, user.get("location")]))))
    comm = find_commission_status(
        "\n".join(filter(None, [bio, user.get("name"), user.get("location")])))
    db.set_commission(conn, account_id, comm, None)
    if comm:
        stats["commission_signals"] += 1

    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        stats["attestations"] += 1
    for signal, matched in find_nsfw_flags(bio):
        db.upsert_content_flag(conn, account_id, "nsfw", signal, matched, snapshot_id)
        stats["nsfw_flags"] += 1

    # Links: t.co expansion is free in entities — parse expanded URLs plus the
    # raw bio text and the location field (artists park links there too).
    link_text = "\n".join([bio, user.get("location") or ""] + expanded_urls(user))
    for link in find_platform_links(link_text) + find_website_links(link_text):
        platform_id = platforms.get(link.platform)
        if platform_id is None:
            continue
        if link.platform == "twitter" and link.handle and link.handle.lower() == handle.lower():
            continue
        target_id = db.get_or_create_account(
            conn, platform_id, native_id=link.native_id,
            handle=link.handle or link.native_id, profile_url=link.url,
            discovered_via="bio_link",
            discovery_details={"source_account_id": account_id},
        )
        db.upsert_edge(conn, account_id, target_id, evidence_type="bio_link",
                       evidence_snapshot_id=snapshot_id, evidence_url=link.url,
                       matched_text=None,
                       claim="related" if link.platform == "website" else "same_person",
                       relation_hint="website" if link.platform == "website" else None)
        stats["edges"] += 1

    for mention in find_mentions(bio, "twitter"):
        if mention.handle.lower() == handle.lower():
            continue
        target_id = db.get_or_create_account(
            conn, platforms["twitter"], handle=mention.handle,
            profile_url=f"https://x.com/{mention.handle}",
            discovered_via="bio_mention",
            discovery_details={"source_account_id": account_id},
        )
        db.upsert_edge(conn, account_id, target_id, evidence_type="bio_mention",
                       evidence_snapshot_id=snapshot_id, evidence_url=None,
                       matched_text=mention.matched_text,
                       claim=mention.claim, relation_hint=mention.relation_hint)
        stats[f"mentions_{mention.claim}"] += 1
