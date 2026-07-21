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


# --- shortened links -------------------------------------------------------

_SHORTENER = re.compile(
    r"(?:https?://)?(?:t\.co|bit\.ly|tinyurl\.com|goo\.gl)/[A-Za-z0-9]+", re.I)


def find_short_links(text: str | None) -> list[str]:
    """Opaque shortener URLs (t.co etc.) that need redirect resolution before
    they mean anything."""
    if not text:
        return []
    urls = []
    for m in _SHORTENER.finditer(text):
        url = m.group(0)
        urls.append(url if url.startswith("http") else "https://" + url)
    return list(dict.fromkeys(urls))


# --- contact emails --------------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9][\w.+-]*@[\w-]+\.[\w.-]+[A-Za-z]")


def find_email(text: str | None) -> str | None:
    if not text:
        return None
    m = _EMAIL.search(text)
    return m.group(0).lower() if m else None


# --- commission status -----------------------------------------------------

# (pattern, status, confidence). Confidence reflects how unambiguous the
# phrasing is; the caller applies a source multiplier (bio > hub page text).
_COMMISSION_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"comm?(?:ission)?s?\s*(?:status)?\s*(?:are|:|-|–|—)?\s*open", re.I), "open", 0.9),
    (re.compile(r"open\s+(?:for\s+)?comm?(?:ission)?s", re.I), "open", 0.85),
    (re.compile(r"taking\s+comm?(?:ission)?s", re.I), "open", 0.8),
    (re.compile(r"comm?(?:ission)?s?\s*(?:status)?\s*(?:are|:|-|–|—)?\s*closed?\b", re.I), "closed", 0.9),
    (re.compile(r"closed?\s+(?:for\s+)?comm?(?:ission)?s", re.I), "closed", 0.85),
    (re.compile(r"not\s+taking\s+comm?(?:ission)?s", re.I), "closed", 0.85),
    (re.compile(r"comm?(?:ission)?s?\s*(?::|-|–|—)?\s*waitlist", re.I), "waitlist", 0.8),
    (re.compile(r"依頼\s*受付中|コミッション\s*(?:受付中|募集中)|お仕事(?:依頼)?\s*受付中"), "open", 0.85),
    (re.compile(r"依頼\s*(?:停止中|受付停止|募集停止)|コミッション\s*(?:停止|休止)中?"), "closed", 0.85),
    (re.compile(r"skeb\s*(?:募集中|受付中)", re.I), "open", 0.7),
]


def find_commission_status(text: str | None, multiplier: float = 1.0):
    """Best (status, confidence, matched_text) from text, or None.
    On open/closed conflicts the closed reading wins at equal confidence —
    a stale/wrong 'open' is the harmful direction."""
    if not text:
        return None
    best = None
    for pattern, status, confidence in _COMMISSION_PATTERNS:
        if m := pattern.search(text):
            score = confidence * multiplier
            rank = (score, 1 if status == "closed" else 0)
            if best is None or rank > best[0]:
                best = (rank, (status, round(score, 2), m.group(0).strip()))
    return best[1] if best else None


# --- artist-evidence heuristic ---------------------------------------------

# Used to gate singleton-artist creation for accounts that arrived via open
# harvests (anyone can post a hashtag). Curated rosters are exempt.
_ARTIST_HINTS = re.compile(
    r"illustrat|artist|art\b|draw|paint|sketch|doodle|fanart|commission|comm\b|comms\b"
    r"|oc\b|vtuber|design|animat|pixiv|skeb|vgen|絵|イラスト|絵描き|絵師|落書き|らくがき"
    r"|お絵描き|依頼|創作|同人", re.IGNORECASE)


def looks_like_artist(text: str | None) -> bool:
    return bool(text and _ARTIST_HINTS.search(text))


# --- bio @mentions: alt accounts vs merely-related accounts ---------------

@dataclass(frozen=True)
class Mention:
    handle: str
    claim: str           # 'same_person' | 'related'
    relation_hint: str | None
    matched_text: str    # context window, kept as evidence


