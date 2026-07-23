"""Region classifier: Eastern / Western, for the stratified ranking cut.

Per artist, combines:
- script detection over member bios + display names (hiragana/katakana => ja,
  hangul => ko, han without kana => zh — all Eastern signals; latin-only
  leans Western);
- platform fingerprint (Skeb/Pixiv/FANBOX/Fantia/nijie/... lean Eastern;
  ArtStation/FurAffinity/INPRNT/DeviantArt lean Western).

Writes artists.region + region_confidence where region_source = 'auto';
manual overrides are never touched. Low-margin artists stay 'unknown'.

Usage: uv run python -m inkpages.classify_region
"""
import re
from collections import Counter

from psycopg.rows import dict_row

from . import db

EAST_PLATFORMS = {"skeb", "pixiv", "fanbox", "fantia", "booth", "dlsite",
                  "nijie", "skima", "coconala", "mihuashi", "xfolio",
                  "litlink", "potofu", "profcard", "twpf", "tsunagu"}
WEST_PLATFORMS = {"artstation", "furaffinity", "inprnt", "deviantart"}

_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
# Non-Latin, non-CJK scripts. Checked before the Latin fallback so a
# Thai/Russian/Arabic bio that also carries a stray latin char isn't mislabeled
# 'en'. Script → dominant language (Thai ⇒ th; Cyrillic ⇒ ru; Arabic ⇒ ar) —
# a heuristic, but far better than the 'en'/'unknown' these used to collapse
# into. Run-length thresholds (not the single-char test the CJK scripts use)
# reject decorative use: Cyrillic and Greek glyphs are Latin-lookalikes that
# stylized English names string together (🏝️кⒶσѕ ρυик — Latin, not Russian),
# so Cyrillic needs a 4+ run (a real word); Thai/Arabic have no Latin-lookalike
# decorative use, so 2 consecutive chars is enough.
_THAI = re.compile(r"[ก-๛]{2}")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]{4,}")
_ARABIC = re.compile(r"[؀-ۿ]{2}")


def detect_language(text: str) -> str:
    if _KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _HAN.search(text):
        return "zh"
    if _THAI.search(text):
        return "th"
    if _CYRILLIC.search(text):
        return "ru"
    if _ARABIC.search(text):
        return "ar"
    if _LATIN.search(text):
        return "en"
    return "unknown"


def score(text: str, platforms: set[str]) -> tuple[str, str, float]:
    """(language, region, confidence)."""
    language = detect_language(text)
    east = west = 0.0
    if language in ("ja", "ko"):
        east += 2.0
    elif language == "zh":
        east += 1.5
    elif language == "en":
        west += 1.0
    east += min(len(platforms & EAST_PLATFORMS), 3)
    west += min(len(platforms & WEST_PLATFORMS), 3)
    margin = abs(east - west)
    if margin < 0.75:
        return language, "unknown", 0.0
    return language, ("eastern" if east > west else "western"), round(min(margin / 4, 1.0), 2)


def main() -> None:
    stats: Counter = Counter()
    with db.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """select ar.id,
                          string_agg(coalesce(s.bio_text, '') || ' ' ||
                                     coalesce(a.display_name, ''), ' ') as text,
                          array_agg(distinct p.slug) as platforms
                   from artists ar
                   join artist_accounts aa on aa.artist_id = ar.id and aa.removed_at is null
                   join accounts a on a.id = aa.account_id
                   join platforms p on p.id = a.platform_id
                   left join lateral (select bio_text from account_snapshots s
                                      where s.account_id = a.id
                                      order by captured_at desc limit 1) s on true
                   where ar.region_source = 'auto' and ar.merged_into is null
                   group by ar.id"""
            )
            rows = cur.fetchall()
        with conn.cursor() as cur:
            for row in rows:
                language, region, confidence = score(row["text"] or "",
                                                     set(row["platforms"]))
                cur.execute(
                    """update artists set region = %s, region_confidence = %s,
                                          language = %s, updated_at = now()
                       where id = %s and region_source = 'auto'""",
                    (region, confidence, language, row["id"]),
                )
                stats[f"lang_{language}"] += 1
        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
