import logging
from typing import cast
from unittest import mock

from lib.sync import sync as sync_module
from tests.helpers import Text


def _sync(manual, silent=False):
    """A Sync with notifications on, Kodi not exiting and its progress dialog mocked."""
    settings = {"sync_on_update": True, "show_sync_notifications": True}
    monitor = mock.Mock(**{"abortRequested.return_value": False})
    with mock.patch.object(sync_module, "get_setting_bool", side_effect=settings.get):
        sync = sync_module.Sync(manual=manual, silent=silent, monitor=monitor)
    sync.progress = mock.Mock()
    return sync


def _half(sync, finished, failed=0):
    """Run a half that finishes or not, failing as many items as given; return its notices."""
    half = mock.Mock()

    def run():
        sync.failed += failed
        return finished

    half.run.side_effect = run
    with mock.patch.object(sync_module, "notify") as notify, \
            mock.patch.object(sync_module, "get_string", side_effect=lambda i: Text(i)):
        sync._run_half(half, 32213)
    return [c.args[:2] for c in notify.call_args_list]


def test_background_half_notifies_its_start_and_finish():
    """A background sync notifies each half's start and, once it finishes, its finish."""
    started, finished = ("32213", "32061"), ("32213", "32062")
    assert _half(_sync(manual=False), finished=True) == [started, finished]
    assert _half(_sync(manual=False), finished=False) == [started]


def test_half_with_failures_notifies_them_unless_silent():
    """A half with failed items notifies their count, not finished, unless the sync is silent."""
    started, failures = ("32213", "32061"), ("32213", "32226")
    assert _half(_sync(manual=False), finished=True, failed=3) == [started, failures]
    assert _half(_sync(manual=True), finished=True, failed=3) == [failures]
    assert _half(_sync(manual=True, silent=True), finished=True, failed=3) == []


def _shows(count):
    """Shows in Trakt form, each with one episode."""
    return [{"title": "Show %i" % n, "ids": {"tvdb": n},
             "seasons": [{"number": 1, "episodes": [{"number": 1}]}]} for n in range(count)]


def test_send_counts_what_trakt_took_and_what_failed(caplog):
    """Each chunk counts what Trakt's reply says it took, or every item in it when it fails."""
    sync = _sync(manual=True)
    cast(mock.Mock, sync.progress).iscanceled.return_value = False
    write = mock.Mock(side_effect=[None, {"added": {"episodes": 9},
                                          "not_found": {"shows": [{"ids": {"tvdb": 55}}]}}])
    strings = {32085: "Added %i", 32089: "%i of %i", 32226: "%i couldn't be synced"}
    caplog.set_level(logging.INFO, logger=sync_module.__name__)
    with mock.patch.object(sync_module, "get_string", side_effect=lambda i: strings.get(i, "")):
        sync.send(write, _shows(60), "episodes", 32068, 32085, 40, 60)
    assert [len(c.args[0]["shows"]) for c in write.call_args_list] == [50, 10]
    assert sync.failed == 50
    cast(mock.Mock, sync.progress).update.assert_called_with(
        60, "\nAdded 9\n50 couldn't be synced")
    assert "'tvdb': 55" in caplog.text


def test_sync_counts_as_canceled_once_kodi_exits():
    """A background sync with no dialog counts as canceled once Kodi starts exiting."""
    sync = _sync(manual=False)
    assert sync.is_canceled() is False
    sync.monitor.abortRequested.return_value = True
    assert sync.is_canceled() is True


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
