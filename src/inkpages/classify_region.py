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
                  "litlink", "potofu"}
WEST_PLATFORMS = {"artstation", "furaffinity", "inprnt", "deviantart"}

_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")


def score(text: str, platforms: set[str]) -> tuple[str, float]:
    east = west = 0.0
    if _KANA.search(text):
        east += 2.0
    if _HANGUL.search(text):
        east += 2.0
    if _HAN.search(text) and not _KANA.search(text):
        east += 1.5
    if _LATIN.search(text) and not (_KANA.search(text) or _HANGUL.search(text)
                                    or _HAN.search(text)):
        west += 1.0
    east += min(len(platforms & EAST_PLATFORMS), 3)
    west += min(len(platforms & WEST_PLATFORMS), 3)
    margin = abs(east - west)
    if margin < 0.75:
        return "unknown", 0.0
    return ("eastern" if east > west else "western"), round(min(margin / 4, 1.0), 2)


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
                region, confidence = score(row["text"] or "", set(row["platforms"]))
                cur.execute(
                    """update artists set region = %s, region_confidence = %s,
                                          updated_at = now()
                       where id = %s and region_source = 'auto'
                         and (region <> %s or region_confidence is distinct from %s)""",
                    (region, confidence, row["id"], region, confidence),
                )
                stats[region] += cur.rowcount
        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
