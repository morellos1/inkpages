"""Bio parsing: no-AI attestation signals and cross-platform profile links.

Pure functions of snapshot text, so extraction can always re-run over stored
account_snapshots when patterns improve.
"""
import re
from dataclasses import dataclass

# --- attestation signals -------------------------------------------------

_NOAI_HASHTAG = re.compile(r"#no[_-]?(?:gen)?[_-]?ai\w*", re.IGNORECASE)
_JP_PHRASES = [
    "無断AI学習禁止",
    "AI学習禁止",
    "AI使用禁止",
    "AI生成禁止",
    "生成AI禁止",
    "生成AI不使用",
    "AI学習・利用禁止",
]
# Word-boundary match trades some precision (pottery "glaze") for recall; an
# attestation is only ever displayed as the artist's own words, so a rare
# false positive is visible and correctable, not defamatory.
_GLAZE = re.compile(r"\bglazed?\b", re.IGNORECASE)
_NIGHTSHADE = re.compile(r"\bnightshaded?\b", re.IGNORECASE)


def find_attestations(text: str | None) -> list[tuple[str, str]]:
    """Return (signal, matched_text) pairs found in a bio."""
    if not text:
        return []
    found: list[tuple[str, str]] = []
    for m in _NOAI_HASHTAG.finditer(text):
        found.append(("bio_tag", m.group(0)))
    for phrase in _JP_PHRASES:
        if phrase in text:
            found.append(("bio_tag", phrase))
    if m := _GLAZE.search(text):
        found.append(("glaze_mention", m.group(0)))
    if m := _NIGHTSHADE.search(text):
        found.append(("nightshade_mention", m.group(0)))
    return list(dict.fromkeys(found))


# --- NSFW / 18+ content flags ---------------------------------------------

_NSFW_MARKERS = [
    re.compile(r"🔞"),
    re.compile(r"\br-?18g?\b", re.IGNORECASE),
    re.compile(r"\b18\+"),
    re.compile(r"\bnsfw\b", re.IGNORECASE),
    re.compile(r"\bmdni\b|minors\s+dni", re.IGNORECASE),
]
# "no NSFW" / "SFW only" / "non-NSFW" must not flag the account. Cheap
# negation guard on the preceding few characters; imprecise by design — the
# flag is displayed as the artist's own words and easy to correct.
_NEGATED = re.compile(r"(?:\bno\b|\bnon|\bnot\b|sfw[\s-]*only)[\s:,-]*$", re.IGNORECASE)


def find_nsfw_flags(text: str | None) -> list[tuple[str, str]]:
    """Return ('bio_marker', matched_text) pairs for 18+ self-labels in a bio."""
    if not text:
        return []
    found: list[tuple[str, str]] = []
    for pattern in _NSFW_MARKERS:
        for m in pattern.finditer(text):
            if _NEGATED.search(text[max(0, m.start() - 12):m.start()]):
                continue
            found.append(("bio_marker", m.group(0)))
    return list(dict.fromkeys(found))


# Bluesky self-labels an account can declare on its own profile record.
BSKY_NSFW_SELF_LABELS = {"porn", "sexual", "nudity"}


# --- cross-platform profile links ----------------------------------------

@dataclass(frozen=True)
class PlatformLink:
    platform: str
    handle: str | None
    native_id: str | None
    url: str


# (platform slug, pattern). Patterns capture 'handle' or 'native_id'.
_LINK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("twitter", re.compile(
        r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/@?"
        r"(?!i/|home\b|search\b|hashtag/|intent/|share\b)"
        r"(?P<handle>[A-Za-z0-9_]{1,15})\b", re.I)),
    ("bluesky", re.compile(r"bsky\.app/profile/(?P<native_id>did:(?:plc|web):[A-Za-z0-9._%:-]+)", re.I)),
    ("bluesky", re.compile(r"bsky\.app/profile/(?!did:)(?P<handle>[A-Za-z0-9.-]+)", re.I)),
    ("pixiv", re.compile(r"pixiv\.net/(?:en/)?(?:users/|member\.php\?id=)(?P<native_id>\d+)", re.I)),
    ("skeb", re.compile(r"skeb\.jp/@(?P<handle>[\w.-]+)", re.I)),
    ("artstation", re.compile(r"artstation\.com/(?!marketplace\b|learning\b)(?P<handle>[A-Za-z0-9_-]+)", re.I)),
    ("patreon", re.compile(r"patreon\.com/(?:c/)?(?!posts\b|join\b|checkout\b)(?P<handle>[A-Za-z0-9_-]+)", re.I)),
    ("kofi", re.compile(r"ko-fi\.com/(?P<handle>[A-Za-z0-9_]+)", re.I)),
    ("vgen", re.compile(r"vgen\.co/(?P<handle>[A-Za-z0-9_-]+)", re.I)),
    ("cara", re.compile(r"cara\.app/(?P<handle>[A-Za-z0-9._-]+)", re.I)),
    ("xfolio", re.compile(r"xfolio\.jp/(?:en/)?portfolio/(?P<handle>[\w-]+)", re.I)),
    ("deviantart", re.compile(r"deviantart\.com/(?!tag\b|art\b)(?P<handle>[A-Za-z0-9-]+)", re.I)),
    ("deviantart", re.compile(r"\b(?P<handle>[A-Za-z0-9-]+)\.deviantart\.com", re.I)),
    ("tumblr", re.compile(r"\b(?P<handle>[A-Za-z0-9-]+)\.tumblr\.com", re.I)),
    ("tumblr", re.compile(r"tumblr\.com/(?!tagged\b|search\b)(?P<handle>[A-Za-z0-9-]+)", re.I)),
    ("gumroad", re.compile(r"\b(?P<handle>[A-Za-z0-9]+)\.gumroad\.com", re.I)),
    ("inprnt", re.compile(r"inprnt\.com/gallery/(?P<handle>\w+)", re.I)),
    ("instagram", re.compile(r"instagram\.com/(?!p/|reel/|explore\b)(?P<handle>[A-Za-z0-9._]{1,30})", re.I)),
    ("linktree", re.compile(r"linktr\.ee/(?P<handle>[\w.]+)", re.I)),
    ("carrd", re.compile(r"\b(?P<handle>[A-Za-z0-9-]+)\.carrd\.co", re.I)),
    ("potofu", re.compile(r"potofu\.me/(?P<handle>[\w.-]+)", re.I)),
    ("litlink", re.compile(r"lit\.link/(?:en/|ja/)?(?P<handle>\w+)", re.I)),
]

_SUBDOMAIN_JUNK = {"www", "blog", "shop", "app", "help", "about", "support"}


def find_platform_links(text: str | None) -> list[PlatformLink]:
    if not text:
        return []
    seen: dict[tuple, PlatformLink] = {}
    for platform, pattern in _LINK_PATTERNS:
        for m in pattern.finditer(text):
            groups = m.groupdict()
            handle = groups.get("handle")
            native_id = groups.get("native_id")
            if handle and handle.lower() in _SUBDOMAIN_JUNK:
                continue
            url = m.group(0)
            if not url.startswith("http"):
                url = "https://" + url.lstrip("/")
            key = (platform, (handle or "").lower(), native_id)
            seen.setdefault(key, PlatformLink(platform, handle, native_id, url))
    return list(seen.values())
