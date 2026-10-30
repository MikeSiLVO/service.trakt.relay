from typing import List, Dict


def is_movie(media_type: str) -> bool:
    """True when the media type is a movie."""
    return media_type == "movie"


def is_episode(media_type: str) -> bool:
    """True when the media type is an episode."""
    return media_type == "episode"


def is_show(media_type: str) -> bool:
    """True when the media type is a show."""
    return media_type == "show"


def is_season(media_type: str) -> bool:
    """True when the media type is a season."""
    return media_type == "season"


def is_valid_media_type(media_type: str) -> bool:
    """True when the media type is one of the four Trakt item types."""
    return media_type in ["movie", "show", "season", "episode"]


def chunks(items: List, n: int) -> List:
    """Split a list into consecutive slices of at most n items."""
    return [items[i : i + n] for i in range(0, len(items), n)]


def format_item_name(media_type: str, info: Dict) -> str:
    """Format a display name like S01E02 - Title, or empty on an unknown type or missing field."""
    s = ""
    try:
        if is_show(media_type):
            s = info["title"]
        elif is_episode(media_type):
            s = "S%02dE%02d - %s" % (info["season"], info["number"], info["title"])
        elif is_season(media_type):
            if info["season"] > 0:
                s = "%s - Season %d" % (info["title"], info["season"])
            else:
                s = "%s - Specials" % info["title"]
        elif is_movie(media_type):
            s = "%s (%s)" % (info["title"], info["year"])
    except KeyError:
        s = ""
    return s
