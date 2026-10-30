import difflib
import re
from typing import Tuple, List, Dict, Optional


def build_media_index(items: List) -> Dict:
    """Index a media list by IMDb, TMDB, TVDB, title with year and title alone; first one wins."""
    index: Dict = {"imdb": {}, "tmdb": {}, "tvdb": {}, "title_year": {}, "title": {}}
    for item in items:
        ids = item.get("ids", {})
        imdb = ids.get("imdb")
        if imdb and str(imdb).startswith("tt"):
            index["imdb"].setdefault(str(imdb), item)
        tmdb = ids.get("tmdb")
        if tmdb:
            index["tmdb"].setdefault(str(tmdb), item)
        tvdb = ids.get("tvdb")
        if tvdb:
            index["tvdb"].setdefault(str(tvdb), item)
        title = item.get("title")
        if title:
            year = item.get("year")
            if year is not None:
                index["title_year"].setdefault((str(title), str(year)), item)
            index["title"].setdefault(str(title), item)
    return index


def find_in_index(
    item: Dict, index: Dict, match_title_year: bool
) -> Optional[Dict]:
    """Find the indexed item matching ids first, then title and year when allowed."""
    ids = item.get("ids", {})

    imdb = ids.get("imdb")
    if imdb and str(imdb).startswith("tt"):
        result = index["imdb"].get(str(imdb))
        if result is not None:
            return result

    tmdb = ids.get("tmdb")
    if tmdb:
        result = index["tmdb"].get(str(tmdb))
        if result is not None:
            return result

    tvdb = ids.get("tvdb")
    if tvdb:
        result = index["tvdb"].get(str(tvdb))
        if result is not None:
            return result

    if match_title_year:
        title = item.get("title")
        year = item.get("year")
        # title alone only without a year, so a miss never lets Blind (2017) match Blind (2014)
        if title is not None and year is not None:
            return index["title_year"].get((str(title), str(year)))
        if title is not None:
            return index["title"].get(str(title))

    return None


def split_title_year(title: str) -> Tuple[str, str]:
    """Split 'Title (2019)' into title and year, or two empty strings."""
    prog = re.compile(r"^(.+) \((\d{4})\)$")
    result = prog.match(title)

    if result:
        return result.group(1), result.group(2)
    else:
        return "", ""


def fuzzy_match(string1: str, string2: str, match_percent: float = 55.0) -> bool:
    """True when the two strings are at least the given percent similar."""
    return (
        difflib.SequenceMatcher(None, string1, string2).ratio() * 100
    ) >= match_percent
