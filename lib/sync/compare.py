"""Comparisons of Kodi and Trakt items, finding what one side lacks or has different."""

import logging
from typing import List, Dict, Optional, Tuple, Union

import dateutil.parser

from lib.media.dates import local_to_utc, utc_to_local
from lib.media.ids import has_id
from lib.media.match import build_media_index, find_in_index


logger = logging.getLogger(__name__)


def sanitize_movies(movies: List) -> None:
    """Sanitize movies for Trakt, stripping Kodi and state fields."""
    for movie in movies:
        if "collected" in movie:
            del movie["collected"]
        if "watched" in movie:
            del movie["watched"]
        if "movieid" in movie:
            del movie["movieid"]
        if "plays" in movie:
            del movie["plays"]
        if "userrating" in movie:
            del movie["userrating"]


def sanitize_shows(shows: Dict) -> None:
    """Sanitize shows for Trakt, stripping Kodi and state fields from their episodes."""
    for show in shows["shows"]:
        for season in show["seasons"]:
            for episode in season["episodes"]:
                if "collected" in episode:
                    del episode["collected"]
                if "watched" in episode:
                    del episode["watched"]
                if "season" in episode:
                    del episode["season"]
                if "plays" in episode:
                    del episode["plays"]
                if "ids" in episode and "episodeid" in episode["ids"]:
                    del episode["ids"]["episodeid"]


def _copy_episode(episode):
    """Copy an episode shallowly, with its own copy of the ids dict."""
    ep = dict(episode)
    if "ids" in ep:
        ep["ids"] = dict(ep["ids"])
    return ep


def compare_movies(
    source: List,
    target: List,
    match_title_year: bool,
    watched: bool = False,
    restrict: bool = False,
    playback: bool = False,
    rating: bool = False,
    unrated_only: bool = False,
) -> List:
    """Compare movies to the target: ones it lacks or differs on, or every match for playback."""
    movies = []
    target_index = build_media_index(target)
    for source_movie in source:
        if source_movie:
            target_movie = find_in_index(source_movie, target_index, match_title_year)

            if target_movie:
                if watched:
                    if target_movie["watched"] == 0 and source_movie["watched"] == 1:
                        movie = dict(source_movie)
                        if "movieid" not in movie:
                            movie["movieid"] = target_movie["movieid"]
                        movies.append(movie)
                elif playback:
                    movie = dict(source_movie)
                    if "movieid" not in movie:
                        movie["movieid"] = target_movie["movieid"]
                    movie["runtime"] = target_movie["runtime"]
                    movies.append(movie)
                elif rating:
                    source_rating = source_movie.get("rating", 0)
                    target_rating = target_movie.get("rating", 0)
                    if source_rating and source_rating != target_rating and not (
                            unrated_only and target_rating):
                        movie = dict(source_movie)
                        movie["rating"] = source_rating
                        if "movieid" not in movie:
                            movie["movieid"] = target_movie["movieid"]
                        movies.append(movie)
                else:
                    if "collected" in target_movie and not target_movie["collected"]:
                        movies.append(dict(source_movie))
            else:
                if not restrict:
                    if "collected" in source_movie and source_movie["collected"]:
                        if watched and (source_movie["watched"] == 1):
                            movies.append(dict(source_movie))
                        elif rating and source_movie["rating"] != 0:
                            movies.append(dict(source_movie))
                        elif not watched and not rating:
                            movies.append(dict(source_movie))
    return movies


