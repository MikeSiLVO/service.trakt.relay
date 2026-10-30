"""Warning that the Trakt add-on is enabled beside Relay, with an offer to disable it."""

import time

import xbmc
import xbmcgui

from lib.kodi.client import ADDON, request
from lib.kodi.settings import get_string
from lib.kodi.utilities import count_idle
from lib.trakt import shared

TRAKT_ADDON = "script.trakt"
CHECK_SECONDS = 60


class ConflictWarning:
    """Warning that the Trakt add-on is enabled too, given once a session and on each re-enable."""

    def __init__(self) -> None:
        self.enabled = False
        self.pending = False
        self.held = 0
        self.next_check = 0.0

    def tick(self) -> None:
        """Check Trakt add-on each minute; offer to disable it after 3 idle ticks if connected."""
        if time.time() >= self.next_check:
            self.next_check = time.time() + CHECK_SECONDS
            enabled = xbmc.getCondVisibility("System.AddonIsEnabled(%s)" % TRAKT_ADDON)
            self.pending = enabled and (self.pending or not self.enabled)
            self.enabled = enabled
        if not self.pending or not shared.api.connected:
            return
        self.held = count_idle(self.held)
        if self.held < 3:
            return
        self.held = 0
        self.pending = False
        if xbmcgui.Dialog().yesno(ADDON.getAddonInfo("name"), get_string(32218),
                                  nolabel=get_string(32219),
                                  yeslabel=xbmc.getLocalizedString(24021)):
            request("Addons.SetAddonEnabled", {"addonid": TRAKT_ADDON, "enabled": False})
            self.enabled = False
