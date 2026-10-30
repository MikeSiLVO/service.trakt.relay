from unittest import mock

from lib.service.monitor import ADDON_ID, EVENT, Monitor


def _monitor():
    """A monitor and its mocked action."""
    action = mock.Mock()
    return Monitor(action=action), action


def test_settings_change_reloads_the_settings():
    """A save of Relay's settings makes the monitor reload them."""
    monitor, _ = _monitor()
    with mock.patch("lib.service.monitor.reload_settings") as reload_settings:
        monitor.onSettingsChanged()
    reload_settings.assert_called_once_with()


def test_clean_run_by_a_scan_is_not_queued():
    """A clean inside a video scan queues only the scan's finish."""
    monitor, action = _monitor()
    monitor.onScanStarted("video")
    monitor.onCleanFinished("video")
    monitor.onScanFinished("video")
    action.assert_called_once_with({"action": "scanFinished"})


def test_clean_on_its_own_is_queued():
    """A clean outside a scan queues a library clean."""
    monitor, action = _monitor()
    monitor.onCleanFinished("video")
    action.assert_called_once_with({"action": "databaseCleaned"})


def test_script_event_is_queued():
    """An event a script sends through a Kodi notification is queued as sent."""
    monitor, action = _monitor()
    monitor.onNotification(ADDON_ID, "Other." + EVENT, '{"action": "sync"}')
    action.assert_called_once_with({"action": "sync"})

