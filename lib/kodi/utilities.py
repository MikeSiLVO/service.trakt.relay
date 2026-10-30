import xbmc
import xbmcgui
import xbmcvfs
import json
import os
import re
import logging
from typing import Any, Tuple, Dict, Optional
from urllib.parse import quote
from lib.media.match import split_title_year
from lib.kodi.client import ADDON
from lib.kodi.settings import get_kodi_setting, get_setting, get_setting_bool

logger = logging.getLogger(__name__)

RED_ICON = os.path.join(ADDON.getAddonInfo("path"), "resources", "media", "icon-red.png")
REGEX_URL = "(^https?://)(.+)"
DB_TYPES = {"movie": "movie", "tvshow": "show", "season": "season", "episode": "episode"}
IDLE_CONDITION = (
    "[Window.IsActive(home) | Window.IsMedia] + !System.HasActiveModalDialog"
    " + !Player.HasMedia + !System.ScreenSaverActive"
)


def database_path() -> str:
    """Build the path of Relay's SQLite database, creating its profile folder if missing."""
    folder = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not xbmcvfs.exists(folder):
        xbmcvfs.mkdir(folder)
    return os.path.join(folder, "queue.db")


def remove_empty_settings() -> bool:
    """Remove an empty user settings file, which Kodi can't load and then never saves over."""
    path = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo("profile")), "settings.xml")
    if os.path.isfile(path) and os.path.getsize(path) == 0:
        os.remove(path)
        return True
    return False


def notify(header: str, message: str, time: int = 5000) -> None:
    """Show a Kodi notification with the add-on icon, or its red one while the OG theme is on."""
    icon = RED_ICON if get_setting_bool("og_theme") else ADDON.getAddonInfo("icon")
    xbmcgui.Dialog().notification(header, message, icon, time)


def set_theme(window: xbmcgui.Window) -> None:
    """Set a Relay dialog's Trakt logo and accent colors, red or purple per the OG theme setting."""
    red = get_setting_bool("og_theme")
    window.setProperty("logo", "trakt-relay-logo-red.png" if red else "trakt-relay-logo.png")
    window.setProperty("accent", "FFED2224" if red else "FF9F42C6")
    window.setProperty("highlight", "FFF47A7C" if red else "FFC580E6")


def count_idle(held: int) -> int:
    """Count one more idle tick in a row, or restart at 0 when Kodi is busy."""
    return held + 1 if xbmc.getCondVisibility(IDLE_CONDITION) else 0


def is_excluded(fullpath: str) -> bool:
    """True when the exclusion settings rule out scrobbling this path."""
    if not fullpath:
        return True

    if fullpath.startswith("pvr://") and get_setting_bool("ExcludeLiveTV"):
        logger.debug(
            "is_excluded(): Video is playing via Live TV, which is currently set as "
            "excluded location."
        )
        return True

    if fullpath.startswith(("http://", "https://")) and get_setting_bool("ExcludeHTTP"):
        logger.debug(
            "is_excluded(): Video is playing via HTTP source, which is currently set as "
            "excluded location."
        )
        return True

    if fullpath.startswith("plugin://") and get_setting_bool("ExcludePlugin"):
        logger.debug(
            "is_excluded(): Video is playing via Plugin source, which is currently set as "
            "excluded location."
        )
        return True

    if xbmcgui.Window(10000).getProperty(
        "script.trakt.paused"
    ) == "true" and get_setting_bool("ExcludeScript"):
        logger.debug(
            "is_excluded(): Video is playing via Script source, which is currently set as "
            "excluded location."
        )
        return True

    for slot in range(1, 13):
        suffix = str(slot) if slot > 1 else ""
        path = get_setting("ExcludePath" + suffix)
        if path and get_setting_bool("ExcludePathOption" + suffix) and fullpath.startswith(path):
            logger.debug("is_excluded(): Video is under excluded path %i." % slot)
            return True

    return False


def get_proxy_url() -> Optional[str]:
    """Build the proxy URL from Relay's override or Kodi's HTTP proxy, or None when unset."""
    if get_setting_bool("proxy_override"):
        host, port, username, password = (
            get_setting(s) for s in ("proxy_uri", "proxy_port", "proxy_user", "proxy_passwd")
        )
    # urllib talks plain HTTP to a proxy, so only Kodi's HTTP proxy type works
    elif (
        get_kodi_setting("network.usehttpproxy")
        and get_kodi_setting("network.httpproxytype") == 0
    ):
        host, port, username, password = (
            str(get_kodi_setting("network.httpproxy" + s) or "")
            for s in ("server", "port", "username", "password")
        )
    else:
        return None
    if not host or not port:
        return None
    match = re.match(REGEX_URL, host)
    scheme, address = match.groups() if match else ("http://", host)
    credentials = ""
    if username and password:
        credentials = "%s:%s@" % (quote(username, safe=""), quote(password, safe=""))
    return "%s%s%s:%s" % (scheme, credentials, address, port)


def get_playing_details(result: Dict) -> Tuple[str, Dict]:
    """Build scrobble data for a non-library item from player infolabels, or ('', {})."""
    media_type = result["item"]["type"]
    data: Dict[str, Any] = {"action": "started"}
    if "id" not in result["item"] or media_type in ("channel", "recording"):
        logger.debug(
            "get_playing_details - Started playing a non-library file, checking available data."
        )
        season = int(xbmc.getInfoLabel("VideoPlayer.Season") or "-1")
        episode = int(xbmc.getInfoLabel("VideoPlayer.Episode") or "-1")
        showtitle = xbmc.getInfoLabel("VideoPlayer.TVShowTitle") or xbmc.getInfoLabel(
            "VideoPlayer.Title"
        )
        title = xbmc.getInfoLabel("VideoPlayer.EpisodeName")
        year = xbmc.getInfoLabel("VideoPlayer.Year")
        video_ids = xbmcgui.Window(10000).getProperty("script.trakt.ids")
        if video_ids:
            data["video_ids"] = json.loads(video_ids)
        logger.debug(
            "get_playing_details info - ids: %s, showtitle: %s, Year: %s, Season: %s, Episode: %s"
            % (video_ids, showtitle, year, season, episode)
        )

        if season >= 0 and episode > 0 and (showtitle or video_ids):
            media_type = "episode"
            data["type"] = "episode"
            data["season"] = season
            data["episode"] = episode
            data["showtitle"] = showtitle
            data["title"] = title or showtitle
            if year.isdigit():
                data["year"] = int(year)
            logger.debug(
                "get_playing_details - Playing a non-library 'episode' - %s - S%02dE%02d - %s."
                % (data["showtitle"], data["season"], data["episode"], data["title"])
            )
        elif (year or video_ids) and season < 0 and not title:
            media_type = "movie"
            data["type"] = "movie"
            if year.isdigit():
                data["year"] = int(year)
            data["title"] = split_title_year(showtitle)[0] or showtitle
            logger.debug(
                "get_playing_details - Playing a non-library 'movie' - %s (%s)."
                % (data["title"], data.get("year", "NaN"))
            )
        else:
            logger.debug(
                "get_playing_details - Non-library file, not enough data for scrobbling, skipping."
            )
            return "", {}
    return media_type, data