def compare_shows(
    source: Dict, target: Dict, match_title_year: bool, restrict: bool = False,
    unrated_only: bool = False
) -> Dict:
    """Compare show ratings to target: ones rated otherwise, plus rated ones it lacks by default."""
    shows = []
    target_index = build_media_index(target["shows"])
    for source_show in source["shows"]:
        if source_show:
            target_show = find_in_index(
                source_show, target_index, match_title_year
            )

            if target_show:
                show = {
                    "title": source_show["title"],
                    "ids": {},
                    "year": source_show["year"],
                }
                if source_show["ids"]:
                    show["ids"].update(source_show["ids"])
                if target_show["ids"]:
                    show["ids"].update(target_show["ids"])
                if "tvshowid" in target_show:
                    show["tvshowid"] = target_show["tvshowid"]

                source_rating = source_show.get("rating", 0)
                target_rating = target_show.get("rating", 0)
                if source_rating and source_rating != target_rating and not (
                        unrated_only and target_rating):
                    show["rating"] = source_rating
                    shows.append(show)
            else:
                if not restrict:
                    show = {
                        "title": source_show["title"],
                        "ids": {},
                        "year": source_show["year"],
                    }
                    if source_show["ids"]:
                        show["ids"].update(source_show["ids"])
                    if "tvshowid" in source_show:
                        show["tvshowid"] = source_show["tvshowid"]

                    if "rating" in source_show and source_show["rating"] != 0:
                        show["rating"] = source_show["rating"]
                        shows.append(show)

    result = {"shows": shows}
    return result


def compare_episodes(
    source: Dict,
    target: Dict,
    match_title_year: bool,
    watched: bool = False,
    restrict: bool = False,
    playback: bool = False,
    rating: bool = False,
    unrated_only: bool = False,
) -> Dict:
    """Compare episodes to the target: ones it lacks or differs on, or every match for playback."""
    shows = []
    target_index = build_media_index(target["shows"])
    for source_show in source["shows"]:
        if source_show:
            target_show = find_in_index(
                source_show, target_index, match_title_year
            )

            if target_show:
                season_diff = {}
                source_seasons = _episodes_by_number(source_show["seasons"])
                target_seasons = _episodes_by_number(target_show["seasons"])
                for season in source_seasons:
                    a = source_seasons[season]
                    if season in target_seasons:
                        b = target_seasons[season]
                        diff = list(set(a).difference(set(b)))
                        if playback:
                            t = list(set(a).intersection(set(b)))
                            if len(t) > 0:
                                eps = {}
                                for ep in t:
                                    eps[ep] = _copy_episode(a[ep])
                                    if "episodeid" in target_seasons[season][ep]["ids"]:
                                        eps[ep]["ids"]["episodeid"] = target_seasons[
                                            season
                                        ][ep]["ids"]["episodeid"]
                                    eps[ep]["runtime"] = target_seasons[season][ep][
                                        "runtime"
                                    ]
                                season_diff[season] = eps
                        elif rating:
                            eps = {}
                            for ep in set(a).intersection(set(b)):
                                source_rating = a[ep].get("rating", 0)
                                target_rating = b[ep].get("rating", 0)
                                if source_rating and source_rating != target_rating and not (
                                        unrated_only and target_rating):
                                    eps[ep] = _copy_episode(a[ep])
                                    eps[ep]["rating"] = source_rating
                                    if "episodeid" in b[ep]["ids"]:
                                        eps[ep]["ids"]["episodeid"] = b[ep]["ids"]["episodeid"]
                            if not restrict:
                                eps.update(
                                    {ep: _copy_episode(a[ep]) for ep in diff if a[ep].get("rating")}
                                )
                            if eps:
                                season_diff[season] = eps
                        elif diff and not restrict:
                            season_diff[season] = {ep: _copy_episode(a[ep]) for ep in diff}
                    elif not restrict:
                        missing = {
                            ep_num: _copy_episode(ep)
                            for ep_num, ep in a.items()
                            if not rating or ep.get("rating")
                        }
                        if missing:
                            season_diff[season] = missing
                if len(season_diff) > 0:
                    show = {
                        "title": source_show["title"],
                        "ids": {},
                        "year": source_show["year"],
                        "seasons": [],
                    }
                    if source_show["ids"]:
                        show["ids"].update(source_show["ids"])
                    if target_show["ids"]:
                        show["ids"].update(target_show["ids"])
                    for season_key in season_diff:
                        episodes = []
                        for episode_key in season_diff[season_key]:
                            episodes.append(season_diff[season_key][episode_key])
                        show["seasons"].append(
                            {"number": season_key, "episodes": episodes}
                        )
                    if "tvshowid" in target_show:
                        show["tvshowid"] = target_show["tvshowid"]
                    shows.append(show)
            else:
                if not restrict:
                    if count_episodes([source_show]) > 0:
                        show = {
                            "title": source_show["title"],
                            "ids": {},
                            "year": source_show["year"],
                            "seasons": [],
                        }
                        if source_show["ids"]:
                            show["ids"].update(source_show["ids"])
                        for season_key in source_show["seasons"]:
                            episodes = []
                            for episode_key in season_key["episodes"]:
                                if watched and (episode_key["watched"] == 1):
                                    episodes.append(_copy_episode(episode_key))
                                elif rating and episode_key["rating"] != 0:
                                    episodes.append(_copy_episode(episode_key))
                                elif not watched and not rating:
                                    episodes.append(_copy_episode(episode_key))
                            if len(episodes) > 0:
                                show["seasons"].append(
                                    {
                                        "number": season_key["number"],
                                        "episodes": episodes,
                                    }
                                )

                        if count_episodes([show]) > 0:
                            shows.append(show)
    result = {"shows": shows}
    return result


