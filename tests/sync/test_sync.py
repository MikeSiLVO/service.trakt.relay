from typing import cast
from unittest import mock

from lib.sync import sync as sync_module


def _sync(manual, silent=False):
    """A Sync with notifications on and its progress dialog mocked."""
    settings = {"sync_on_update": True, "show_sync_notifications": True}
    with mock.patch.object(sync_module, "get_setting_bool", side_effect=settings.get):
        sync = sync_module.Sync(manual=manual, silent=silent)
    sync.progress = mock.Mock()
    return sync


def _half(sync, finished):
    """Run a half that finishes or not; return the notices it showed."""
    half = mock.Mock()
    half.run.return_value = finished
    with mock.patch.object(sync_module, "notify") as notify, \
            mock.patch.object(sync_module, "get_string", side_effect=str):
        sync._run_half(half, 32213)
    return [c.args[:2] for c in notify.call_args_list]


def test_background_half_notifies_its_start_and_finish():
    """A background sync notifies each half's start and, once it finishes, its finish."""
    started, finished = ("32213", "32061"), ("32213", "32062")
    assert _half(_sync(manual=False), finished=True) == [started, finished]
    assert _half(_sync(manual=False), finished=False) == [started]


def test_manual_half_shows_the_dialog_unless_silent():
    """A manual sync shows and closes the progress dialog, and a silent one shows none."""
    sync = _sync(manual=True)
    assert _half(sync, finished=True) == []
    cast(mock.Mock, sync.progress).create.assert_called_once()
    cast(mock.Mock, sync.progress).close.assert_called_once_with()
    silent = _sync(manual=True, silent=True)
    _half(silent, finished=True)
    cast(mock.Mock, silent.progress).create.assert_not_called()


def test_counted_step_shows_its_name_count_and_bar():
    """A counted step shows its name, its count so far and a bar at the same share."""
    sync = _sync(manual=True)
    strings = {32074: "Kodi watched status", 32089: "%i of %i"}
    with mock.patch.object(sync_module, "get_string", side_effect=strings.get):
        sync.update_count(100, 200, 40, 60, 32074)
    cast(mock.Mock, sync.progress).update.assert_called_once_with(
        50, "Kodi watched status\n100 of 200\n")
