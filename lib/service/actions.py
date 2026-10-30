"""Actions the context menu and scripts ask for: rating, the watchlist and rewatches."""
import logging
from typing import Any, Dict, Optional
import xbmcgui

from lib.trakt import shared
from lib.media import utilities
from lib.kodi.utilities import DB_TYPES, notify
from lib.dialogs.rating import rate_media
from lib.media.ids import pick_best_id
from lib.media.ids import guess_ids
from lib.kodi.client import batch_request, request
from lib.kodi.library import (
    get_episode_details,
    get_movie_details,
    get_season_details,
    get_show_details,
)
from lib.kodi.settings import get_string

logger = logging.getLogger(__name__)
RATINGS = [str(n) for n in range(1, 11)]


def _library_item(data: Dict) -> Optional[Dict]:
    """Look up the library item a script sent; a show, season or episode carries the show's ids."""
    media_type = data.get("media_type") or DB_TYPES.get(data.get("dbtype", ""), "")
    dbid = str(data.get("dbid", ""))
    if not dbid.isdigit() or not utilities.is_valid_media_type(media_type):
        logger.debug("No library item to %s: %s" % (data["action"], data))
        return None
    item: Dict = {"media_type": media_type, "dbid": int(dbid)}
    if utilities.is_movie(media_type):
        movie = get_movie_details(item["dbid"], ["uniqueid", "title", "year"])
        if not movie:
            return None
        item.update(ids=movie["uniqueid"], title=movie["title"], year=movie["year"])
        return item
    tvshowid = item["dbid"]
    if utilities.is_season(media_type):
        season = get_season_details(item["dbid"], ["tvshowid", "season"])
        if not season:
            return None
        tvshowid, item["season"] = season["tvshowid"], season["season"]
    elif utilities.is_episode(media_type):
        episode = get_episode_details(item["dbid"], ["tvshowid", "season", "episode"])
        if not episode:
            return None
        tvshowid, item["season"], item["number"] = (
            episode["tvshowid"], episode["season"], episode["episode"])
    show = get_show_details(tvshowid, ["uniqueid"])
    if not show:
        return None
    item.update(tvshowid=tvshowid, ids=show["uniqueid"], title=show["label"])
    return item


def find_trakt_id(best_id: Any, id_type: Optional[str], media_type: str) -> Any:
    """Find the Trakt id for an item's best id, searching Trakt unless it is one, or None."""
    if not id_type or id_type == "trakt":
        return best_id
    search_type = "movie" if utilities.is_movie(media_type) else "show"
    hits = shared.api.search_by_id(best_id, id_type, search_type)
    return dict(hits[0].keys)["trakt"] if hits else None


def rate_manually(data: Dict) -> None:
    """Rate or unrate a library or remote item on Trakt, asking when no valid rating is given."""
    action = data["action"]
    if "remoteid" in data:
        media_type = data.get("media_type", "")
        if not utilities.is_valid_media_type(media_type):
            logger.debug("Invalid media type '%s' passed for manual %s." % (media_type, action))
            return
        item: Optional[Dict] = {"media_type": media_type}
        try:
            for key, arg in (("season", "season"), ("number", "episode")):
                if arg in data:
                    item[key] = int(data[arg])
        except ValueError:
            logger.debug("Error parsing season or episode for manual %s" % action)
            return
        temp_ids, id_type = guess_ids(str(data["remoteid"]), media_type)
        best_id = temp_ids[id_type]
    else:
        item = _library_item(data)
        if not item:
            return
        media_type = item["media_type"]
        best_id, id_type = pick_best_id(item["ids"], media_type)
    logger.debug("Manual %s of %s: %s" % (action, media_type, item))

    trakt_id = find_trakt_id(best_id, id_type, media_type)
    if not trakt_id:
        logger.debug("No Trakt id for: |%s|-|%s|-|%s|." % (media_type, id_type, best_id))
        return
    if utilities.is_episode(media_type):
        summary = shared.api.get_episode_summary(
            trakt_id, item["season"], item["number"]
        )
        user_rating = shared.api.get_user_rating("episode", dict(summary.keys).get("trakt"))
    elif utilities.is_season(media_type):
        summary = shared.api.get_show_summary(trakt_id)
        user_rating = shared.api.get_user_rating("season", trakt_id, item["season"])
    elif utilities.is_show(media_type):
        summary = shared.api.get_show_summary(trakt_id)
        user_rating = shared.api.get_user_rating("show", trakt_id)
    else:
        summary = shared.api.get_movie_summary(trakt_id)
        user_rating = shared.api.get_user_rating("movie", trakt_id)

    summary = summary.to_dict()
    if not summary:
        logger.debug("Summary info was empty, possible problem retrieving data from Trakt.tv")
        return
    summary["user"] = {"ratings": user_rating}
    if utilities.is_episode(media_type):
        summary["season"] = item["season"]
        summary["number"] = item["number"]
        summary["episodeid"] = item.get("dbid")
    elif utilities.is_season(media_type):
        summary["season"] = item["season"]
        summary["seasonid"] = item.get("dbid")
    elif utilities.is_movie(media_type):
        summary["movieid"] = item.get("dbid")
    elif utilities.is_show(media_type):
        summary["tvshowid"] = item.get("dbid")

    if action == "unrate":
        rate_media(media_type, [summary], unrate=True)
    elif data.get("rating") in RATINGS:
        rate_media(media_type, [summary], rating=int(data["rating"]))
    else:
        rate_media(media_type, [summary])


def add_to_watchlist(data: Dict) -> None:
    """Add a library movie, show, season or episode to the Trakt watchlist and notify the result."""
    item = _library_item(data)
    if not item:
        return
    media_type = item["media_type"]
    if utilities.is_movie(media_type):
        payload = {"movies": [{"ids": item["ids"]}]}
    elif utilities.is_show(media_type):
        payload = {"shows": [{"ids": item["ids"]}]}
    else:
        season: Dict = {"number": item["season"]}
        if utilities.is_episode(media_type):
            season["episodes"] = [{"number": item["number"]}]
        payload = {"shows": [{"ids": item["ids"], "seasons": [season]}]}
    logger.debug("Watchlist add: %s" % payload)

    result = shared.api.add_to_watchlist(payload)
    name = utilities.format_item_name(media_type, item)
    if result and not any((result.get("not_found") or {}).values()):
        notify(get_string(32165), name)
    else:
        notify(get_string(32166), name)


def start_rewatch(data: Dict) -> None:
    """Start a rewatch of a library show on Trakt, once confirmed, and unwatch its Kodi episodes."""
    item = _library_item(data)
    if not item or not utilities.is_show(item["media_type"]):
        return
    title = item["title"]
    if not xbmcgui.Dialog().yesno(title, get_string(32221)):
        return
    trakt_id = find_trakt_id(*pick_best_id(item["ids"], "show"), "show")
    if not trakt_id or shared.api.start_rewatch(trakt_id) is None:
        notify(get_string(32223), title)
        return
    result = request("VideoLibrary.GetEpisodes",
                     {"tvshowid": item["tvshowid"], "properties": ["playcount"]}) or {}
    updates = [{"episodeid": episode["episodeid"], "playcount": 0, "lastplayed": ""}
               for episode in result.get("episodes", []) if episode["playcount"]]
    logger.info("Rewatch of '%s' started, unwatching %d episode(s)" % (title, len(updates)))
    for chunk in utilities.chunks(updates, 50):
        batch_request("VideoLibrary.SetEpisodeDetails", chunk)
    notify(get_string(32222), title)
