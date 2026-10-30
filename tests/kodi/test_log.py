import logging
import sys
from unittest import mock

from lib.kodi import log


def _emit(name, level, debug_setting, exc_info=None):
    """Emit one record and return the patched xbmc module and the Kodi log calls it made."""
    record = logging.LogRecord(name, level, "/x/service.py", 1, "hello", None, exc_info)
    with mock.patch.object(log, "xbmc") as xbmc, \
            mock.patch.object(log, "get_setting_bool", return_value=debug_setting):
        log.KodiLogHandler().emit(record)
    return xbmc, xbmc.log.call_args_list


def test_line_uses_addon_id_and_package_category():
    """A lib logger's record reads `<add-on id>: [package.module] message` at its Kodi level."""
    xbmc, calls = _emit("lib.service.scrobbler", logging.WARNING, debug_setting=False)
    assert calls == [mock.call("service.trakt.relay: [service.scrobbler] hello", xbmc.LOGWARNING)]


def test_script_logger_uses_its_file_name():
    """A record from an entry script running as __main__ uses the script's file name."""
    _, calls = _emit("__main__", logging.INFO, debug_setting=False)
    assert calls[0][0][0] == "service.trakt.relay: [service] hello"


def test_debug_goes_to_kodi_debug_or_info_by_setting():
    """Debug records go to Kodi's debug level, or info while the debug setting is on."""
    xbmc, calls = _emit("lib.trakt.api", logging.DEBUG, debug_setting=False)
    assert calls[0][0][1] is xbmc.LOGDEBUG
    xbmc, calls = _emit("lib.trakt.api", logging.DEBUG, debug_setting=True)
    assert calls[0][0][1] is xbmc.LOGINFO


def test_exception_record_carries_traceback():
    """A record logged with an exception carries its traceback after the message."""
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    xbmc, calls = _emit("lib.sync.sync", logging.ERROR, debug_setting=False, exc_info=exc_info)
    text, level = calls[0][0]
    assert level is xbmc.LOGERROR
    assert text.startswith("service.trakt.relay: [sync.sync] hello\nTraceback")
    assert "ValueError: boom" in text
