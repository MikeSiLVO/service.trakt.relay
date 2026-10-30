"""Episode sync between Kodi and Trakt: collection, watched state, progress and ratings."""

import copy
import logging
from typing import Dict, Tuple, Optional, Any

from lib.media import utilities
from lib.sync.compare import (
    compare_episodes,
    compare_shows,
    episodes_to_mark_watched,
    episodes_to_unwatch,
    count_episodes,
    needs_resume,
    sanitize_shows,
)
from lib.service.queue import ScrobbleQueue
from lib.kodi.client import batch_request, request
from lib.kodi.library import get_play_states, to_trakt_items
from lib.kodi.settings import get_setting_bool, get_string

logger = logging.getLogger(__name__)


class EpisodeSync:
    """Episode half of a sync, each step run when its setting is on."""

    sync: Any

    def __init__(self, sync: Any) -> None:
        self.sync = sync

    def run(self) -> bool:
        """Run each episode step; False when Kodi has no shows or either side fails to load."""
        kodi_shows_collected, kodi_shows_watched = self._load_kodi()
        if kodi_shows_collected is None or kodi_shows_watched is None:
            logger.debug("[Episodes Sync] Kodi show list is empty, aborting tv show Sync.")
            return False

        trakt_shows = self._load_trakt()
        if trakt_shows is None:
            logger.debug(
                "[Episodes Sync] Error getting Trakt.tv show lists, aborting tv show sync."
            )
            return False
        (
            trakt_shows_collected,
            trakt_shows_watched,
            trakt_shows_rated,
            trakt_episodes_rated,
        ) = trakt_shows

        trakt_shows_progress = self._load_trakt_progress(25, 36)

        self._add_to_trakt_collection(
            kodi_shows_collected, trakt_shows_collected, 37, 47
        )

        self._remove_from_trakt_collection(
            trakt_shows_collected, kodi_shows_collected, 48, 58
        )

        self._add_to_trakt_history(kodi_shows_watched, trakt_shows_watched, 59, 69)

        self._mark_watched_in_kodi(
            trakt_shows_watched, kodi_shows_watched, kodi_shows_collected, 70, 80
        )

        self._set_kodi_progress(trakt_shows_progress, kodi_shows_collected, 81, 91)

        self._sync_show_ratings(trakt_shows_rated, kodi_shows_collected, 92, 95)
        self._sync_episode_ratings(trakt_episodes_rated, kodi_shows_collected, 96, 99)

        logger.debug(
            "[Episodes Sync] Shows on Trakt.tv (%d), shows in Kodi (%d)."
            % (len(trakt_shows_collected["shows"]), len(kodi_shows_collected["shows"]))
        )
        logger.debug(
            "[Episodes Sync] Episodes on Trakt.tv (%d), episodes in Kodi (%d)."
            % (
                count_episodes(trakt_shows_collected),
                count_episodes(kodi_shows_collected),
            )
        )
        logger.debug("[Episodes Sync] Complete.")
        return True

    def _load_kodi(self) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Load Kodi's shows with all episodes and watched only; None if empty or unreadable."""
        self.sync.update_progress(1, line1=get_string(32079))

        logger.debug("[Episodes Sync] Getting show data from Kodi")
        data = request(
            "VideoLibrary.GetTVShows", {"properties": ["title", "uniqueid", "year", "userrating"]}
        )
        if not data or data["limits"]["total"] == 0:
            logger.debug("[Episodes Sync] Kodi json request was empty.")
            return None, None

        tvshows = to_trakt_items(data)
        logger.debug("[Episodes Sync] Getting shows from kodi finished %s" % tvshows)

        if tvshows is None:
            return None, None
        result_collected = {"shows": []}
        result_watched = {"shows": []}
        i = 0
        logger.debug("[Episodes Sync] Getting episode data from Kodi")
        for kodi_show in tvshows:
            i += 1
            self.sync.update_count(i, len(tvshows), 2, 10, 32079)

            if "ids" not in kodi_show:
                logger.debug(
                    "[Episodes Sync] Tvshow %s has no uniqueid"
                    % kodi_show["tvshowid"]
                )
                continue

            show = {
                "title": kodi_show["title"],
                "ids": kodi_show["ids"],
                "year": kodi_show["year"],
                "rating": kodi_show["rating"],
                "tvshowid": kodi_show["tvshowid"],
                "seasons": [],
            }

            data = request(
                "VideoLibrary.GetEpisodes",
                {
                    "tvshowid": kodi_show["tvshowid"],
                    "properties": [
                        "season",
                        "episode",
                        "playcount",
                        "uniqueid",
                        "lastplayed",
                        "file",
                        "dateadded",
                        "runtime",
                        "userrating",
                    ],
                },
            )
            if not data:
                logger.debug(
                    "[Episodes Sync] There was a problem getting episode data for '%s', "
                    "aborting sync." % show["title"]
                )
                return None, None
            elif "episodes" not in data:
                logger.debug(
                    "[Episodes Sync] '%s' has no episodes in Kodi." % show["title"]
                )
                continue

            if "tvshowid" in kodi_show:
                del kodi_show["tvshowid"]

            show_watched = dict(show)
            data2 = copy.deepcopy(data)
            show["seasons"] = to_trakt_items(data)

            show_watched["seasons"] = to_trakt_items(
                data2, "watched"
            )

            result_collected["shows"].append(show)
            result_watched["shows"].append(show_watched)

        return result_collected, result_watched

    def _load_trakt(self) -> Optional[Tuple[Dict, Dict, Dict, Dict]]:
        """Load collected, watched and, if ratings sync is on, rated Trakt shows; None on error."""
        self.sync.update_progress(10, line1=get_string(32081))

        logger.debug(
            "[Episodes Sync] Getting episode collection/watched/rated from Trakt.tv"
        )
        trakt_shows_collected = self.sync.api.get_collected_shows({})
        self.sync.update_progress(15, line1=get_string(32082))
        trakt_shows_watched = self.sync.api.get_watched_shows({})
        trakt_shows_rated: Optional[Dict] = {}
        trakt_episodes_rated: Optional[Dict] = {}
        if get_setting_bool("sync_ratings_to_trakt") or get_setting_bool("sync_ratings_to_kodi"):
            self.sync.update_progress(20, line1=get_string(32216))
            trakt_shows_rated = self.sync.api.get_rated_shows({})
            trakt_episodes_rated = self.sync.api.get_rated_episodes({})
        if (
            trakt_shows_collected is None
            or trakt_shows_watched is None
            or trakt_shows_rated is None
            or trakt_episodes_rated is None
        ):
            logger.debug(
                "[Episodes Sync] Invalid Trakt.tv show list, possible error getting data "
                "from Trakt, aborting Trakt.tv collection/watched/rated update."
            )
            return None

        return (
            {"shows": [show.to_dict() for show in trakt_shows_collected.values()]},
            {"shows": [show.to_dict() for show in trakt_shows_watched.values()]},
            {"shows": [show.to_dict() for show in trakt_shows_rated.values()]},
            {"shows": [show.to_dict() for show in trakt_episodes_rated.values()]},
        )

    def _load_trakt_progress(
        self, from_percent: int, to_percent: int
    ) -> Optional[Dict]:
        """Load paused episodes from Trakt; None when playback sync is off, canceled or failing."""
        if (
            get_setting_bool("trakt_episode_playback")
            and not self.sync.is_canceled()
        ):
            self.sync.update_progress(from_percent, line1=get_string(32122))

            logger.debug("[Playback Sync] Getting playback progress from Trakt.tv")
            trakt_progress_shows = self.sync.api.get_episode_playback_progress()
            if trakt_progress_shows is None:
                logger.debug(
                    "[Playback Sync] Invalid Trakt.tv progress list, possible error getting "
                    "data from Trakt, aborting Trakt.tv playback update."
                )
                return None

            self.sync.update_progress(to_percent, line1=get_string(32122))
            return {"shows": [show.to_dict() for show in trakt_progress_shows]}

    def _add_to_trakt_collection(
        self, kodi_shows: Dict, trakt_shows: Dict, from_percent: int, to_percent: int
    ) -> None:
        """Add Kodi episodes missing from the Trakt collection, when that sync is on."""
        if (
            get_setting_bool("sync_collection_episodes_to_trakt")
            and not self.sync.is_canceled()
        ):
            trakt_shows_add = compare_episodes(
                kodi_shows,
                trakt_shows,
                get_setting_bool("scrobble_fallback"),
            )
            sanitize_shows(trakt_shows_add)

            if len(trakt_shows_add["shows"]) == 0:
                self.sync.update_progress(
                    to_percent,
                    line1=get_string(32068),
                    line2=get_string(32086),
                )
                logger.debug(
                    "[Episodes Sync] Trakt.tv episode collection is up to date."
                )
                return
            logger.debug(
                "[Episodes Sync] %i show(s) have episodes (%d) to be added to your "
                "Trakt.tv collection."
                % (len(trakt_shows_add["shows"]), count_episodes(trakt_shows_add))
            )
            for show in trakt_shows_add["shows"]:
                logger.debug(
                    "[Episodes Sync] Episodes added: %s"
                    % self._describe(show)
                )

            self.sync.send(self.sync.api.add_to_collection, trakt_shows_add["shows"], "episodes",
                           32068, 32085, from_percent, to_percent)

    def _remove_from_trakt_collection(
        self, trakt_shows: Dict, kodi_shows: Dict, from_percent: int, to_percent: int
    ) -> None:
        """Remove collected Trakt episodes missing from Kodi, when collection cleaning is on."""
        if (
            get_setting_bool("sync_clean_collection_episodes_to_trakt")
            and not self.sync.is_canceled()
        ):
            trakt_shows_remove = compare_episodes(
                trakt_shows,
                kodi_shows,
                get_setting_bool("scrobble_fallback"),
            )
            sanitize_shows(trakt_shows_remove)

            if len(trakt_shows_remove["shows"]) == 0:
                self.sync.update_progress(
                    to_percent,
                    line1=get_string(32068),
                    line2=get_string(32086),
                )
                logger.debug(
                    "[Episodes Sync] Trakt.tv episode collection is clean, no episodes to remove."
                )
                return

            logger.debug(
                "[Episodes Sync] %i show(s) will have episodes removed from Trakt.tv collection."
                % len(trakt_shows_remove["shows"])
            )
            for show in trakt_shows_remove["shows"]:
                logger.debug(
                    "[Episodes Sync] Episodes removed: %s"
                    % self._describe(show)
                )
            self.sync.send(self.sync.api.remove_from_collection, trakt_shows_remove["shows"],
                           "episodes", 32068, 32092, from_percent, to_percent)

    def _add_to_trakt_history(
        self, kodi_shows: Dict, trakt_shows: Dict, from_percent: int, to_percent: int
    ) -> None:
        """Add episodes watched in Kodi but not on Trakt to its history, when that sync is on."""
        if (
            get_setting_bool("sync_playcount_episodes_to_trakt")
            and not self.sync.is_canceled()
        ):
            trakt_shows_update = compare_episodes(
                kodi_shows,
                trakt_shows,
                get_setting_bool("scrobble_fallback"),
                watched=True,
            )
            sanitize_shows(trakt_shows_update)

            if len(trakt_shows_update["shows"]) == 0:
                self.sync.update_progress(
                    to_percent,
                    line1=get_string(32071),
                    line2=get_string(32086),
                )
                logger.debug(
                    "[Episodes Sync] Trakt.tv episode playcounts are up to date."
                )
                return

            logger.debug(
                "[Episodes Sync] %i show(s) are missing playcounts on Trakt.tv"
                % len(trakt_shows_update["shows"])
            )
            for show in trakt_shows_update["shows"]:
                logger.debug(
                    "[Episodes Sync] Episodes updated: %s"
                    % self._describe(show)
                )
            self.sync.send(self.sync.api.add_to_history, trakt_shows_update["shows"], "episodes",
                           32071, 32085, from_percent, to_percent)

    def _mark_watched_in_kodi(
        self, trakt_shows: Dict, kodi_shows: Dict, kodi_shows_collected: Dict,
        from_percent: int, to_percent: int
    ) -> None:
        """Mark Kodi episodes watched when Trakt has more plays; in a rewatch, unwatch the rest."""
        if not (
            get_setting_bool("kodi_episode_playcount")
            and not self.sync.is_canceled()
        ):
            return

        rewatches = get_setting_bool("rewatch_aware_sync")
        episodes = episodes_to_mark_watched(
            trakt_shows,
            kodi_shows_collected,
            get_setting_bool("scrobble_fallback"),
            apply_rewatch_filter=rewatches,
        )
        # unwatch only while every Kodi play reaches Trakt
        if rewatches and get_setting_bool("scrobble_episode") and not len(ScrobbleQueue()):
            episodes += episodes_to_unwatch(
                trakt_shows,
                kodi_shows,
                get_setting_bool("scrobble_fallback"),
                # the history push sends these this sync
                keep_unplayed=get_setting_bool("sync_playcount_episodes_to_trakt"),
            )

        if len(episodes) == 0:
            self.sync.update_progress(
                to_percent,
                line1=get_string(32074),
                line2=get_string(32086),
            )
            logger.debug("[Episodes Sync] Kodi episode playcounts are up to date.")
            return

        logger.debug(
            "[Episodes Sync] %i episode(s) need playcount update in Kodi"
            % len(episodes)
        )

        chunksize = 50
        chunked_episodes = utilities.chunks(
            episodes,
            chunksize,
        )
        i = 0
        total = len(episodes)
        for chunk in chunked_episodes:
            if self.sync.is_canceled():
                return
            i += 1
            self.sync.update_count(
                min(i * chunksize, total), total, from_percent, to_percent, 32074)

            logger.debug("[Episodes Sync] chunk %s" % str(chunk))
            result = batch_request("VideoLibrary.SetEpisodeDetails", chunk)
            logger.debug("[Episodes Sync] result %s" % str(result))

        self.sync.update_progress(
            to_percent, line1=get_string(32074), line2=get_string(32090) % len(episodes)
        )

    def _set_kodi_progress(
        self, trakt_shows: Optional[Dict], kodi_shows: Dict, from_percent: int, to_percent: int
    ) -> None:
        """Set Kodi resume points from Trakt's paused-episode progress, when playback sync is on."""
        if (
            get_setting_bool("trakt_episode_playback")
            and trakt_shows
            and not self.sync.is_canceled()
        ):
            kodi_shows_update = compare_episodes(
                trakt_shows,
                kodi_shows,
                get_setting_bool("scrobble_fallback"),
                restrict=True,
                playback=True,
            )
            paused = [
                (show, season, episode)
                for show in kodi_shows_update["shows"]
                for season in show["seasons"]
                for episode in season["episodes"]
            ]
            states = get_play_states("episode", [e["ids"]["episodeid"] for _, _, e in paused])
            paused = [item for item, state in zip(paused, states) if needs_resume(item[2], state)]

            if not paused:
                self.sync.update_progress(
                    to_percent,
                    line1=get_string(32206),
                    line2=get_string(32086),
                )
                logger.debug("[Episodes Sync] Kodi episode progress is up to date.")
                return

            logger.debug("[Episodes Sync] %i episode(s) are missing progress in Kodi" % len(paused))
            for show, season, episode in paused:
                logger.debug("[Episodes Sync] Episode updated: %s S%02dE%02d"
                             % (show["title"], season["number"], episode["number"]))

            self.sync.update_progress(from_percent, line1=get_string(32206))
            episodes = []
            for show, season, episode in paused:
                if not episode["runtime"]:
                    summary = self.sync.api.get_episode_summary(
                        show["ids"]["trakt"],
                        season["number"],
                        episode["number"],
                        extended="full",
                    )
                    episode["runtime"] = (summary.to_dict().get("runtime") or 0) * 60
                episodes.append(
                    {
                        "episodeid": episode["ids"]["episodeid"],
                        "progress": episode["progress"],
                        "runtime": episode["runtime"],
                    }
                )

            chunksize = 50
            chunked_episodes = utilities.chunks(
                [
                    {
                        "episodeid": episodes[i]["episodeid"],
                        "resume": {
                            "position": episodes[i]["runtime"]
                            / 100.0
                            * episodes[i]["progress"],
                            "total": episodes[i]["runtime"],
                        },
                    }
                    for i in range(len(episodes))
                    if episodes[i]["runtime"] > 0
                ],
                chunksize,
            )
            i = 0
            total = len(episodes)
            for chunk in chunked_episodes:
                if self.sync.is_canceled():
                    return
                i += 1
                self.sync.update_count(
                    min(i * chunksize, total), total, from_percent, to_percent, 32206)

                batch_request("VideoLibrary.SetEpisodeDetails", chunk)

            self.sync.update_progress(
                to_percent, line1=get_string(32206), line2=get_string(32090) % len(episodes)
            )

    def _sync_show_ratings(
        self, trakt_shows: Dict, kodi_shows: Dict, from_percent: int, to_percent: int
    ) -> None:
        """Sync show ratings to Trakt and to Kodi, each direction when its setting is on."""
        if (
            get_setting_bool("sync_ratings_to_trakt")
            and trakt_shows
            and not self.sync.is_canceled()
        ):
            trakt_shows_to_update = compare_shows(
                kodi_shows,
                trakt_shows,
                get_setting_bool("scrobble_fallback"),
            )
            if len(trakt_shows_to_update["shows"]) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32179), line2=get_string(32086)
                )
                logger.debug("[Episodes Sync] Trakt show ratings are up to date.")
            else:
                logger.debug(
                    "[Episodes Sync] %i show(s) will have show ratings added on Trakt"
                    % len(trakt_shows_to_update["shows"])
                )
                self.sync.send(self.sync.api.add_ratings, trakt_shows_to_update["shows"], "shows",
                               32179, 32085, from_percent, to_percent)

        if (
            get_setting_bool("sync_ratings_to_kodi")
            and trakt_shows
            and not self.sync.is_canceled()
        ):
            kodi_shows_update = compare_shows(
                trakt_shows,
                kodi_shows,
                get_setting_bool("scrobble_fallback"),
                restrict=True,
            )

            if len(kodi_shows_update["shows"]) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32169), line2=get_string(32086)
                )
                logger.debug("[Episodes Sync] Kodi show ratings are up to date.")
            else:
                logger.debug(
                    "[Episodes Sync] %i show(s) will have show ratings added in Kodi"
                    % len(kodi_shows_update["shows"])
                )

                shows = []
                for show in kodi_shows_update["shows"]:
                    shows.append(
                        {"tvshowid": show["tvshowid"], "rating": show["rating"]}
                    )

                chunksize = 50
                chunked_episodes = utilities.chunks(
                    [
                        {
                            "tvshowid": shows[i]["tvshowid"],
                            "userrating": int(shows[i]["rating"]),
                        }
                        for i in range(len(shows))
                    ],
                    chunksize,
                )
                i = 0
                total = len(shows)
                for chunk in chunked_episodes:
                    if self.sync.is_canceled():
                        return
                    i += 1
                    self.sync.update_count(
                        min(i * chunksize, total), total, from_percent, to_percent, 32169)

                    batch_request("VideoLibrary.SetTVShowDetails", chunk)

                self.sync.update_progress(
                    to_percent, line1=get_string(32169), line2=get_string(32090) % len(shows)
                )

    def _sync_episode_ratings(
        self, trakt_shows: Dict, kodi_shows: Dict, from_percent: int, to_percent: int
    ) -> None:
        """Sync episode ratings to Trakt and to Kodi, each direction when its setting is on."""
        if (
            get_setting_bool("sync_ratings_to_trakt")
            and trakt_shows
            and not self.sync.is_canceled()
        ):
            trakt_shows_to_update = compare_episodes(
                kodi_shows,
                trakt_shows,
                get_setting_bool("scrobble_fallback"),
                rating=True,
            )
            if len(trakt_shows_to_update["shows"]) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32179), line2=get_string(32086)
                )
                logger.debug("[Episodes Sync] Trakt episode ratings are up to date.")
            else:
                logger.debug(
                    "[Episodes Sync] %i show(s) will have episode ratings added on Trakt"
                    % len(trakt_shows_to_update["shows"])
                )
                self.sync.send(self.sync.api.add_ratings, trakt_shows_to_update["shows"],
                               "episodes", 32179, 32085, from_percent, to_percent)

        if (
            get_setting_bool("sync_ratings_to_kodi")
            and trakt_shows
            and not self.sync.is_canceled()
        ):
            kodi_shows_update = compare_episodes(
                trakt_shows,
                kodi_shows,
                get_setting_bool("scrobble_fallback"),
                restrict=True,
                rating=True,
            )
            if len(kodi_shows_update["shows"]) == 0:
                self.sync.update_progress(
                    to_percent, line1=get_string(32169), line2=get_string(32086)
                )
                logger.debug("[Episodes Sync] Kodi episode ratings are up to date.")
            else:
                logger.debug(
                    "[Episodes Sync] %i show(s) will have episode ratings added in Kodi"
                    % len(kodi_shows_update["shows"])
                )
                for s in [
                    "%s" % self._describe(s)
                    for s in kodi_shows_update["shows"]
                ]:
                    logger.debug("[Episodes Sync] Episodes updated: %s" % s)

                episodes = []
                for show in kodi_shows_update["shows"]:
                    for season in show["seasons"]:
                        for episode in season["episodes"]:
                            episodes.append(
                                {
                                    "episodeid": episode["ids"]["episodeid"],
                                    "rating": episode["rating"],
                                }
                            )

                chunksize = 50
                chunked_episodes = utilities.chunks(
                    [
                        {
                            "episodeid": episodes[i]["episodeid"],
                            "userrating": int(episodes[i]["rating"]),
                        }
                        for i in range(len(episodes))
                    ],
                    chunksize,
                )
                i = 0
                total = len(episodes)
                for chunk in chunked_episodes:
                    if self.sync.is_canceled():
                        return
                    i += 1
                    self.sync.update_count(
                        min(i * chunksize, total), total, from_percent, to_percent, 32169)

                    batch_request("VideoLibrary.SetEpisodeDetails", chunk)

                self.sync.update_progress(
                    to_percent, line1=get_string(32169), line2=get_string(32090) % len(episodes)
                )

    def _describe(self, show: Dict) -> str:
        """Describe a show and its episodes as SxxEyy codes for the log."""
        codes = [
            "S%02dE%02d" % (season["number"], episode["number"])
            for season in show.get("seasons", [])
            for episode in season["episodes"]
        ]
        tvdb = show["ids"].get("tvdb", "No id")
        return "%s [tvdb: %s] - %s" % (show["title"], tvdb, ", ".join(codes) or "All")
