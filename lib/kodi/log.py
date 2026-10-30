import logging

import xbmc

from lib.kodi.client import ADDON
from lib.kodi.settings import get_setting_bool

ADDON_ID = ADDON.getAddonInfo("id")


def kodi_level(levelno: int) -> int:
    """Map a logging level to Kodi's, raising debug to info while the debug setting is on."""
    if levelno >= logging.CRITICAL:
        return xbmc.LOGFATAL
    if levelno >= logging.ERROR:
        return xbmc.LOGERROR
    if levelno >= logging.WARNING:
        return xbmc.LOGWARNING
    if levelno >= logging.INFO or get_setting_bool("debug"):
        return xbmc.LOGINFO
    return xbmc.LOGDEBUG


class KodiLogHandler(logging.Handler):
    """Logging handler that writes each record to kodi.log as `<add-on id>: [category] message`."""

    def emit(self, record: logging.LogRecord) -> None:
        """Emit the record, its category the logger name minus `lib.`, else the file name."""
        try:
            name = record.name
            category = name[4:] if name.startswith("lib.") else record.module
            message = "%s: [%s] %s" % (ADDON_ID, category, self.format(record))
            xbmc.log(message, kodi_level(record.levelno))
        except Exception:
            self.handleError(record)


def configure_logging() -> None:
    """Configure logging to route every logger in the add-on to kodi.log."""
    root = logging.getLogger()
    root.addHandler(KodiLogHandler())
    root.setLevel(logging.DEBUG)