def episodes_to_mark_watched(
    trakt_shows: Dict,
    kodi_shows_collected: Dict,
    match_title_year: bool,
    apply_rewatch_filter: bool = False,
) -> List[Dict]:
    """List Kodi episode updates for each copy Trakt has more plays of, rewatch-aware when asked."""
    updates: List[Dict] = []
    show_index = build_media_index(kodi_shows_collected.get("shows", []))
    for trakt_show in trakt_shows.get("shows", []):
        kodi_show = find_in_index(trakt_show, show_index, match_title_year)
        if not kodi_show:
            continue

        reset_at = trakt_show.get("reset_at") if apply_rewatch_filter else None

        kodi_eps_by_key: Dict = {}
        for season in kodi_show.get("seasons", []) or []:
            season_num = season.get("number")
            for ep in season.get("episodes", []) or []:
                ep_num = ep.get("number")
                if season_num is None or ep_num is None:
                    continue
                kodi_eps_by_key.setdefault((season_num, ep_num), []).append(ep)

        for trakt_season in trakt_show.get("seasons", []) or []:
            season_num = trakt_season.get("number")
            for trakt_ep in trakt_season.get("episodes", []) or []:
                trakt_plays = trakt_ep.get("plays", 0) or 0
                if trakt_plays <= 0:
                    continue
                ep_watched_at = trakt_ep.get("last_watched_at")
                if reset_at and (not ep_watched_at or ep_watched_at < reset_at):
                    continue
                key = (season_num, trakt_ep.get("number"))
                for kodi_ep in kodi_eps_by_key.get(key, []):
                    if (kodi_ep.get("plays", 0) or 0) >= trakt_plays:
                        continue
                    episodeid = kodi_ep.get("ids", {}).get("episodeid")
                    if episodeid is None:
                        continue
                    updates.append({
                        "episodeid": episodeid,
                        "playcount": trakt_plays,
                        "lastplayed": utc_to_local(ep_watched_at),
                    })
    return updates


def episodes_to_unwatch(
    trakt_shows: Dict, kodi_shows_watched: Dict, match_title_year: bool
) -> List[Dict]:
    """List Kodi updates unwatching episodes Trakt played before a rewatch and not since."""
    updates: List[Dict] = []
    show_index = build_media_index(kodi_shows_watched.get("shows", []))
    for trakt_show in trakt_shows.get("shows", []):
        reset_at = trakt_show.get("reset_at")
        kodi_show = find_in_index(trakt_show, show_index, match_title_year) if reset_at else None
        if not kodi_show:
            continue
        last_played = {
            (season.get("number"), ep.get("number")): ep.get("last_watched_at") or ""
            for season in trakt_show.get("seasons", []) or []
            for ep in season.get("episodes", []) or []
        }
        for season in kodi_show.get("seasons", []) or []:
            for ep in season.get("episodes", []) or []:
                watched_at = last_played.get((season.get("number"), ep.get("number")))
                episodeid = ep.get("ids", {}).get("episodeid")
                # one Trakt never played goes to its history this sync
                if episodeid is None or watched_at is None:
                    continue
                if watched_at < reset_at:
                    updates.append({"episodeid": episodeid, "playcount": 0, "lastplayed": ""})
    return updates


