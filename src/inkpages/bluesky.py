"""Minimal client for the public Bluesky AppView (unauthenticated, free)."""
from collections import Counter

import httpx

APPVIEW = "https://public.api.bsky.app/xrpc"


class Bluesky:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self._client = httpx.Client(
            timeout=30,
            headers={"User-Agent": "inkpages/0.1 (no-AI artist directory)"},
        )

    def _get(self, method: str, **params) -> dict:
        self.calls[method] += 1
        resp = self._client.get(
            f"{APPVIEW}/{method}",
            params={k: v for k, v in params.items() if v is not None},
        )
        resp.raise_for_status()
        return resp.json()

    def popular_feeds(self, query: str, limit: int = 10) -> list[dict]:
        data = self._get("app.bsky.unspecced.getPopularFeedGenerators",
                         query=query, limit=limit)
        return data.get("feeds", [])

    def feed_authors(self, feed_uri: str, max_posts: int = 100) -> list[dict]:
        """Post authors from a feed generator, deduped, in feed order."""
        authors: dict[str, dict] = {}
        cursor = None
        fetched = 0
        while fetched < max_posts:
            page = self._get("app.bsky.feed.getFeed", feed=feed_uri,
                             limit=min(100, max_posts - fetched), cursor=cursor)
            items = page.get("feed", [])
            if not items:
                break
            fetched += len(items)
            for item in items:
                author = item.get("post", {}).get("author") or {}
                if did := author.get("did"):
                    authors.setdefault(did, author)
            cursor = page.get("cursor")
            if not cursor:
                break
        return list(authors.values())

    def resolve_handle(self, handle: str) -> str:
        return self._get("com.atproto.identity.resolveHandle", handle=handle)["did"]

    def starter_pack_members(self, uri: str) -> list[dict]:
        """Members of a starter pack; accepts at:// URIs or bsky.app URLs."""
        if uri.startswith("https://"):
            # https://bsky.app/starter-pack/{handle_or_did}/{rkey}
            parts = uri.rstrip("/").split("/")
            actor, rkey = parts[-2], parts[-1]
            did = actor if actor.startswith("did:") else self.resolve_handle(actor)
            uri = f"at://{did}/app.bsky.graph.starterpack/{rkey}"
        pack = self._get("app.bsky.graph.getStarterPack", starterPack=uri)["starterPack"]
        list_uri = (pack.get("list") or {}).get("uri")
        return self.list_members(list_uri) if list_uri else []

    def list_members(self, list_uri: str) -> list[dict]:
        members: dict[str, dict] = {}
        cursor = None
        while True:
            page = self._get("app.bsky.graph.getList", list=list_uri,
                             limit=100, cursor=cursor)
            for item in page.get("items", []):
                subject = item.get("subject") or {}
                if did := subject.get("did"):
                    members.setdefault(did, subject)
            cursor = page.get("cursor")
            if not cursor or not page.get("items"):
                break
        return list(members.values())

    def last_post_time(self, did: str) -> str | None:
        """indexedAt of the newest post or repost on the author's feed."""
        try:
            page = self._get("app.bsky.feed.getAuthorFeed", actor=did, limit=1)
        except httpx.HTTPStatusError:
            return None
        items = page.get("feed", [])
        if not items:
            return None
        item = items[0]
        reason = item.get("reason") or {}
        return reason.get("indexedAt") or item.get("post", {}).get("indexedAt")

    def get_profile(self, actor: str) -> dict:
        return self._get("app.bsky.actor.getProfile", actor=actor)

    def get_profiles(self, dids: list[str]) -> list[dict]:
        profiles = []
        for i in range(0, len(dids), 25):
            batch = dids[i:i + 25]
            profiles += self._get("app.bsky.actor.getProfiles",
                                  actors=batch).get("profiles", [])
        return profiles
