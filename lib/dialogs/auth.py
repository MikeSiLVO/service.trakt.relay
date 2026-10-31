"""Connecting Relay to a Trakt account by the device-code dialog, offered each session."""

import logging
import threading
from typing import cast

import xbmc
import xbmcgui

from lib.dialogs import setup
from lib.kodi.client import ADDON
from lib.kodi.settings import get_setting, get_setting_bool, get_string, show_settings
from lib.kodi.utilities import count_idle, notify, set_theme
from lib.trakt import shared

AUTH_XML = "script-trakt-relay-auth.xml"
INSTRUCTION_LABEL = 203
CODE_LABEL = 204
SETTINGS_BUTTON = 202

logger = logging.getLogger(__name__)

_warned = threading.Event()


class DeviceAuthDialog(xbmcgui.WindowXMLDialog):
    """Dialog showing the code to enter at Trakt's activate page, with a QR code to that page."""

    code: str
    url: str
    open_settings = False

    def onInit(self) -> None:
        """Fill in the instructions and the code once the window has loaded."""
        address = self.url.split("://", 1)[-1]
        address = "[COLOR %s]%s[/COLOR]" % (self.getProperty("highlight"), address)
        instruction = cast(xbmcgui.ControlLabel, self.getControl(INSTRUCTION_LABEL))
        instruction.setLabel(get_string(32200).format(address))
        cast(xbmcgui.ControlLabel, self.getControl(CODE_LABEL)).setLabel("[B]%s[/B]" % self.code)

    def onClick(self, control_id: int) -> None:
        """Close on Settings, flagging the settings to open once the dialog is gone."""
        if control_id == SETTINGS_BUTTON:
            self.open_settings = True
            self.close()


def connect() -> bool:
    """Connect until approved, refused, expired or closed; offer setup or open settings if asked."""
    api = shared.api
    code = api.request_device_code()
    if not code:
        notify(ADDON.getAddonInfo("name"), get_string(32023))
        return False
    logger.debug("Enter the code %s at %s" % (code.get("user_code"), code.get("verification_url")))

    dialog = DeviceAuthDialog(AUTH_XML, ADDON.getAddonInfo("path"), "Default", "1080i")
    set_theme(dialog)
    dialog.code = code.get("user_code", "")
    dialog.url = code.get("verification_url", "")
    stop = threading.Event()
    approved = threading.Event()

    def wait() -> None:
        if api.wait_for_token(code, stop):
            approved.set()
        if not stop.is_set():
            dialog.close()

    threading.Thread(target=wait, daemon=True).start()
    dialog.doModal()
    stop.set()
    if approved.is_set():
        notify(get_string(32152), get_setting("user"), 3000)
        setup.offer()
    elif dialog.open_settings:
        show_settings()
    return api.connected


def notify_not_connected() -> None:
    """Notify once per session that Relay is not connected to Trakt."""
    if not _warned.is_set():
        _warned.set()
        notify(ADDON.getAddonInfo("name"), get_string(32202))


class ConnectPrompt:
    """Connect dialog once a session, or sync setup when connected, after 3 idle ticks in a row."""

    def __init__(self) -> None:
        self.held = 0
        self.pending = True

    def tick(self) -> None:
        """Count idle ticks and on the third show the connect dialog, or the setup if connected."""
        if shared.api.connected and get_setting_bool("sync_setup_saved"):
            self.pending = False
        if not self.pending:
            return
        self.held = count_idle(self.held)
        if self.held < 3:
            return
        self.held = 0
        skin = xbmc.getSkinDir()
        if shared.api.connected:
            setup.offer()
        else:
            connect()
        # a skin switch closes the dialog the same way Back does
        if xbmc.getSkinDir() == skin:
            self.pending = False
