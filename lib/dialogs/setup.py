"""Sync setup dialog choosing what syncs between Kodi and Trakt, offered until it is saved."""

from typing import Dict, Optional, cast

import xbmcgui

from lib.kodi.client import ADDON
from lib.kodi.settings import get_setting_bool, get_setting_int, get_string, set_setting
from lib.kodi.utilities import set_theme
from lib.script.script import send_event

SETUP_XML = "script-trakt-relay-setup.xml"
ROWS = {1: "watched", 2: "ratings", 3: "resume", 4: "scan"}
SETTINGS = {
    "watched": ("sync_watched_movies", "sync_watched_episodes"),
    "ratings": ("sync_ratings",),
    "resume": ("trakt_movie_playback", "trakt_episode_playback"),
    "scan": ("sync_on_update",),
}
TOGGLES = ("resume", "scan")
DESCRIPTIONS = {
    100: 32251, 101: 32252, 102: 32253, 103: 32254,
    200: 32255, 201: 32256, 202: 32257, 203: 32258,
    300: 32259, 301: 32260,
    400: 32261, 401: 32262,
}
DESCRIPTION_LABEL = 600
SAVE_AND_SYNC = 501
SAVE = 502


class SyncSetupDialog(xbmcgui.WindowXMLDialog):
    """Dialog with a row of choices each for watched status, ratings, resume points and scans."""

    choices: Dict[str, Optional[int]]
    saved = False
    sync_now = False

    def onInit(self) -> None:
        """Mark each row's current choice and focus the watched status row's."""
        self.setProperty("selected", "80" + self.getProperty("accent")[2:])
        for row, choice in self.choices.items():
            self.setProperty(row, "" if choice is None else str(choice))
        self.setFocusId(100 + (self.choices["watched"] or 0))

    def onClick(self, control_id: int) -> None:
        """Take a row's clicked choice, or close on a save, flagging a sync for Save and sync."""
        if control_id in (SAVE_AND_SYNC, SAVE):
            self.saved = True
            self.sync_now = control_id == SAVE_AND_SYNC
            self.close()
        elif control_id in DESCRIPTIONS:
            row = ROWS[control_id // 100]
            self.choices[row] = control_id % 100
            self.setProperty(row, str(control_id % 100))

    def onFocus(self, control_id: int) -> None:
        """Describe the focused choice under the rows."""
        text = get_string(DESCRIPTIONS[control_id]) if control_id in DESCRIPTIONS else ""
        cast(xbmcgui.ControlLabel, self.getControl(DESCRIPTION_LABEL)).setLabel(text)


def _current_choices() -> Dict[str, Optional[int]]:
    """Read each row's current choice, or None where its movie and episode settings differ."""
    choices = {}
    for row, settings in SETTINGS.items():
        values = {int(get_setting_bool(s)) if row in TOGGLES else get_setting_int(s)
                  for s in settings}
        choices[row] = values.pop() if len(values) == 1 else None
    return choices


def _save(choices: Dict[str, Optional[int]]) -> None:
    """Save each row's choice to its settings, skipping rows with none, and mark the setup saved."""
    for row, choice in choices.items():
        if choice is None:
            continue
        value = ("true" if choice else "false") if row in TOGGLES else choice
        for setting in SETTINGS[row]:
            set_setting(setting, value)
    set_setting("sync_setup_saved", "true")


def run() -> None:
    """Show the sync setup, save its choices on a save, and ask the service for a sync if chosen."""
    dialog = SyncSetupDialog(SETUP_XML, ADDON.getAddonInfo("path"), "Default", "1080i")
    set_theme(dialog)
    dialog.choices = _current_choices()
    dialog.doModal()
    if dialog.saved:
        _save(dialog.choices)
        if dialog.sync_now:
            send_event({"action": "sync"})
    del dialog


def offer() -> None:
    """Offer the sync setup unless it was saved before, here or from the settings."""
    if not get_setting_bool("sync_setup_saved"):
        run()
