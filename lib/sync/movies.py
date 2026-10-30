"""Movie sync between Kodi and Trakt: collection, watched state, progress and ratings."""

import logging
from typing import Dict, List, Optional, Any

from lib.media import utilities
from lib.media.ids import has_id
from lib.sync.compare import (compare_movies, movies_to_mark_watched, movies_to_mirror,
                              needs_resume, sanitize_movies)
from lib.service.queue import ScrobbleQueue
from lib.kodi.client import batch_request, request
from lib.kodi.library import get_play_states, to_trakt_items
from lib.kodi.settings import (ADD_AND_REMOVE, BOTH_WAYS, TRAKT_TO_KODI, get_setting_bool,
                               get_setting_int, get_string, syncs_to_kodi, syncs_to_trakt)

logger = logging.getLogger(__name__)


class MovieSync:
    """Movie half of a sync, each step run when its setting is on."""

    sync: Any

    def __init__(self, sync: Any) -> None:
        self.sync = sync

    def run(self) -> bool:
        """Run each movie step; False when Kodi has no movies or either side fails to load."""
        kodi_movies = self._load_kodi()
        if kodi_movies is None:
            logger.debug("[Movies Sync] Kodi movie list is empty, aborting movie Sync.")
            return False
        trakt_movies = self._load_trakt()
        if trakt_movies is None:
            logger.debug(
                "[Movies Sync] Error getting Trakt.tv movie list, aborting movie Sync."
            )
            return False

        trakt_movies_progress = self._load_trakt_progress(25, 36)

        self._add_to_trakt_collection(kodi_movies, trakt_movies, 37, 47)

        self._remove_from_trakt_collection(trakt_movies, kodi_movies, 48, 58)

        self._add_to_trakt_history(kodi_movies, trakt_movies, 59, 69)

        self._mark_watched_in_kodi(trakt_movies, kodi_movies, trakt_movies_progress, 70, 80)

        self._set_kodi_progress(trakt_movies_progress, kodi_movies, 81, 91)

        self._sync_ratings(trakt_movies, kodi_movies, 92, 99)

        logger.debug(
            "[Movies Sync] Movies on Trakt.tv (%d), movies in Kodi (%d)."
            % (len(trakt_movies), len(kodi_movies))
        )
        logger.debug("[Movies Sync] Complete.")
        return True

    def _load_kodi(self) -> Optional[List[Dict]]:
        """Load Kodi's movies in Trakt form, or None when the library is empty or unreadable."""
        self.sync.update_progress(1, line1=get_string(32079))

        logger.debug("[Movies Sync] Getting movie data from Kodi")
        data = request(
            "VideoLibrary.GetMovies",
            {
                "properties": [
                    "title",
                    "uniqueid",
                    "year",
                    "playcount",
                    "lastplayed",
                    "file",
                    "dateadded",
                    "runtime",
                    "userrating",
                ]
            },
        )
        if not data or data["limits"]["total"] == 0:
            logger.debug("[Movies Sync] Kodi JSON request was empty.")
            return

        return to_trakt_items(data)

    def _load_trakt(self) -> Optional[List[Dict]]:
        """Load collected, watched and, if synced, rated Trakt movies; None on error or cancel."""
        self.sync.update_progress(10, line1=get_string(32081))

        logger.debug("[Movies Sync] Getting movie collection from Trakt.tv")

        trakt_movies = self.sync.api.get_collected_movies({})
        if trakt_movies is not None and not self.sync.is_canceled():
            self.sync.update_progress(17, line1=get_string(32082))
            trakt_movies = self.sync.api.get_watched_movies(trakt_movies)
        if trakt_movies is not None and not self.sync.is_canceled() and get_setting_int(
            "sync_ratings"
        ):
            self.sync.update_progress(21, line1=get_string(32216))
            trakt_movies = self.sync.api.get_rated_movies(trakt_movies)
        if trakt_movies is None or self.sync.is_canceled():
            return None

        return [movie.to_dict() for movie in trakt_movies.values()]

    def _load_trakt_progress(
        self, from_percent: int, to_percent: int
    ) -> Optional[Dict]:
        """Load paused movies for resume points and mirror dates; None if skipped or failing."""
        if (
            get_setting_bool("trakt_movie_playback")
            or get_setting_int("sync_watched_movies") == TRAKT_TO_KODI
        ) and not self.sync.is_canceled():
            self.sync.update_progress(from_percent, line1=get_string(32122))

            logger.debug("[Movies Sync] Getting playback progress from Trakt.tv")
            trakt_progress_movies = self.sync.api.get_movie_playback_progress()
            if trakt_progress_movies is None:
                logger.debug(
                    "[Movies Sync] Invalid Trakt.tv playback progress list, possible error "
                    "getting data from Trakt, aborting Trakt.tv playback update."
                )
                return None

            self.sync.update_progress(to_percent, line1=get_string(32122))
            return {"movies": [movie.to_dict() for movie in trakt_progress_movies]}

    def _add_to_trakt_collection(
        self, kodi_movies: List[Dict], trakt_movies: List[Dict], from_percent: int, to_percent: int
    ) -> None:
        """Add Kodi movies missing from the Trakt collection, when that sync is on."""
        if get_setting_int("sync_collection_movies") and not self.sync.is_canceled():
            trakt_movies_to_add = compare_movies(
                kodi_movies,
                trakt_movies,
                get_setting_bool("scrobble_fallback"),
            )
            sanitize_movies(trakt_movies_to_add)
            logger.debug(
                "[Movies Sync] Compared movies, found %s to add."
                % len(trakt_movies_to_add)
            )

            if len(trakt_movies_to_add) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32068), line2=get_string(32086)
                )
                logger.debug("[Movies Sync] Trakt.tv movie collection is up to date.")
                return

            titles = ", ".join(["%s" % (m["title"]) for m in trakt_movies_to_add])
            logger.debug(
                "[Movies Sync] %i movie(s) will be added to Trakt.tv collection."
                % len(trakt_movies_to_add)
            )
            logger.debug("[Movies Sync] Movies to add : %s" % titles)
            self.sync.send(self.sync.api.add_to_collection, trakt_movies_to_add, "movies",
                           32068, 32085, from_percent, to_percent)

    def _remove_from_trakt_collection(
        self, trakt_movies: List[Dict], kodi_movies: List[Dict], from_percent: int, to_percent: int
    ) -> None:
        """Remove collected Trakt movies missing from Kodi, when collection sync removes too."""
        if (
            get_setting_int("sync_collection_movies") == ADD_AND_REMOVE
            and not self.sync.is_canceled()
        ):
            logger.debug("[Movies Sync] Starting to remove.")
            trakt_movies_to_remove = compare_movies(
                trakt_movies,
                kodi_movies,
                get_setting_bool("scrobble_fallback"),
            )
            sanitize_movies(trakt_movies_to_remove)
            logger.debug(
                "[Movies Sync] Compared movies, found %s to remove."
                % len(trakt_movies_to_remove)
            )

            if len(trakt_movies_to_remove) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32068), line2=get_string(32086)
                )
                logger.debug(
                    "[Movies Sync] Trakt.tv movie collection is clean, no movies to remove."
                )
                return

            titles = ", ".join(["%s" % (m["title"]) for m in trakt_movies_to_remove])
            logger.debug(
                "[Movies Sync] %i movie(s) will be removed from Trakt.tv collection."
                % len(trakt_movies_to_remove)
            )
            logger.debug("[Movies Sync] Movies removed: %s" % titles)
            self.sync.send(self.sync.api.remove_from_collection, trakt_movies_to_remove, "movies",
                           32068, 32092, from_percent, to_percent)

    def _add_to_trakt_history(
        self, kodi_movies: List[Dict], trakt_movies: List[Dict], from_percent: int, to_percent: int
    ) -> None:
        """Add movies watched in Kodi but not on Trakt to its history, per the watched setting."""
        if syncs_to_trakt("sync_watched_movies") and not self.sync.is_canceled():
            trakt_movies_to_update = compare_movies(
                kodi_movies,
                trakt_movies,
                get_setting_bool("scrobble_fallback"),
                watched=True,
            )
            sanitize_movies(trakt_movies_to_update)

            if len(trakt_movies_to_update) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32071), line2=get_string(32086)
                )
                logger.debug("[Movies Sync] Trakt.tv movie playcount is up to date")
                return

            titles = ", ".join(["%s" % (m["title"]) for m in trakt_movies_to_update])
            logger.debug(
                "[Movies Sync] %i movie(s) playcount will be updated on Trakt.tv"
                % len(trakt_movies_to_update)
            )
            logger.debug("[Movies Sync] Movies updated: %s" % titles)
            self.sync.send(self.sync.api.add_to_history, trakt_movies_to_update, "movies",
                           32071, 32085, from_percent, to_percent)

    def _mark_watched_in_kodi(
        self, trakt_movies: List[Dict], kodi_movies: List[Dict], trakt_progress: Optional[Dict],
        from_percent: int, to_percent: int
    ) -> None:
        """Mark Kodi movies watched as on Trakt, every copy; mirroring also unwatches the rest."""
        if not syncs_to_kodi("sync_watched_movies") or self.sync.is_canceled():
            return

        fallback = get_setting_bool("scrobble_fallback")
        if get_setting_int("sync_watched_movies") == TRAKT_TO_KODI:
            movie_updates, unwatches = movies_to_mirror(
                trakt_movies, kodi_movies, fallback, (trakt_progress or {}).get("movies", []))
            if unwatches and len(ScrobbleQueue()):
                logger.info("[Movies Sync] Unwatching waits for failed scrobbles to be retried")
            else:
                movie_updates += self.sync.confirm_removals(unwatches, 32264)
        else:
            movie_updates = movies_to_mark_watched(trakt_movies, kodi_movies, fallback)

        if len(movie_updates) == 0:
            self.sync.update_progress(
                to_percent, line1=get_string(32074), line2=get_string(32086)
            )
            logger.debug("[Movies Sync] Kodi movie playcount is up to date.")
            return

        logger.debug(
            "[Movies Sync] %i movie(s) playcount will be updated in Kodi"
            % len(movie_updates)
        )

        chunksize = 50
        chunked_movies = utilities.chunks(
            movie_updates,
            chunksize,
        )
        i = 0
        total = len(movie_updates)
        for chunk in chunked_movies:
            if self.sync.is_canceled():
                return
            i += 1
            self.sync.update_count(
                min(i * chunksize, total), total, from_percent, to_percent, 32074)

            batch_request("VideoLibrary.SetMovieDetails", chunk)

        self.sync.update_progress(
            to_percent,
            line1=get_string(32074),
            line2=get_string(32090) % len(movie_updates),
        )

    def _set_kodi_progress(
        self, trakt_movies: Optional[Dict], kodi_movies: List[Dict], from_percent: int,
        to_percent: int
    ) -> None:
        """Set Kodi resume points from Trakt's paused-movie progress, when playback sync is on."""
        if (
            get_setting_bool("trakt_movie_playback")
            and trakt_movies
            and not self.sync.is_canceled()
        ):
            kodi_movies_to_update = compare_movies(
                trakt_movies["movies"],
                kodi_movies,
                get_setting_bool("scrobble_fallback"),
                restrict=True,
                playback=True,
            )
            states = get_play_states("movie", [m["movieid"] for m in kodi_movies_to_update])
            kodi_movies_to_update = [
                movie for movie, state in zip(kodi_movies_to_update, states)
                if needs_resume(movie, state)
            ]
            if len(kodi_movies_to_update) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32206), line2=get_string(32086)
                )
                logger.debug("[Movies Sync] Kodi movie progress is up to date.")
                return

            logger.debug(
                "[Movies Sync] %i movie(s) progress will be updated in Kodi"
                % len(kodi_movies_to_update)
            )

            self.sync.update_progress(from_percent, line1=get_string(32206))
            for movie in kodi_movies_to_update:
                if self.sync.is_canceled():
                    return
                if not movie["runtime"]:
                    summary = self.sync.api.get_movie_summary(
                        movie["ids"]["trakt"], extended="full"
                    )
                    movie["runtime"] = (summary.to_dict().get("runtime") or 0) * 60
            chunksize = 50
            chunked_movies = utilities.chunks(
                [
                    {
                        "movieid": kodi_movies_to_update[i]["movieid"],
                        "resume": {
                            "position": kodi_movies_to_update[i]["runtime"]
                            / 100.0
                            * kodi_movies_to_update[i]["progress"],
                            "total": kodi_movies_to_update[i]["runtime"],
                        },
                    }
                    for i in range(len(kodi_movies_to_update))
                    if kodi_movies_to_update[i]["runtime"] > 0
                ],
                chunksize,
            )
            i = 0
            total = len(kodi_movies_to_update)
            for chunk in chunked_movies:
                if self.sync.is_canceled():
                    return
                i += 1
                self.sync.update_count(
                    min(i * chunksize, total), total, from_percent, to_percent, 32206)
                batch_request("VideoLibrary.SetMovieDetails", chunk)

            self.sync.update_progress(
                to_percent,
                line1=get_string(32206),
                line2=get_string(32090) % len(kodi_movies_to_update),
            )

    def _sync_ratings(
        self, trakt_movies: List[Dict], kodi_movies: List[Dict], from_percent: int, to_percent: int
    ) -> None:
        """Sync movie ratings the chosen way; mirroring also clears Kodi ratings Trakt lacks."""
        direction = get_setting_int("sync_ratings")
        fallback = get_setting_bool("scrobble_fallback")
        if syncs_to_trakt("sync_ratings") and not self.sync.is_canceled():
            # with both ways Trakt's rating wins where both sides have one
            trakt_movies_to_update = compare_movies(
                kodi_movies, trakt_movies, fallback, rating=True,
                unrated_only=direction == BOTH_WAYS)
            if len(trakt_movies_to_update) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32179), line2=get_string(32086)
                )
                logger.debug("[Movies Sync] Trakt movie ratings are up to date.")
            else:
                logger.debug(
                    "[Movies Sync] %i movie(s) ratings will be updated on Trakt"
                    % len(trakt_movies_to_update)
                )
                self.sync.send(self.sync.api.add_ratings, trakt_movies_to_update, "movies",
                               32179, 32085, from_percent, to_percent)

        if syncs_to_kodi("sync_ratings") and not self.sync.is_canceled():
            kodi_movies_to_update = compare_movies(
                trakt_movies, kodi_movies, fallback, restrict=True, rating=True)
            if direction == TRAKT_TO_KODI:
                cleared = [dict(movie, rating=0) for movie in compare_movies(
                    kodi_movies, trakt_movies, fallback, rating=True, unrated_only=True)
                    if has_id(movie)]
                kodi_movies_to_update += self.sync.confirm_removals(cleared, 32265)
            if len(kodi_movies_to_update) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32169), line2=get_string(32086)
                )
                logger.debug("[Movies Sync] Kodi movie ratings are up to date.")
            else:
                logger.debug(
                    "[Movies Sync] %i movie(s) ratings will be updated in Kodi"
                    % len(kodi_movies_to_update)
                )

                chunksize = 50
                chunked_movies = utilities.chunks(
                    [
                        {
                            "movieid": kodi_movies_to_update[i]["movieid"],
                            "userrating": int(kodi_movies_to_update[i]["rating"]),
                        }
                        for i in range(len(kodi_movies_to_update))
                    ],
                    chunksize,
                )
                i = 0
                total = len(kodi_movies_to_update)
                for chunk in chunked_movies:
                    if self.sync.is_canceled():
                        return
                    i += 1
                    self.sync.update_count(
                        min(i * chunksize, total), total, from_percent, to_percent, 32169)
                    batch_request("VideoLibrary.SetMovieDetails", chunk)

                self.sync.update_progress(
                    to_percent,
                    line1=get_string(32169),
                    line2=get_string(32090) % len(kodi_movies_to_update),
                )
