from typing import Any, Union

import xbmcaddon

from lib.kodi.client import ADDON, request

TRAKT_TO_KODI, KODI_TO_TRAKT, BOTH_WAYS = 1, 2, 3
ADD_AND_REMOVE = 2


def reload_settings() -> None:
    """Reload the settings by swapping in a fresh Addon for every later read and write."""
    # Kodi pushes a save only to the first Addon of the id, which a stopped service can leave behind
    global ADDON
    ADDON = xbmcaddon.Addon()


def show_settings() -> None:
    """Open the add-on settings dialog."""
    ADDON.openSettings()


def get_setting(setting: str) -> str:
    """Get a setting as a stripped string."""
    return ADDON.getSetting(setting).strip()


def set_setting(setting: str, value: Union[str, int, float, bool]) -> None:
    """Set a setting, storing any value as its string form, unless that value is already stored."""
    # each save rewrites the whole settings file
    if ADDON.getSetting(setting) != str(value):
        ADDON.setSetting(setting, str(value))


def get_setting_bool(setting: str) -> bool:
    """Get a setting as a bool, True only for 'true'."""
    return get_setting(setting).lower() == "true"


def get_setting_float(setting: str) -> float:
    """Get a setting as a float, or 0 when it does not parse."""
    try:
        return float(get_setting(setting))
    except ValueError:
        return 0


def get_setting_int(setting: str) -> int:
    """Get a setting as an int, truncating any fraction, or 0 when it does not parse."""
    try:
        return int(get_setting_float(setting))
    except ValueError:
        return 0


def syncs_to_kodi(setting: str) -> bool:
    """True when a direction setting copies Trakt to Kodi, one way or both."""
    return get_setting_int(setting) in (TRAKT_TO_KODI, BOTH_WAYS)


def syncs_to_trakt(setting: str) -> bool:
    """True when a direction setting sends Kodi to Trakt, one way or both."""
    return get_setting_int(setting) in (KODI_TO_TRAKT, BOTH_WAYS)


def get_string(string_id: int) -> str:
    """Get a localized string from the add-on's strings.po."""
    return ADDON.getLocalizedString(string_id)


def get_kodi_setting(setting: str) -> Any:
    """Get one of Kodi's own settings, or None when Kodi refuses the id."""
    result = request("Settings.GetSettingValue", {"setting": setting})
    return result.get("value") if result else None