def movies_to_mark_watched(
    trakt_movies: List[Dict],
    kodi_movies: List[Dict],
    match_title_year: bool,
) -> List[Dict]:
    """List Kodi movie updates for each copy Trakt has more plays of."""
    kodi_by_uid: Dict = {}
    kodi_by_title_year: Dict = {}
    kodi_by_title: Dict = {}
    for movie in kodi_movies:
        ids = movie.get("ids", {}) or {}
        imdb = ids.get("imdb")
        if imdb and str(imdb).startswith("tt"):
            kodi_by_uid.setdefault(("imdb", str(imdb)), []).append(movie)
        tmdb = ids.get("tmdb")
        if tmdb:
            kodi_by_uid.setdefault(("tmdb", str(tmdb)), []).append(movie)
        tvdb = ids.get("tvdb")
        if tvdb:
            kodi_by_uid.setdefault(("tvdb", str(tvdb)), []).append(movie)
        title = movie.get("title")
        if title:
            year = movie.get("year")
            if year is not None:
                kodi_by_title_year.setdefault((str(title), str(year)), []).append(movie)
            kodi_by_title.setdefault(str(title), []).append(movie)

    updates: List[Dict] = []
    for trakt_movie in trakt_movies:
        trakt_plays = trakt_movie.get("plays", 0) or 0
        if trakt_plays <= 0:
            continue

        matches: List[Dict] = []
        ids = trakt_movie.get("ids", {}) or {}
        imdb = ids.get("imdb")
        if imdb and str(imdb).startswith("tt"):
            matches = kodi_by_uid.get(("imdb", str(imdb)), [])
        if not matches:
            tmdb = ids.get("tmdb")
            if tmdb:
                matches = kodi_by_uid.get(("tmdb", str(tmdb)), [])
        if not matches:
            tvdb = ids.get("tvdb")
            if tvdb:
                matches = kodi_by_uid.get(("tvdb", str(tvdb)), [])
        if not matches and match_title_year:
            title = trakt_movie.get("title")
            year = trakt_movie.get("year")
            if title and year is not None:
                matches = kodi_by_title_year.get((str(title), str(year)), [])
            elif title:
                matches = kodi_by_title.get(str(title), [])

        last_watched_at = trakt_movie.get("last_watched_at")
        for kodi_movie in matches:
            if (kodi_movie.get("plays", 0) or 0) >= trakt_plays:
                continue
            movieid = kodi_movie.get("movieid")
            if movieid is None:
                continue
            updates.append({
                "movieid": movieid,
                "playcount": trakt_plays,
                "lastplayed": utc_to_local(last_watched_at),
            })
    return updates


def movies_to_mirror(
    trakt_movies: List[Dict], kodi_movies: List[Dict], match_title_year: bool, paused: List[Dict]
) -> Tuple[List[Dict], List[Dict]]:
    """List Kodi movie updates to Trakt's plays and last play, plus unwatches of ones it lacks."""
    index = build_media_index([movie for movie in trakt_movies if movie.get("plays")])
    paused_at = {movie["ids"]["trakt"]: movie.get("paused_at")
                 for movie in paused if (movie.get("ids") or {}).get("trakt")}
    updates: List[Dict] = []
    unwatches: List[Dict] = []
    for movie in kodi_movies:
        trakt_movie = find_in_index(movie, index, match_title_year)
        if trakt_movie:
            last = _latest(trakt_movie.get("last_watched_at"),
                           paused_at.get((trakt_movie.get("ids") or {}).get("trakt")))
            if movie.get("plays") != trakt_movie["plays"] or not _same_time(
                    movie.get("watched_at"), last):
                updates.append({"movieid": movie["movieid"], "playcount": trakt_movie["plays"],
                                "lastplayed": utc_to_local(last)})
        elif movie.get("plays") and has_id(movie):
            unwatches.append({"movieid": movie["movieid"], "playcount": 0, "lastplayed": ""})
    return updates, unwatches


