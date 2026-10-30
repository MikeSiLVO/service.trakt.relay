"""Kodi monitor hooks for library scans and cleans, Up Next, and events sent by scripts."""
import json
import logging
from typing import Any, Dict, Callable
import xbmc

from lib.kodi.log import ADDON_ID
from lib.kodi.settings import reload_settings
from lib.script.script import EVENT

logger = logging.getLogger(__name__)


class Monitor(xbmc.Monitor):
    """Kodi monitor queueing library scan and clean events, Up Next's signal and script events."""
    action: Callable[[Dict], None]
    scanning_video: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.action = kwargs["action"]
        # Library.IsScanningVideo is false while a clean runs during an update
        self.scanning_video = False
        logger.debug("Monitor hooks ready.")

    def onNotification(self, sender: str, method: str, data: str) -> None:
        """Queue a script's event, or a natural end when Up Next signals the episode watched."""
        if sender == ADDON_ID and method == "Other." + EVENT:
            self.action(json.loads(data))
            return
        # method looks like Other.NEXTUPWATCHEDSIGNAL
        if "." not in method or method.split(".")[1].upper() != "NEXTUPWATCHEDSIGNAL":
            return

        logger.debug("Callback received - Upnext skipped to the next episode")
        self.action({"action": "ended"})

    def onSettingsChanged(self) -> None:
        """Reload the settings once any Addon of Relay's saves them."""
        reload_settings()

    def onScanFinished(self, database: str) -> None:
        """Queue a library update once a video scan finishes."""
        if database == "video":
            self.scanning_video = False
            logger.debug("Library scan finished: %s" % database)
            data = {"action": "scanFinished"}
            self.action(data)

    def onScanStarted(self, database: str) -> None:
        """Flag a running video scan; the clean it triggers is not queued."""
        if database == "video":
            self.scanning_video = True
            logger.debug(
                "Library scan started: %s" % database
            )

    def onCleanFinished(self, database: str) -> None:
        """Queue a library clean event, unless the clean was part of a scan."""
        if database == "video" and not self.scanning_video:
            data = {"action": "databaseCleaned"}
            self.action(data)
