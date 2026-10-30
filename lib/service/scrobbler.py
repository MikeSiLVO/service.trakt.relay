"""Scrobbling the playing video to Trakt, with failed stops queued for retry."""
import xbmc
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from lib.media import utilities
from lib.kodi.utilities import notify
import math
from lib.dialogs.rating import check_rating
from lib.service.player import get_playing_item, identify
from lib.service.actions import find_trakt_id
from lib.service.queue import ScrobbleQueue
from lib.trakt import shared
from lib.media.dates import to_seconds, local_to_utc
from lib.media.ids import pick_best_id
from lib.media.match import fuzzy_match, split_title_year
from lib.kodi.library import (
    get_episode_details,
    get_movie_details,
    to_trakt_item,
)
from lib.kodi.settings import get_setting_bool, get_string

logger = logging.getLogger(__name__)

SEEK_SETTLE = 2


class Scrobbler:
    """State of the playing video, with its start, pause and stop scrobbled to Trakt."""
    def __init__(self) -> None:
        self.api = shared.api
        self.scrobble_queue = ScrobbleQueue()
        self.playing = False
        self.paused = False
        self.stop_pending = False
        self.pvr = False
        self.multi_part = False
        self.last_check = 0
        self.part = 0
        self.duration = 1
        self.watched_time = 0
        self.paused_at = 0
        self.video: Optional[Dict[str, Any]] = None
        self.video_info: Optional[Dict[str, Any]] = None
        self.playlist_index = 0
        self.show_info: Optional[Dict[str, Any]] = None
        self.to_rate: List[Dict[str, Any]] = []
        self.seek_due: Optional[float] = None
        self.program: Optional[Tuple[str, ...]] = None

    def _part_at(self, percent: float, count: int) -> int:
        """Index of the multi-part episode's part at the watched percent."""
        split = 100 / count
        for i in range(count - 1, 0, -1):
            if percent >= (i * split):
                return i
        return 0

    def check_transition(self) -> None:
        """Update watched time and follow live TV; scrobble part changes and settled seeks."""
        if not xbmc.Player().isPlayingVideo():
            return

        if self.playing:
            elapsed = xbmc.Player().getTime()
            position = xbmc.PlayList(xbmc.PLAYLIST_VIDEO).getposition()
            if self.pvr:
                if self.stop_pending:
                    self.stop_pending = False
                    self.last_check = time.time() + 600
                    self._scrobble("stop")
                    return
                labels = _program_labels()
                if labels[0] and labels != self.program:
                    self._change_program(labels)
                    return
                self.watched_time = to_seconds(
                    xbmc.getInfoLabel("PVR.EpgEventElapsedTime(hh:mm:ss)")
                )
                self.duration = int(
                    to_seconds(
                        xbmc.getInfoLabel("PVR.EpgEventDuration(hh:mm:ss)")
                    )
                )
            elif self.playlist_index == position:
                self.watched_time = elapsed
                # player may not know the duration at start
                if self.duration == 0:
                    self.duration = xbmc.Player().getTotalTime()
            else:
                logger.debug(
                    "Current playlist item changed! Not updating time! (%d -> %d)"
                    % (self.playlist_index, position)
                )

            seek = self.seek_due is not None and time.time() >= self.seek_due
            if seek:
                self.seek_due = None
            if (time.time() > (self.last_check + 60)) or seek:
                self.last_check = time.time()
                percent = self._watched_percent()

                if self.video and "id" in self.video and self.multi_part:
                    part = self._part_at(
                        percent, self.video["multi_episode_count"]
                    )
                    if self.part != part:
                        # advance even if the stop fails, or every later part is lost
                        response = self._scrobble("stop")
                        logger.debug("Scrobble response: %s" % str(response))
                        if self.video_info is not None:
                            self.to_rate.append(self.video_info)
                        self.part = part
                        episode_details = get_episode_details(
                            self.video["multi_episode_data"][
                                self.part
                            ],
                            [
                                "showtitle",
                                "season",
                                "episode",
                                "tvshowid",
                                "uniqueid",
                                "file",
                                "playcount",
                            ],
                        )
                        if episode_details:
                            self.video_info = to_trakt_item(
                                "episode", episode_details
                            )
                        else:
                            self.video_info = None

                        logger.debug(
                            "Multi episode transition - call start for next episode"
                        )
                        self._prefetch_rating(self._scrobble_start())

                elif seek:
                    self._scrobble_restart()

    def _change_program(self, labels: Tuple[str, ...]) -> None:
        """Change to the live TV program now on: stop the old one, start the new if identified."""
        logger.debug("Live TV program changed: %s" % (labels,))
        self._scrobble("stop")
        self.program = labels
        result = get_playing_item()
        event = identify(result) if result else None
        if not event:
            self.video_info = None
            return
        self.playback_started({k: v for k, v in event.items() if k != "action"})

    def playback_started(self, data: Dict) -> None:
        """Load the item, record it for recovery, and scrobble its start or fetch it to rate."""
        logger.debug("Playback started: %s" % data)
        if not data:
            return
        self.video = data
        self.video_info = None
        self.to_rate = []
        self.seek_due = None
        playcount = None

        if (
            not get_setting_bool("scrobble_fallback")
            and "id" not in self.video
            and "video_ids" not in self.video
        ):
            logger.debug("Aborting scrobble to avoid fallback: %s" % (self.video))
            return

        if "type" in self.video:
            logger.debug("Watching: %s" % self.video["type"])
            if not xbmc.Player().isPlayingVideo():
                logger.debug("Suddenly stopped watching item")
                return
            try:
                self.pvr = xbmc.getCondVisibility("Pvr.IsPlayingTv")
                self.watched_time = xbmc.Player().getTime()
                self.duration = 0
                if self.pvr:
                    self.program = _program_labels()
                    self.watched_time = to_seconds(
                        xbmc.getInfoLabel("PVR.EpgEventElapsedTime(hh:mm:ss)")
                    )
                    self.duration = int(
                        to_seconds(
                            xbmc.getInfoLabel("PVR.EpgEventDuration(hh:mm:ss)")
                        )
                    )
                else:
                    self.duration = xbmc.Player().getTotalTime()
            except Exception as e:
                logger.debug("Suddenly stopped watching item: %s" % str(e))
                self.video = None
                return

            if self.duration == 0:
                if utilities.is_movie(self.video["type"]):
                    self.duration = 90 * 60
                elif utilities.is_episode(self.video["type"]):
                    self.duration = 30 * 60
                else:
                    self.duration = 1

            self.playlist_index = xbmc.PlayList(xbmc.PLAYLIST_VIDEO).getposition()

            self.multi_part = False
            if utilities.is_movie(self.video["type"]):
                if "id" in self.video:
                    movie_details = get_movie_details(
                        self.video["id"],
                        [
                            "uniqueid",
                            "title",
                            "year",
                            "file",
                            "lastplayed",
                            "playcount",
                        ],
                    )
                    if movie_details:
                        playcount = movie_details.get("playcount")
                        self.video_info = to_trakt_item(
                            "movie", movie_details
                        )
                    else:
                        self.video_info = None
                elif "video_ids" in self.video:
                    self.video_info = {"ids": self.video["video_ids"]}
                elif "title" in self.video and "year" in self.video:
                    self.video_info = {
                        "title": self.video["title"],
                        "year": self.video["year"],
                    }
                else:
                    logger.debug("Couldn't set video_info for movie type")
                logger.debug("Movie type, video_info: %s" % self.video_info)

            elif utilities.is_episode(self.video["type"]):
                if "id" in self.video:
                    episode_details = get_episode_details(
                        self.video["id"],
                        [
                            "showtitle",
                            "season",
                            "episode",
                            "tvshowid",
                            "uniqueid",
                            "file",
                            "playcount",
                        ],
                    )
                    if episode_details:
                        playcount = episode_details.get("playcount")
                        title, year = split_title_year(episode_details["showtitle"])
                        if not year:
                            self.show_info = {
                                "title": episode_details["showtitle"],
                                "year": episode_details.get("year"),
                            }
                        else:
                            self.show_info = {"title": title, "year": year}
                        if "show_ids" in episode_details:
                            self.show_info["ids"] = episode_details["show_ids"]
                        self.video_info = to_trakt_item(
                            "episode", episode_details
                        )
                    else:
                        self.video_info = None
                    if not self.video_info:
                        logger.debug(
                            "Episode details from Kodi was empty, ID (%d) seems invalid,"
                            " aborting further scrobbling of this episode."
                            % self.video["id"]
                        )
                        self.video = None
                        self.playing = False
                        self.watched_time = 0
                        return
                elif (
                    "video_ids" in self.video
                    and "season" in self.video
                    and "episode" in self.video
                ):
                    self.video_info = {
                        "season": self.video["season"],
                        "number": self.video["episode"],
                    }
                    self.show_info = {"ids": self.video["video_ids"]}
                elif (
                    "title" in self.video
                    and "season" in self.video
                    and "episode" in self.video
                ):
                    self.video_info = {
                        "title": self.video["title"],
                        "season": self.video["season"],
                        "number": self.video["episode"],
                    }

                    title, year = split_title_year(self.video["showtitle"])
                    if not year:
                        self.show_info = {"title": self.video["showtitle"]}
                    else:
                        self.show_info = {"title": title, "year": year}

                    if "year" in self.video:
                        self.show_info["year"] = self.video["year"]
                else:
                    logger.debug(
                        "Couldn't set video_info/show_info for episode type"
                    )

                if (
                    "multi_episode_count" in self.video
                    and self.video["multi_episode_count"] > 1
                ):
                    self.multi_part = True

                logger.debug("Episode type, video_info: %s" % self.video_info)
                logger.debug(
                    "Episode type, show_info: %s" % self.show_info
                )

            self.playing = True
            self.paused = False
            self._record_active(playcount)

            self._prefetch_rating(self._scrobble_start(), self.video.get("id"))

    def _scrobble_start(self) -> Optional[Dict]:
        """Scrobble the start, or just fetch Trakt's item to rate when its type isn't scrobbled."""
        if not self.video or not self.video_info:
            return None
        if utilities.is_movie(self.video["type"]):
            if get_setting_bool("scrobble_movie"):
                return self._scrobble("start")
            if not get_setting_bool("rate_movie") or "ids" not in self.video_info:
                return None
            movie_id = find_trakt_id(*pick_best_id(self.video_info["ids"], "movie"), "movie")
            movie = self.api.get_movie_summary(movie_id).to_dict() if movie_id else {}
            return {"movie": movie} if movie else None

        if get_setting_bool("scrobble_episode"):
            return self._scrobble("start")
        show_ids = (self.show_info or {}).get("ids")
        if (
            not utilities.is_episode(self.video["type"])
            or not get_setting_bool("rate_episode")
            or not show_ids
        ):
            return None
        show_id = find_trakt_id(*pick_best_id(show_ids, "show"), "show")
        if not show_id:
            return None
        show = self.api.get_show_summary(show_id).to_dict()
        season = self.video_info["season"]
        episode = self.api.get_episode_summary(
            show_id, season, self.video_info["number"]
        ).to_dict()
        if not show or not episode:
            return None
        episode["season"] = season
        return {"show": show, "episode": episode}

    def _prefetch_rating(self, result: Optional[Dict], library_id: Optional[int] = None) -> None:
        """Pre-fetch user's rating, swapping in Trakt's item with any Kodi id, when rating is on."""
        if result and self.video:
            if (
                utilities.is_movie(self.video["type"])
                and get_setting_bool("rate_movie")
                and "movie" in result
            ):
                logger.debug(
                    "Movie rating is enabled, pre-fetching summary information."
                )
                self.video_info = info = result["movie"]
                if library_id is not None:
                    info["movieid"] = library_id
                info["user"] = {
                    "ratings": self.api.get_user_rating(
                        "movie", result["movie"]["ids"]["trakt"]
                    )
                }
            elif (
                utilities.is_episode(self.video["type"])
                and get_setting_bool("rate_episode")
                and "episode" in result
                and "show" in result
            ):
                logger.debug(
                    "Episode rating is enabled, pre-fetching summary information."
                )
                self.video_info = info = result["episode"]
                if library_id is not None:
                    info["episodeid"] = library_id
                info["user"] = {
                    "ratings": self.api.get_user_rating(
                        "episode", (info.get("ids") or {}).get("trakt")
                    )
                }
            logger.debug("Pre-Fetch result: %s; Info: %s" % (result, self.video_info))

    def _scrobble_restart(self) -> None:
        """Scrobble a start after a pause or seek, pre-fetching the rating a failed start missed."""
        reply = self._scrobble("start")
        if self.video and not self.multi_part and "user" not in (self.video_info or {}):
            self._prefetch_rating(reply, self.video.get("id"))

    def playback_resumed(self) -> None:
        """Scrobble a start on resume after a pause, except on PVR."""
        if not self.playing or self.pvr:
            return

        logger.debug("Playback resumed")
        if self.paused:
            paused_for = time.time() - self.paused_at
            logger.debug("Resumed after: %s" % str(paused_for))
            self.paused_at = 0
            self.paused = False
            self._scrobble_restart()

    def playback_paused(self) -> None:
        """Scrobble a pause, except on PVR."""
        if not self.playing or self.pvr:
            return

        logger.debug("Playback paused")
        logger.debug("Paused after: %s" % str(self.watched_time))
        self.paused = True
        self.paused_at = time.time()
        self._scrobble("pause")

    def playback_seek(self) -> None:
        """Update the watched time, holding the seek's scrobble until seeking settles for 2s."""
        if not self.playing:
            return

        logger.debug("Seek")
        self.seek_due = time.time() + SEEK_SETTLE
        self.check_transition()

    def playback_ended(self, natural: bool = False) -> None:
        """Scrobble the stop, offering a rating if any was watched; a natural end counts in full."""
        if not self.pvr and self.video_info is not None:
            self.to_rate.append(self.video_info)
        if not self.playing:
            return

        logger.debug("Playback ended, natural end: %s" % natural)
        if not self.to_rate and not self.pvr:
            logger.debug("Warning: Playback ended but video forgotten.")
            return
        self.playing = False
        self.stop_pending = False
        # seek-past-end races the seek dispatch, leaving watched_time stale
        if natural and not self.pvr and self.duration > 0:
            self.watched_time = self.duration
        if self.video and "type" in self.video:
            # Trakt refuses a stop under 1% but still clears the watching state
            self._scrobble("stop")
            if self.watched_time != 0:
                check_rating(self.video["type"], self.to_rate, self.watched_time, self.duration)
        self.watched_time = 0
        self.pvr = False
        self.multi_part = False
        self.to_rate = []
        self.video_info = None
        self.video = None
        self.playlist_index = 0
        self.seek_due = None
        self.scrobble_queue.clear_active()

    def _record_active(self, playcount: Optional[int]) -> None:
        """Record the library item so a crash before its stop can be recovered at startup."""
        if (
            playcount is None
            or self.multi_part
            or not self.video
            or not self.video_info
        ):
            self.scrobble_queue.clear_active()
            return
        media_type = self.video["type"]
        setting = "scrobble_movie" if utilities.is_movie(media_type) else "scrobble_episode"
        if not get_setting_bool(setting):
            self.scrobble_queue.clear_active()
            return
        show_info = self.show_info if utilities.is_episode(media_type) else None
        self.scrobble_queue.set_active(
            media_type, self.video["id"], playcount, self.video_info, show_info
        )

    def recover_crashed_playback(self) -> None:
        """Recover the item a crashed session left playing: queue it if Kodi marked it watched."""
        active = self.scrobble_queue.pop_active()
        if not active:
            return
        fields = ["playcount", "lastplayed"]
        if active["media_type"] == "movie":
            details = get_movie_details(active["dbid"], fields)
        else:
            details = get_episode_details(active["dbid"], fields + ["tvshowid"])
        if not details or details.get("playcount", 0) <= active["playcount"]:
            logger.debug("Previous session ended mid-playback, item not watched: %s" % active)
            return
        logger.info("Previous session ended after %s was watched, queuing" % active["media_type"])
        watched_at = local_to_utc(details.get("lastplayed")) or datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.scrobble_queue.add(
            active["media_type"], active["media_info"], active["show_info"], 100.0, watched_at
        )

    def _watched_percent(self) -> float:
        """Percent of the video watched, capped at 100."""
        floored = math.floor(self.duration)
        if floored != 0:
            return min((self.watched_time / floored) * 100, 100.0)
        else:
            return 0

    def _scrobble(self, status: str) -> Optional[Dict]:
        """Scrobble at the current progress, queueing a failed stop at 80% or more for history."""
        if not self.video_info or not self.video:
            return

        logger.info("Scrobble '%s'" % status)
        scrobble_movies = get_setting_bool("scrobble_movie")
        scrobble_episodes = get_setting_bool("scrobble_episode")

        percent = self._watched_percent()

        if utilities.is_movie(self.video["type"]) and scrobble_movies:
            response = self.api.scrobble_movie(
                self.video_info, percent, status
            )
            if response is not None:
                if status == "stop":
                    self._clear_playback_progress(response)
                if response.get("duplicate"):
                    logger.debug("Movie already scrobbled recently, skipping notification")
                else:
                    self._notify_scrobble(response)
                logger.debug("Scrobble response: %s" % str(response))
                return response
            else:
                logger.debug(
                    "Failed to scrobble movie: %s | %s | %s"
                    % (self.video_info, percent, status)
                )
                # Trakt's watched threshold is 80%
                if status == "stop" and percent >= 80:
                    self.scrobble_queue.add(
                        "movie", self.video_info, None,
                        percent,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    )

        elif utilities.is_episode(self.video["type"]) and scrobble_episodes:
            if self.multi_part:
                logger.debug(
                    "Multi-part episode, scrobbling part %d of %d."
                    % (self.part + 1, self.video["multi_episode_count"])
                )
                part_duration = int(
                    self.duration / self.video["multi_episode_count"]
                )
                percent = (
                    (self.watched_time - (part_duration * self.part))
                    / part_duration
                ) * 100

            logger.debug(
                "scrobble sending show object: %s" % str(self.show_info)
            )
            logger.debug("scrobble sending episode object: %s" % str(self.video_info))
            response = self.api.scrobble_episode(
                self.show_info, self.video_info, percent, status
            )

            if get_setting_bool("scrobble_secondary_title"):
                logger.debug(
                    "Setting is enabled to try secondary show title, if necessary."
                )
                show_title = (self.show_info or {}).get("title")
                # title may be an alternative one; text search matches those, scrobble doesn't
                if response is None and show_title:
                    logger.debug("Searching for show title: %s" % show_title)
                    hits = self.api.search_by_text(show_title, "show", None)
                    if not hits:
                        logger.debug("Empty Response from search_by_text, giving up")
                    else:
                        logger.debug(
                            "Got Response from search_by_text: %s" % str(hits)
                        )
                        # no way to choose between several matches
                        show = {"title": hits[0].title, "year": hits[0].year}
                        logger.debug(
                            "scrobble sending search_by_text first show object: %s"
                            % str(show)
                        )
                        response = self.api.scrobble_episode(
                            show, self.video_info, percent, status
                        )

            if response is not None:
                if status == "stop":
                    self._clear_playback_progress(response)
                if response.get("duplicate"):
                    logger.debug("Episode already scrobbled recently, skipping notification")
                    return response

                # PVR numbering can be aired or DVD order
                titled = self.pvr and "episode" in response and "title" in self.video_info
                if titled and not fuzzy_match(
                    self.video_info["title"], response["episode"]["title"], 50.0
                ):
                    logger.debug(
                        "scrobble sending incorrect scrobble_episode stopping: %sx%s - %s != %s"
                        % (
                            self.video_info["season"],
                            self.video_info["number"],
                            self.video_info["title"],
                            response["episode"]["title"],
                        )
                    )
                    self.stop_pending = True

                self._notify_scrobble(response)
                logger.debug("Scrobble response: %s" % str(response))
                return response
            else:
                logger.debug(
                    "Failed to scrobble episode: %s | %s | %s | %s"
                    % (self.show_info, self.video_info, percent, status)
                )
                if status == "stop" and percent >= 80:
                    self.scrobble_queue.add(
                        "episode", self.video_info, self.show_info,
                        percent,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    )

    def _clear_playback_progress(self, response: Dict) -> None:
        """Clear Trakt's playback progress when the setting is on and the stop only paused."""
        if not get_setting_bool("clear_playback_progress"):
            return
        if not response or response.get("action") != "pause" or not self.video:
            return

        media_type = self.video.get("type")
        trakt_id = None
        if media_type == "movie" and "movie" in response:
            trakt_id = response["movie"].get("ids", {}).get("trakt")
        elif media_type == "episode" and "episode" in response:
            trakt_id = response["episode"].get("ids", {}).get("trakt")

        if trakt_id:
            logger.debug("Clearing playback progress for %s trakt:%s" % (media_type, trakt_id))
            self.api.delete_item_playback_progress(media_type, trakt_id)

    def _notify_scrobble(self, info: Dict) -> None:
        """Notify the item as watching, paused or watched per Trakt's reply, if notices are on."""
        if not self.video_info or not self.video:
            return

        if get_setting_bool("scrobble_notification"):
            name = utilities.format_item_name(
                self.video["type"], info[self.video["type"]]
            )
            action = info.get("action")
            if action == "scrobble":
                header = get_string(32015)
            elif action == "pause":
                header = get_string(32215) % ("%d%%" % info.get("progress", 0))
            else:
                header = get_string(32217)
            notify(header, name)


def _program_labels() -> Tuple[str, ...]:
    """Labels naming the live TV program on now, which change when the next one starts."""
    return tuple(xbmc.getInfoLabel("VideoPlayer.%s" % name)
                 for name in ("Title", "EpisodeName", "Season", "Episode"))