def episodes_to_mirror(
    trakt_shows: Dict, kodi_shows: Dict, match_title_year: bool, paused: Dict, rewatches: bool
) -> Tuple[List[Dict], List[Dict]]:
    """List Kodi episode updates to Trakt's plays and last play, plus unwatches of ones it lacks."""
    index = build_media_index(trakt_shows.get("shows", []))
    paused_at = {
        ((show.get("ids") or {}).get("trakt"), season.get("number"), ep.get("number")):
            ep.get("paused_at")
        for show in paused.get("shows", [])
        for season in show.get("seasons", []) or []
        for ep in season.get("episodes", []) or []
    }
    updates: List[Dict] = []
    unwatches: List[Dict] = []
    for kodi_show in kodi_shows.get("shows", []):
        trakt_show = find_in_index(kodi_show, index, match_title_year) or {}
        show_id = (trakt_show.get("ids") or {}).get("trakt")
        reset_at = trakt_show.get("reset_at") if rewatches else None
        played = {}
        for season in trakt_show.get("seasons", []) or []:
            for ep in season.get("episodes", []) or []:
                before = reset_at and (ep.get("last_watched_at") or "") < reset_at
                if ep.get("plays") and not before:
                    played[(season.get("number"), ep.get("number"))] = ep
        for season in kodi_show.get("seasons", []) or []:
            for ep in season.get("episodes", []) or []:
                episodeid = ep.get("ids", {}).get("episodeid")
                if episodeid is None:
                    continue
                key = (season.get("number"), ep.get("number"))
                trakt_ep = played.get(key)
                if trakt_ep:
                    last = _latest(trakt_ep.get("last_watched_at"), paused_at.get((show_id,) + key))
                    if ep.get("plays") != trakt_ep["plays"] or not _same_time(
                            ep.get("watched_at"), last):
                        updates.append({"episodeid": episodeid, "playcount": trakt_ep["plays"],
                                        "lastplayed": utc_to_local(last)})
                elif ep.get("plays") and has_id(kodi_show):
                    unwatches.append({"episodeid": episodeid, "playcount": 0, "lastplayed": ""})
    return updates, unwatches


def _latest(*times: Optional[str]) -> Optional[str]:
    """Latest of Trakt's UTC timestamps, skipping missing ones, or None when all are."""
    known = [t for t in times if t]
    return max(known) if known else None


def _same_time(kodi_utc: Optional[str], trakt_utc: Optional[str]) -> bool:
    """True when Kodi's last play and Trakt's time fall within a minute, or both are unset."""
    if not kodi_utc or not trakt_utc:
        return not kodi_utc and not trakt_utc
    gap = dateutil.parser.parse(kodi_utc) - dateutil.parser.parse(trakt_utc)
    # Trakt keeps watch times to the minute, Kodi to the second
    return abs(gap.total_seconds()) < 60


def needs_resume(item: Dict, kodi_state: Optional[Dict]) -> bool:
    """True when Trakt's paused point differs from Kodi's resume and is newer than its last play."""
    if not kodi_state:
        return True
    resume = kodi_state.get("resume") or {}
    if resume.get("total"):
        kodi_progress = resume.get("position", 0) * 100 / resume["total"]
        if abs(kodi_progress - item.get("progress", 0)) < 1:
            return False
    lastplayed = local_to_utc(kodi_state.get("lastplayed"))
    paused_at = item.get("paused_at")
    return not (lastplayed and paused_at
                and dateutil.parser.parse(lastplayed) > dateutil.parser.parse(paused_at))


def count_episodes(shows: Union[Dict, List]) -> int:
    """Count the episodes in a show list, skipping any marked uncollected."""
    count = 0
    if isinstance(shows, dict) and "shows" in shows:
        shows = shows["shows"]
    for show in shows:
        for season_key in show["seasons"]:
            if season_key is not None and "episodes" in season_key:
                for episode_key in season_key["episodes"]:
                    if episode_key is not None:
                        if "collected" in episode_key and not episode_key["collected"]:
                            continue
                        if "number" in episode_key and episode_key["number"]:
                            count += 1
    return count


def _episodes_by_number(seasons: List) -> Dict:
    """Index seasons by number, each holding its episodes by number."""
    data = {}
    for season in seasons:
        episodes = {}
        for episode in season["episodes"]:
            episodes[episode["number"]] = episode
        data[season["number"]] = episodes

    return data