# Tokens that mark a mention as the artist's own other account. Modifiers like
# "nsfw"/"🔞" alone are not enough — "nsfw alt" clusters, a bare "nsfw @x"
# does not.
_ALT_TOKENS = ("alt", "main", "side", "backup", "moved", "sub acc", "subacc",
               "2nd acc", "second acc", "other acc", "aka", "旧アカ", "サブ垢",
               "別垢", "本垢", "移転")
_RELATED_TOKENS = ("pfp", "icon", "banner", "header", "art by", "artist",
                   "partner", "bf", "gf", "wife", "husband", "friend",
                   "絵師", "アイコン")

_MENTION_PATTERNS = {
    "twitter": re.compile(r"(?<![\w.@/])@([A-Za-z0-9_]{2,15})\b"),
    # Bluesky handles are domains; only match dotted forms to avoid noise.
    "bluesky": re.compile(r"(?<![\w.@/])@([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)", re.I),
}


def find_mentions(text: str | None, platform: str) -> list[Mention]:
    pattern = _MENTION_PATTERNS.get(platform)
    if not text or pattern is None:
        return []
    found: dict[str, Mention] = {}
    prev_end = 0
    for m in pattern.finditer(text):
        ctx = text[max(prev_end, m.start() - 24):m.end()]
        prev_end = m.end()
        ctx_lower = ctx.lower()
        claim, hint = "related", None
        for token in _ALT_TOKENS:
            if token in ctx_lower:
                claim, hint = "same_person", token
                break
        else:
            for token in _RELATED_TOKENS:
                if token in ctx_lower:
                    hint = token
                    break
        found.setdefault(m.group(1).lower(), Mention(m.group(1), claim, hint, ctx.strip()))
    return list(found.values())


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
    ("artstation", re.compile(r"artstation\.com/(?!marketplace\b|learning\b|artwork\b|blogs\b|prints\b|challenges\b|jobs\b)(?P<handle>[A-Za-z0-9_-]+)", re.I)),
    # Patreon uses both /c/ and the newer /cw/ as path prefixes before the
    # creator name — neither is ever the handle itself.
    ("patreon", re.compile(r"patreon\.com/(?:c/|cw/)?(?!posts\b|join\b|checkout\b|user\b|c\b|cw\b)(?P<handle>[A-Za-z0-9_-]+)", re.I)),
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
    ("mihuashi", re.compile(r"mihuashi\.com/(?:users|painters)/(?P<handle>[^/\s?#\"']+)", re.I)),
    ("youtube", re.compile(r"youtube\.com/@(?P<handle>[\w.-]+)", re.I)),
    ("youtube", re.compile(r"youtube\.com/(?:c/|user/)(?P<handle>[\w.-]+)", re.I)),
    ("youtube", re.compile(r"youtube\.com/channel/(?P<native_id>UC[\w-]{10,})", re.I)),
    ("discord", re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/(?P<handle>[A-Za-z0-9-]+)", re.I)),
    ("telegram", re.compile(r"\bt\.me/(?P<handle>[A-Za-z0-9_]{4,32})", re.I)),
    ("twitch", re.compile(r"twitch\.tv/(?!videos\b|directory\b|collections\b)(?P<handle>[A-Za-z0-9_]{3,25})", re.I)),
    ("furaffinity", re.compile(r"furaffinity\.net/user/(?P<handle>[\w.~-]+)", re.I)),
    ("behance", re.compile(r"(?:behance\.net|be\.net)/(?!gallery\b)(?P<handle>[A-Za-z0-9_-]+)", re.I)),
    ("boosty", re.compile(r"boosty\.to/(?P<handle>[A-Za-z0-9_.-]+)", re.I)),
    ("artfight", re.compile(r"artfight\.net/~(?P<handle>[\w.-]+)", re.I)),
    ("biosite", re.compile(r"bio\.site/(?P<handle>[\w.-]+)", re.I)),
    ("coloso", re.compile(r"coloso\.(?:us|global|jp|co\.kr)/(?:[a-z]{2}/)?(?:products/)?(?P<handle>[\w-]+)", re.I)),
    ("linktree", re.compile(r"linktr\.ee/(?P<handle>[\w.]+)", re.I)),
    ("carrd", re.compile(r"\b(?P<handle>[A-Za-z0-9-]+)\.carrd\.co", re.I)),
    ("potofu", re.compile(r"potofu\.me/(?P<handle>[\w.-]+)", re.I)),
    ("litlink", re.compile(r"lit\.link/(?:en/|ja/)?(?P<handle>\w+)", re.I)),
]

