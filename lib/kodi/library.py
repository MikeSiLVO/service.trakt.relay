import logging
from typing import List, Dict, Optional
from lib.media.dates import local_to_utc
from lib.media.ids import guess_ids
from lib.kodi.utilities import is_excluded

from lib.kodi.client import batch_request, request

logger = logging.getLogger(__name__)


def to_trakt_item(
    media_type: str, data: Dict, mode: str = "collected"
) -> Optional[Dict]:
    """Reshape Kodi item to Trakt form, mutating it; None if excluded or episode the mode skips."""
    if media_type == "show":
        if "uniqueid" in data:
            data["ids"] = data.pop("uniqueid")
        else:
            logger.debug("to_trakt_item(): No uniqueid found")
        data["rating"] = (
            data["userrating"] if "userrating" in data and data["userrating"] > 0 else 0
        )
        del data["label"]
        return data
    elif media_type == "episode":
        if is_excluded(data["file"]):
            return

        if data["playcount"] is None:
            plays = 0
        else:
            plays = data.pop("playcount")

        if plays > 0:
            watched = 1
        else:
            watched = 0

        episode = {
            "season": data["season"],
            "number": data["episode"],
            "title": data["label"],
            "ids": {"episodeid": data["episodeid"]},
            "watched": watched,
            "plays": plays,
            "collected": 1,
        }

        if "uniqueid" in data:
            if "tmdb" in data["uniqueid"]:
                episode["ids"]["tmdb"] = data["uniqueid"]["tmdb"]
            if "imdb" in data["uniqueid"]:
                episode["ids"]["imdb"] = data["uniqueid"]["imdb"]
            if "tvdb" in data["uniqueid"]:
                episode["ids"]["tvdb"] = data["uniqueid"]["tvdb"]
            elif "unknown" in data["uniqueid"] and data["uniqueid"]["unknown"] != "":
                episode["ids"].update(
                    guess_ids(data["uniqueid"]["unknown"], media_type)[0]
                )

        if "lastplayed" in data:
            episode["watched_at"] = local_to_utc(data["lastplayed"])
        if "dateadded" in data:
            episode["collected_at"] = local_to_utc(data["dateadded"])
        if "runtime" in data:
            episode["runtime"] = data["runtime"]
        episode["rating"] = (
            data["userrating"] if "userrating" in data and data["userrating"] > 0 else 0
        )
        if mode == "watched" and episode["watched"]:
            return episode
        elif mode == "collected" and episode["collected"]:
            return episode
        else:
            return

    elif media_type == "movie":
        if is_excluded(data.pop("file")):
            return
        if "lastplayed" in data:
            data["watched_at"] = local_to_utc(data.pop("lastplayed"))
        if "dateadded" in data:
            data["collected_at"] = local_to_utc(data.pop("dateadded"))
        if data["playcount"] is None:
            data["plays"] = 0
        else:
            data["plays"] = data.pop("playcount")
        data["rating"] = (
            data["userrating"] if "userrating" in data and data["userrating"] > 0 else 0
        )
        data["collected"] = 1
        data["watched"] = 1 if data["plays"] > 0 else 0
        if "uniqueid" in data:
            data["ids"] = data.pop("uniqueid")
        else:
            logger.debug("to_trakt_item(): No uniqueid found")
        del data["label"]
        return data
    else:
        logger.debug("to_trakt_item() No valid media_type")
        return


def to_trakt_items(data: Dict, mode: str = "collected") -> Optional[List]:
    """Convert a Kodi list to Trakt form, episodes grouped by season, dropped items left out."""
    if "tvshows" in data:
        shows = data["tvshows"]

        for show in shows:
            to_trakt_item("show", show, mode)
        return shows

    elif "episodes" in data:
        episodes_by_season = {}
        seasons = []
        for episode in data["episodes"]:
            while episode["season"] not in episodes_by_season:
                s_no = episode["season"]
                episodes_by_season[s_no] = []
            s_no = episode["season"]
            trakt_episode = to_trakt_item("episode", episode, mode)
            if trakt_episode:
                episodes_by_season[s_no].append(trakt_episode)

        for episode in episodes_by_season:
            seasons.append({"number": episode, "episodes": episodes_by_season[episode]})
        return seasons

    elif "movies" in data:
        movies = data["movies"]
        kodi_movies = []

        for movie in movies:
            trakt_movie = to_trakt_item("movie", movie, mode)
            if trakt_movie:
                kodi_movies.append(trakt_movie)
        return kodi_movies
    else:
        logger.debug("to_trakt_items() No valid key found in rpc data")
        return


def get_show_details(show_id: int, properties: List) -> Optional[Dict]:
    """Get a show's details from the Kodi library, or None."""
    result = request(
        "VideoLibrary.GetTVShowDetails", {"tvshowid": show_id, "properties": properties}
    )
    logger.debug("get_show_details(): %s" % str(result))

    if not result:
        logger.debug("get_show_details(): Result from Kodi was empty.")
        return None

    try:
        return result["tvshowdetails"]
    except KeyError:
        logger.debug("get_show_details(): KeyError: result['tvshowdetails']")
        return None


def get_season_details(season_id: int, properties: List) -> Optional[Dict]:
    """Get a season's details from the Kodi library, or None."""
    result = request(
        "VideoLibrary.GetSeasonDetails", {"seasonid": season_id, "properties": properties}
    )
    logger.debug("get_season_details(): %s" % str(result))

    if not result:
        logger.debug("get_season_details(): Result from Kodi was empty.")
        return None

    try:
        return result["seasondetails"]
    except KeyError:
        logger.debug("get_season_details(): KeyError: result['seasondetails']")
        return None


def get_episode_details(episode_id: int, properties: List) -> Optional[Dict]:
    """Get an episode's details plus its show's ids and year, or None."""
    if "tvshowid" not in properties:
        properties = properties + ["tvshowid"]
    result = request(
        "VideoLibrary.GetEpisodeDetails", {"episodeid": episode_id, "properties": properties}
    )
    logger.debug("get_episode_details(): %s" % str(result))

    episode = result.get("episodedetails") if result else None
    if not episode:
        logger.debug("get_episode_details(): Result from Kodi was empty.")
        return None

    show_data = get_show_details(episode["tvshowid"], ["year", "uniqueid"])
    if not show_data:
        logger.debug(
            "get_episode_details(): Result from get_show_details() was empty."
        )
        return None

    if "uniqueid" in show_data:
        episode["show_ids"] = show_data["uniqueid"]
    episode["year"] = show_data["year"]
    return episode


def get_play_states(media_type: str, ids: List[int]) -> List[Optional[Dict]]:
    """Get each movie's or episode's resume point and last play in id order, None if unreadable."""
    key = media_type + "id"
    method = "VideoLibrary.Get%sDetails" % media_type.capitalize()
    results = batch_request(method, [{key: i, "properties": ["resume", "lastplayed"]} for i in ids])
    return [(result or {}).get(media_type + "details") for result in results]


def get_movie_details(movie_id: int, properties: List) -> Optional[Dict]:
    """Get a movie's details from the Kodi library, or None."""
    result = request(
        "VideoLibrary.GetMovieDetails", {"movieid": movie_id, "properties": properties}
    )
    logger.debug("get_movie_details(): %s" % str(result))

    if not result:
        logger.debug("get_movie_details(): Result from Kodi was empty.")
        return None

    try:
        return result["moviedetails"]
    except KeyError:
        logger.debug("get_movie_details(): KeyError: result['moviedetails']")
        return None
