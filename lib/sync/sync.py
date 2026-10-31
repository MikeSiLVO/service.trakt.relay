"""One Kodi and Trakt sync run, and the thread that runs it."""

import threading
import logging
from typing import Any, Callable, Dict, List, Optional

import xbmc
import xbmcgui
from lib.media.utilities import chunks
from lib.sync import episodes
from lib.sync import movies
from lib.sync.compare import count_episodes

from lib.trakt import shared
from lib.kodi.client import ADDON
from lib.kodi.settings import (get_setting, get_setting_bool, get_setting_int, get_string,
                               set_setting)
from lib.kodi.utilities import notify

logger = logging.getLogger(__name__)

REMOVAL_LIMIT = 20


class Sync:
    """Kodi and Trakt sync of movies then episodes, skipped when not manual and nothing changed."""

    def __init__(self, manual: bool = False, silent: bool = False, library: str = "all",
                 api: Any = None, monitor: Any = None) -> None:
        self.api = api
        self.monitor = monitor
        self.progress = xbmcgui.DialogProgress()
        self.manual = manual
        self.show_progress = manual and not silent
        self.library = library
        if manual and silent:
            logger.debug("Sync is being run silently.")
        self.sync_on_update = get_setting_bool('sync_on_update')
        self.notify = get_setting_bool('show_sync_notifications')
        self.notify_during_playback = not get_setting_bool("hide_notifications_playback")
        self.failed = 0

    def _enabled(self, media_type: str) -> bool:
        """True when any sync of the media type is on."""
        return (
            self._collection_enabled(media_type)
            or self._watched_enabled(media_type)
            or self._playback_enabled(media_type)
            or self._ratings_enabled()
        )

    def _playback_enabled(self, media_type: str) -> bool:
        """True when Trakt's paused progress is copied to Kodi for the media type."""
        if media_type == 'movies':
            return get_setting_bool('trakt_movie_playback')
        else:
            return get_setting_bool('trakt_episode_playback')

    def _collection_enabled(self, media_type: str) -> bool:
        """True when the media type's Trakt collection is added to or cleaned."""
        return get_setting_int('sync_collection_%s' % media_type) > 0

    def _ratings_enabled(self) -> bool:
        """True when ratings sync is on in any direction."""
        return get_setting_int('sync_ratings') > 0

    def _watched_enabled(self, media_type: str) -> bool:
        """True when watched state syncs in any direction for the media type."""
        return get_setting_int('sync_watched_%s' % media_type) > 0

    @property
    def should_notify(self) -> bool:
        """True when a background sync notifies its start and finish."""
        return (
            not self.manual
            and self.sync_on_update
            and self.notify
            and (self.notify_during_playback or not xbmc.Player().isPlayingVideo())
        )

    def run(self) -> None:
        """Run movie then episode sync; skip unchanged background runs; cache only complete runs."""
        logger.info("Starting synchronization with Trakt.tv")

        if not self.manual and self._can_skip():
            logger.info("[Sync] No changes on Trakt or Kodi since last sync, skipping.")
            return

        complete = self.library == "all"
        if self._enabled('movies'):
            if self.library in ["all", "movies"]:
                complete = self._run_half(movies.MovieSync(self), 32213) and complete
            else:
                logger.debug(
                    "Movie sync is being skipped for this manual sync.")
        else:
            logger.debug("Movie sync is disabled, skipping.")

        if self._enabled('episodes'):
            if self.library in ["all", "episodes"]:
                if not (self._enabled('movies') and self.is_canceled()):
                    complete = self._run_half(episodes.EpisodeSync(self), 32214) and complete
                else:
                    logger.debug(
                        "Episode sync is being skipped because movie sync was canceled.")
            else:
                logger.debug(
                    "Episode sync is being skipped for this manual sync.")
        else:
            logger.debug("Episode sync is disabled, skipping.")

        if complete and not self.is_canceled():
            self._save_last_activities()
        else:
            # what this run left undone must not look unchanged to the next background run
            set_setting("kodi_library_dirty", "true")
        logger.info("[Sync] Finished synchronization with Trakt.tv")

    def _run_half(self, half: Any, heading_id: int) -> bool:
        """Run a half in a progress dialog, notifying start, end or failures; True if finished."""
        heading = get_string(heading_id)
        if self.should_notify:
            notify(heading, get_string(32061))
        if self.show_progress:
            self.progress.create(heading, "")
        failed_before = self.failed
        finished = half.run()
        failed = self.failed - failed_before
        if finished:
            self.update_progress(100, line1=get_string(32062))
        if self.show_progress:
            self.progress.close()
        silent = self.manual and not self.show_progress
        hidden = not self.notify_during_playback and xbmc.Player().isPlayingVideo()
        if failed and not silent and not hidden:
            notify(heading, get_string(32226) % failed)
        elif finished and self.should_notify:
            notify(heading, get_string(32062))
        return finished

    def _can_skip(self) -> bool:
        """True when the Kodi library is clean and Trakt's last activity matches the cached one."""
        if get_setting_bool("kodi_library_dirty"):
            logger.debug("[Sync] Kodi library is dirty, cannot skip sync.")
            return False

        try:
            activities = self.api.get_last_activities()
        except Exception as ex:
            logger.debug("[Sync] Failed to fetch last_activities: %s" % ex)
            return False

        if not activities or "all" not in activities:
            logger.debug("[Sync] Invalid last_activities response, cannot skip sync.")
            return False

        cached = get_setting("last_activities_all")
        current = activities["all"]

        if not cached:
            logger.debug("[Sync] No cached last_activities, running full sync.")
            return False

        if current == cached:
            logger.debug("[Sync] last_activities unchanged (%s), skipping sync." % current)
            return True

        logger.debug("[Sync] last_activities changed (cached=%s, current=%s)." % (cached, current))
        return False

    def _save_last_activities(self) -> None:
        """Save Trakt's last activity if read, and clear the dirty flag unless the call raises."""
        try:
            activities = self.api.get_last_activities()
        except Exception as ex:
            logger.debug("[Sync] Failed to fetch last_activities for caching: %s" % ex)
            return

        if activities and "all" in activities:
            set_setting("last_activities_all", activities["all"])
            logger.debug("[Sync] Cached last_activities: %s" % activities["all"])

        set_setting("kodi_library_dirty", "false")

    def is_canceled(self) -> bool:
        """True when the user canceled the progress dialog or Kodi is exiting."""
        if self.monitor is not None and self.monitor.abortRequested():
            logger.debug("Sync stopped for Kodi exiting.")
            return True
        if self.show_progress:
            try:
                if self.progress.iscanceled():
                    logger.debug("Sync was canceled by user.")
                    return True
            except RuntimeError:
                pass
        return False

    def update_progress(
        self, percent: int, line1: str = "", line2: str = "", line3: str = ""
    ) -> None:
        """Update the progress dialog's percent and lines, when one is showing."""
        if self.show_progress:
            self.progress.update(percent, "%s\n%s\n%s" % (line1, line2, line3))

    def update_count(self, done: int, total: int, from_percent: int, to_percent: int,
                     step_id: int) -> None:
        """Update the progress dialog with the step's name, its count so far and a matching bar."""
        percent = from_percent + (to_percent - from_percent) * done // total
        self.update_progress(
            percent, line1=get_string(step_id), line2=get_string(32089) % (done, total))

    def confirm_removals(self, removals: List[Dict], message_id: int) -> List[Dict]:
        """Confirm Kodi unwatches or rating clears: up to 20 pass, more if a user agrees."""
        if len(removals) <= REMOVAL_LIMIT or self.show_progress and xbmcgui.Dialog().yesno(
                ADDON.getAddonInfo("name"), get_string(message_id) % len(removals)):
            return removals
        logger.info("[Sync] Left %i Kodi items as they are; a sync with progress asks before"
                    " more than %i" % (len(removals), REMOVAL_LIMIT))
        return []

    def send(self, write: Callable[[Dict], Optional[Dict]], items: List[Dict], kind: str,
             step_id: int, done_id: int, from_percent: int, to_percent: int) -> None:
        """Send items to Trakt in chunks, show what it took and failed, and tally the failures."""
        key = "movies" if kind == "movies" else "shows"
        size = 200 if key == "movies" else 50
        done = failed = 0
        for i, chunk in enumerate(chunks(items, size), 1):
            if self.is_canceled():
                return
            self.update_count(min(i * size, len(items)), len(items), from_percent, to_percent,
                              step_id)
            reply = write({key: chunk})
            if reply is None:
                failed += count_episodes(chunk) if kind == "episodes" else len(chunk)
                continue
            done += sum((reply.get(field) or {}).get(kind, 0)
                        for field in ("added", "updated", "deleted"))
            missing = {k: v for k, v in (reply.get("not_found") or {}).items() if v}
            if missing:
                logger.info("[Sync] Trakt found no match for: %s" % missing)
        if failed:
            logger.warning("[Sync] %i %s couldn't be synced" % (failed, kind))
        self.failed += failed
        self.update_progress(to_percent, line1=get_string(step_id),
                             line2=get_string(done_id) % done,
                             line3=get_string(32226) % failed if failed else "")


class SyncThread(threading.Thread):
    """Thread running one sync with the options it was made with."""

    _manual: bool = False
    _silent: bool = False
    _library: str = "all"

    def __init__(self, manual: bool = False, silent: bool = False, library: str = "all",
                 monitor: Any = None) -> None:
        threading.Thread.__init__(self)
        self.name = "trakt-sync"
        self._manual = manual
        self._silent = silent
        self._library = library
        self._monitor = monitor

    def run(self) -> None:
        """Run one sync with the options the thread was created with."""
        sync = Sync(
            manual=self._manual,
            silent=self._silent,
            library=self._library,
            api=shared.api,
            monitor=self._monitor,
        )
        sync.run()
