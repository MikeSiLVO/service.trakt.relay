"""The service's event loop, dispatching player, monitor and script events."""
import logging
import time
from collections import deque
from datetime import timedelta, timezone
from typing import Deque, Dict, Optional
import dateutil.parser
import xbmc

from lib.dialogs import auth
from lib.dialogs.conflict import ConflictWarning
from lib.media.match import build_media_index, find_in_index
from lib.trakt import shared
from lib.service import actions
from lib.service.monitor import Monitor
from lib.service.player import Player
from lib.service.scrobbler import Scrobbler
from lib.sync.sync import SyncThread
from lib.trakt.api import TraktAPI
from lib.kodi.client import ADDON
from lib.kodi.log import ADDON_ID, configure_logging
from lib.kodi.settings import get_setting_bool, get_setting_int, set_setting
from lib.kodi.utilities import remove_empty_settings

logger = logging.getLogger(__name__)

SCRIPT_ACTIONS = ("sync", "rate", "unrate", "startrewatch", "addtowatchlist")
HISTORY_WINDOW = timedelta(minutes=5)


class Service:
    """Background service holding the scrobbler, the event queue and any running sync."""
    def __init__(self) -> None:
        self.scrobbler: Optional[Scrobbler] = None
        self.sync_thread: Optional[SyncThread] = None
        self.events: Deque[Dict] = deque()
        self._dispatching = False

    def _enqueue(self, data: Dict) -> None:
        """Queue an event and dispatch it at once, unless a dispatch is already running."""
        logger.debug("Queuing for dispatch: %s" % data)
        self.events.append(data)
        if not self._dispatching:
            self._dispatch_queued()

    def _dispatch_queued(self) -> None:
        """Dispatch queued events in order until none are left or Kodi is exiting."""
        self._dispatching = True
        try:
            while self.events and not self.monitor.abortRequested():
                self._dispatch(self.events.popleft())
        finally:
            self._dispatching = False

    def _dispatch(self, data: Dict) -> None:
        """Dispatch a queued event; if not connected, starts only notify, scripts connect first."""
        if self.scrobbler is None:
            return
        try:
            logger.debug("Dispatch: %s" % data)
            action = data["action"]
            if not shared.api.connected:
                if action == "started":
                    auth.notify_not_connected()
                    return
                if action in SCRIPT_ACTIONS and not auth.connect():
                    return
            if action == "started":
                del data["action"]
                self.scrobbler.playback_started(data)
            elif action == "ended" or action == "stopped":
                self.scrobbler.playback_ended(natural=(action == "ended"))
            elif action == "paused":
                self.scrobbler.playback_paused()
            elif action == "resumed":
                self.scrobbler.playback_resumed()
            elif action == "seek" or action == "seekchapter":
                self.scrobbler.playback_seek()
            elif action == "scanFinished":
                set_setting("kodi_library_dirty", "true")
                if get_setting_bool("sync_on_update"):
                    logger.debug("Performing sync after library update.")
                    self.start_sync()
            elif action == "databaseCleaned":
                set_setting("kodi_library_dirty", "true")
                if get_setting_bool("sync_on_update") and (
                    get_setting_bool("sync_clean_collection_movies_to_trakt")
                    or get_setting_bool("sync_clean_collection_episodes_to_trakt")
                ):
                    logger.debug("Performing sync after library clean.")
                    self.start_sync()
            elif action == "sync":
                logger.debug("Performing a manual sync.")
                library = data.get("library")
                self.start_sync(
                    manual=True,
                    silent=str(data.get("silent")).lower() == "true",
                    library=library if library in ("episodes", "movies") else "all",
                )
            elif action in ("rate", "unrate"):
                actions.rate_manually(data)
            elif action == "startrewatch":
                actions.start_rewatch(data)
            elif action == "addtowatchlist":
                actions.add_to_watchlist(data)
            elif action == "auth_info":
                xbmc.executebuiltin("Dialog.Close(all, true)")
                auth.connect()
            else:
                logger.debug("Unknown dispatch action, '%s'." % action)
        except Exception:
            logger.exception("Dispatch failed: %s" % data)

    def _retry_failed_scrobbles(self) -> None:
        """Retry queued stops as history adds; drop any Trakt has or can't match; stop on error."""
        if self.scrobbler is None or not shared.api.connected:
            return
        try:
            queue = self.scrobbler.scrobble_queue
            pending = queue.get_pending()
            if not pending:
                return

            logger.info("%d pending scrobble(s) to retry" % len(pending))
            for item in pending:
                played = _in_history(item)
                if played is None:
                    queue.increment_retry(item["id"])
                    logger.debug("Trakt history unreadable, will try again later")
                    break
                if played:
                    logger.info("Trakt already has the %s play, dropping it" % item["media_type"])
                    queue.remove(item["id"])
                    continue

                result = shared.api.add_to_history(_history_payload(item))
                if not isinstance(result, dict):
                    queue.increment_retry(item["id"])
                    logger.debug("Retry failed, will try again later")
                    break

                media_type = item["media_type"]
                added = result.get("added") or {}
                added_count = added.get("movies", 0) + added.get("episodes", 0)
                not_found = result.get("not_found") or {}
                nf_count = (
                    len(not_found.get("movies") or [])
                    + len(not_found.get("episodes") or [])
                    + len(not_found.get("shows") or [])
                )

                if added_count > 0:
                    logger.info("Successfully retried %s scrobble" % media_type)
                    queue.remove(item["id"])
                elif nf_count > 0:
                    logger.warning(
                        "Trakt could not match %s scrobble, dropping: %s"
                        % (media_type, not_found)
                    )
                    queue.remove(item["id"])
                else:
                    logger.warning(
                        "Unexpected response (no added, no not_found): %s" % result
                    )
                    queue.increment_retry(item["id"])
                    break
        except Exception as ex:
            logger.error("Unhandled exception: %s" % str(ex))

    def run(self) -> None:
        """Run the service loop until Kodi exits, retrying failed scrobbles every 5 minutes."""
        startup_delay = get_setting_int("startup_delay")
        if startup_delay:
            logger.debug("Delaying startup by %d seconds." % startup_delay)
            xbmc.sleep(startup_delay * 1000)

        logger.debug("Service thread starting.")

        shared.api = TraktAPI()

        self.scrobbler = Scrobbler()
        self.scrobbler.recover_crashed_playback()
        self._last_retry_check = 0
        connect_prompt = auth.ConnectPrompt()
        conflict_warning = ConflictWarning()

        self._start_hooks()

        while not self.monitor.abortRequested():
            self.player.queue_due_start()
            if self.player.isPlayingVideo():
                self.scrobbler.check_transition()

            connect_prompt.tick()
            conflict_warning.tick()

            if time.time() - self._last_retry_check > 300:
                self._last_retry_check = time.time()
                self._retry_failed_scrobbles()

            if self.monitor.waitForAbort(1):
                break

        logger.debug("Beginning shut down.")

        del self.player
        del self.monitor

        if self.sync_thread is not None and self.sync_thread.is_alive():
            self.sync_thread.join()

    def _start_hooks(self) -> None:
        """Start the monitor and player hooks, holding a start for a video already playing."""
        self.monitor = Monitor(action=self._enqueue)
        self.player = Player(action=self._enqueue)
        if self.player.isPlayingVideo():
            logger.info("Picking up the video already playing")
            self.player.onAVStarted()

    def start_sync(self, manual: bool = False, silent: bool = False, library: str = "all") -> None:
        """Start a sync thread unless one runs, or notify once when Relay is not connected."""
        if not shared.api.connected:
            auth.notify_not_connected()
            return
        if self.sync_thread is not None and self.sync_thread.is_alive():
            logger.debug("There already is a sync in progress.")
            return
        self.sync_thread = SyncThread(manual, silent, library, self.monitor)
        self.sync_thread.start()


