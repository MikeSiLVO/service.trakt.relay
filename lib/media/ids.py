from typing import Any, Tuple, Dict, Optional

from lib.media.utilities import is_episode, is_movie, is_season, is_show


def guess_ids(item_id: str, media_type: str) -> Tuple[Dict, str]:
    """Guess the id type of a bare id and return it as an ids dict with its type."""
    data = {}
    id_type = ""
    if item_id.startswith("tt"):
        data["imdb"] = item_id
        id_type = "imdb"
    elif item_id.isdigit() and is_movie(media_type):
        data["tmdb"] = item_id
        id_type = "tmdb"
    elif item_id.isdigit() and (
        is_episode(media_type) or is_season(media_type) or is_show(media_type)
    ):
        data["tvdb"] = item_id
        id_type = "tvdb"
    else:
        data["slug"] = item_id
        id_type = "slug"
    return data, id_type


def has_id(item: Dict) -> bool:
    """True when the item has an IMDb, TMDB or TVDB id to match by, not just a title."""
    ids = item.get("ids") or {}
    return bool(str(ids.get("imdb") or "").startswith("tt") or ids.get("tmdb") or ids.get("tvdb"))


def pick_best_id(ids: Dict, media_type: str) -> Tuple[Any, Optional[str]]:
    """Pick the most reliable id and type: Trakt, IMDb for a movie, then TMDB, TVDB, the rest."""
    if "trakt" in ids:
        return ids["trakt"], "trakt"
    elif "imdb" in ids and is_movie(media_type):
        return ids["imdb"], "imdb"
    elif "tmdb" in ids:
        return ids["tmdb"], "tmdb"
    elif "tvdb" in ids:
        return ids["tvdb"], "tvdb"
    elif "imdb" in ids:
        return ids["imdb"], "imdb"
    elif "tvrage" in ids:
        return ids["tvrage"], "tvrage"
    elif "slug" in ids:
        return ids["slug"], "slug"
    return None, None