_SUBDOMAIN_JUNK = {"www", "blog", "shop", "app", "help", "about", "support"}

# --- personal websites (generic fallback) ---------------------------------

_GENERIC_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]{}]+", re.I)
_DOMAIN_OF = re.compile(r"^(?:https?://)?(?:www\.)?([^/\s:?#]+)", re.I)

_PLATFORM_DOMAINS = {
    "x.com", "twitter.com", "bsky.app", "pixiv.net", "skeb.jp",
    "artstation.com", "patreon.com", "ko-fi.com", "vgen.co", "cara.app",
    "xfolio.jp", "deviantart.com", "tumblr.com", "gumroad.com", "inprnt.com",
    "instagram.com", "linktr.ee", "carrd.co", "potofu.me", "lit.link",
    "mihuashi.com", "youtube.com", "youtu.be", "discord.gg", "discord.com",
    "discordapp.com", "t.me", "telegram.me", "twitch.tv",
    "furaffinity.net", "behance.net", "be.net", "boosty.to", "artfight.net",
    "bio.site", "coloso.us", "coloso.global", "coloso.jp", "coloso.co.kr",
}
# Shorteners (opaque), commerce/utility noise, and booru domains — boorus are
# hint-only by hard rule and must never enter the graph as artist links.
_NON_WEBSITE_DOMAINS = _PLATFORM_DOMAINS | {
    "t.co", "bit.ly", "tinyurl.com", "goo.gl",
    "google.com", "forms.gle", "docs.google.com", "drive.google.com",
    "open.spotify.com", "spotify.com", "amazon.com", "amazon.co.jp",
    "amzn.to", "amzn.asia", "apple.com", "paypal.me", "paypal.com",
    "cash.app", "streamlabs.com", "throne.com", "thron.ee",
    "donmai.us", "gelbooru.com", "e621.net", "rule34.xxx",
    "safebooru.org", "yande.re", "konachan.com", "sankakucomplex.com",
}


def find_website_links(text: str | None) -> list[PlatformLink]:
    """URLs that belong to no known platform => the artist's personal site.
    The handle keeps the path (trimmed) so different artists' pages on a
    shared host never collapse into one account."""
    if not text:
        return []
    seen: dict[str, PlatformLink] = {}
    for m in _GENERIC_URL.finditer(text):
        if text[m.end():m.end() + 3].startswith(("…", "...", "‥")):
            continue
        url = m.group(0).rstrip(".,;:!?)»」】")
        domain_match = _DOMAIN_OF.match(url)
        if not domain_match:
            continue
        domain = domain_match.group(1).lower()
        if "." not in domain or any(
            domain == d or domain.endswith("." + d) for d in _NON_WEBSITE_DOMAINS
        ):
            continue
        path = re.sub(r"^(?:https?://)?(?:www\.)?", "", url).split("?")[0].split("#")[0]
        handle = path.rstrip("/").lower()[:80]
        if not url.startswith("http"):
            url = "https://" + url
        seen.setdefault(handle, PlatformLink("website", handle, None, url))
    return list(seen.values())


def find_platform_links(text: str | None) -> list[PlatformLink]:
    if not text:
        return []
    seen: dict[tuple, PlatformLink] = {}
    for platform, pattern in _LINK_PATTERNS:
        for m in pattern.finditer(text):
            # Platform bio limits truncate long URLs with an ellipsis; half a
            # handle silently matches the wrong account, so drop the link.
            if text[m.end():m.end() + 3].startswith(("…", "...", "‥")):
                continue
            groups = m.groupdict()
            handle = groups.get("handle")
            native_id = groups.get("native_id")
            if handle and (len(handle) < 2 or handle.lower() in _SUBDOMAIN_JUNK):
                continue
            url = m.group(0)
            if not url.startswith("http"):
                url = "https://" + url.lstrip("/")
            key = (platform, (handle or "").lower(), native_id)
            seen.setdefault(key, PlatformLink(platform, handle, native_id, url))
    return list(seen.values())