def _history_payload(item: Dict) -> Dict:
    """Build the history add for a queued movie or episode stop."""
    media_info, show_info = item["media_info"], item["show_info"] or {}
    if item["media_type"] == "movie":
        return {"movies": [{
            "ids": media_info.get("ids", {}),
            "title": media_info.get("title"),
            "year": media_info.get("year"),
            "watched_at": item["watched_at"],
        }]}
    return {"shows": [{
        "ids": show_info.get("ids", {}),
        "title": show_info.get("title"),
        "year": show_info.get("year"),
        "seasons": [{
            "number": media_info.get("season"),
            "episodes": [
                {"number": media_info.get("number"), "watched_at": item["watched_at"]}
            ],
        }],
    }]}


def _in_history(item: Dict) -> Optional[bool]:
    """Whether Trakt's history has the queued stop within 5 minutes of it; None if unreadable."""
    at = dateutil.parser.parse(item["watched_at"])
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    stamp = "%Y-%m-%dT%H:%M:%S.000Z"
    plays = shared.api.get_history(
        item["media_type"],
        (at - HISTORY_WINDOW).astimezone(timezone.utc).strftime(stamp),
        (at + HISTORY_WINDOW).astimezone(timezone.utc).strftime(stamp),
    )
    if plays is None:
        return None
    media_info = item["media_info"]
    if item["media_type"] == "movie":
        candidates, wanted = [play.get("movie") or {} for play in plays], media_info
    else:
        number = (str(media_info.get("season")), str(media_info.get("number")))
        candidates = [play.get("show") or {} for play in plays
                      if (str(play["episode"].get("season")), str(play["episode"].get("number")))
                      == number]
        wanted = item["show_info"] or {}
    return find_in_index(wanted, build_media_index(candidates), True) is not None


def main() -> None:
    """Remove empty settings, configure logging and run the service, logging any unhandled error."""
    # Kodi reads the user settings once, at the first setting asked for
    removed = remove_empty_settings()
    configure_logging()
    if removed:
        logger.warning("Removed an empty settings.xml that Kodi couldn't load; settings reset")
    logger.debug("Loading '%s' version '%s'" % (ADDON_ID, ADDON.getAddonInfo("version")))
    try:
        Service().run()
    except Exception:
        logger.exception("Service stopped on an unhandled error")
    logger.debug("'%s' shutting down." % ADDON_ID)
