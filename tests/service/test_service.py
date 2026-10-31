from unittest import mock

import pytest

from lib.service import service


def _dispatch(action, connected, approve=False):
    """Dispatch an event with Relay connected or not; return the scrobbler, actions and auth."""
    svc = service.Service.__new__(service.Service)
    svc.scrobbler = mock.Mock()
    svc.sync_thread = None
    auth = mock.Mock()
    auth.connect.return_value = approve
    with mock.patch.object(service.shared, "api", mock.Mock(connected=connected)), \
            mock.patch.object(service, "auth", auth), \
            mock.patch.object(service, "actions") as actions:
        svc._dispatch({"action": action, "dbid": "5"})
    return svc.scrobbler, actions, auth


def test_unconnected_play_start_notifies_without_scrobbling():
    """A play start while not connected notifies and leaves the scrobbler alone."""
    scrobbler, _, auth = _dispatch("started", connected=False)
    scrobbler.playback_started.assert_not_called()
    auth.notify_not_connected.assert_called_once_with()


def test_unconnected_script_action_runs_only_once_connected():
    """A script action while not connected asks to connect and runs only when that succeeds."""
    _, actions, auth = _dispatch("rate", connected=False, approve=True)
    auth.connect.assert_called_once_with()
    actions.rate_manually.assert_called_once_with({"action": "rate", "dbid": "5"})
    _, actions, _ = _dispatch("rate", connected=False, approve=False)
    actions.rate_manually.assert_not_called()


def test_sync_setup_opens_without_connecting():
    """The sync setup opens without asking to connect while Relay is not connected."""
    with mock.patch.object(service.setup, "run") as run:
        _, _, auth = _dispatch("syncsetup", connected=False)
    run.assert_called_once_with()
    auth.connect.assert_not_called()


def test_connected_events_run_without_asking():
    """A connected Relay runs events straight away."""
    scrobbler, _, auth = _dispatch("started", connected=True)
    scrobbler.playback_started.assert_called_once_with({"dbid": "5"})
    auth.connect.assert_not_called()


@pytest.mark.parametrize("data, silent, library", [
    ({}, False, "all"),
    ({"silent": "True", "library": "movies"}, True, "movies"),
    ({"silent": "false", "library": "music"}, False, "all"),
])
def test_script_sync_reads_its_arguments(data, silent, library):
    """A script's sync reads the silent flag and a movies or episodes library from its arguments."""
    svc = service.Service.__new__(service.Service)
    svc.scrobbler = mock.Mock()
    svc.start_sync = mock.Mock()
    with mock.patch.object(service.shared, "api", mock.Mock(connected=True)):
        svc._dispatch(dict(data, action="sync"))
    svc.start_sync.assert_called_once_with(manual=True, silent=silent, library=library)


def test_unconnected_sync_notifies_without_starting():
    """A sync while not connected notifies and starts no sync thread."""
    svc = service.Service.__new__(service.Service)
    svc.sync_thread = None
    with mock.patch.object(service.shared, "api", mock.Mock(connected=False)), \
            mock.patch.object(service, "auth") as auth, \
            mock.patch.object(service, "SyncThread") as sync_thread:
        svc.start_sync()
    sync_thread.assert_not_called()
    auth.notify_not_connected.assert_called_once_with()


def test_sync_skips_while_one_runs():
    """A sync asked for while another runs starts no second thread."""
    svc = service.Service.__new__(service.Service)
    svc.sync_thread = mock.Mock(**{"is_alive.return_value": True})
    with mock.patch.object(service.shared, "api", mock.Mock(connected=True)), \
            mock.patch.object(service, "SyncThread") as sync_thread:
        svc.start_sync()
    sync_thread.assert_not_called()


def _retry(pending, result, history=(), exiting=False):
    """Retry queued stops against stubbed Trakt history calls; return the api and queue."""
    svc = service.Service.__new__(service.Service)
    svc.monitor = mock.Mock(**{"abortRequested.return_value": exiting})
    svc.scrobbler = mock.Mock()
    queue = svc.scrobbler.scrobble_queue
    queue.get_pending.return_value = pending
    api = mock.Mock(connected=True, **{"add_to_history.return_value": result,
                                       "get_history.return_value": history})
    with mock.patch.object(service.shared, "api", api):
        svc._retry_failed_scrobbles()
    return api, queue


EPISODE = {"id": 7, "media_type": "episode", "media_info": {"season": 2, "number": 5},
           "show_info": {"ids": {"tvdb": "81189"}, "title": "Show", "year": 2008},
           "watched_at": "2026-10-15T04:56:00.000Z"}


def test_retry_adds_an_episode_to_history_and_dequeues_it():
    """A queued episode stop goes to Trakt's history under its show and leaves the queue."""
    api, queue = _retry([EPISODE], {"added": {"episodes": 1}})
    api.add_to_history.assert_called_once_with({"shows": [{
        "ids": {"tvdb": "81189"}, "title": "Show", "year": 2008,
        "seasons": [{"number": 2, "episodes": [
            {"number": 5, "watched_at": "2026-10-15T04:56:00.000Z"}]}]}]})
    queue.remove.assert_called_once_with(7)


def test_retry_keeps_a_stop_trakt_did_not_take():
    """A failed history add counts a retry and stops the pass."""
    _, queue = _retry([EPISODE, dict(EPISODE, id=8)], None)
    queue.increment_retry.assert_called_once_with(7)
    queue.remove.assert_not_called()


def test_retry_drops_a_stop_trakt_already_has():
    """A queued stop whose play is in Trakt's history within 5 minutes leaves the queue unsent."""
    play = {"episode": {"season": 2, "number": 5},
            "show": {"ids": {"tvdb": 81189, "trakt": 1}, "title": "Show", "year": 2008}}
    api, queue = _retry([EPISODE], None, history=[play])
    api.get_history.assert_called_once_with(
        "episode", "2026-10-15T04:51:00.000Z", "2026-10-15T05:01:00.000Z")
    api.add_to_history.assert_not_called()
    queue.remove.assert_called_once_with(7)


def test_retry_sends_a_stop_when_only_another_episode_was_played():
    """A play of another episode of the show in the window does not count as the queued one."""
    play = {"episode": {"season": 2, "number": 6},
            "show": {"ids": {"tvdb": 81189}, "title": "Show", "year": 2008}}
    api, _ = _retry([EPISODE], {"added": {"episodes": 1}}, history=[play])
    api.add_to_history.assert_called_once()


def test_retry_waits_when_the_history_cannot_be_read():
    """An unreadable history counts a retry and sends nothing."""
    api, queue = _retry([EPISODE], {"added": {"episodes": 1}}, history=None)
    api.add_to_history.assert_not_called()
    queue.increment_retry.assert_called_once_with(7)


def test_retry_stops_once_kodi_exits():
    """Kodi exiting stops the retry pass before its next queued stop."""
    api, queue = _retry([EPISODE], {"added": {"episodes": 1}}, exiting=True)
    api.get_history.assert_not_called()
    queue.remove.assert_not_called()


def _service():
    """A service ready to dispatch, and the mock standing in for its dispatch."""
    svc = service.Service()
    svc.monitor = mock.Mock(**{"abortRequested.return_value": False})
    dispatch = mock.Mock()
    svc._dispatch = dispatch
    return svc, dispatch


def test_event_dispatches_as_it_arrives():
    """A queued event is dispatched before the queuing call returns."""
    svc, dispatch = _service()
    svc._enqueue({"action": "paused"})
    dispatch.assert_called_once_with({"action": "paused"})


def test_event_queued_during_a_dispatch_waits_its_turn():
    """An event arriving mid-dispatch runs after the current one, in order."""
    svc, dispatch = _service()
    order = []

    def run(data):
        order.append(("start", data["n"]))
        if data["n"] == 1:
            svc._enqueue({"n": 2})
        order.append(("end", data["n"]))

    dispatch.side_effect = run
    svc._enqueue({"n": 1})
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


@pytest.mark.parametrize("playing", [True, False])
def test_hooks_pick_up_a_video_already_playing(playing):
    """Starting the hooks mid-playback holds a start for the playing video, as Kodi sends none."""
    svc = service.Service()
    with mock.patch.object(service, "Monitor"), mock.patch.object(service, "Player") as player, \
            mock.patch.object(service.shared, "api", mock.Mock()):
        player.return_value.isPlayingVideo.return_value = playing
        svc._start_hooks()
    assert player.return_value.onAVStarted.called is playing


def test_hooks_give_the_trakt_client_the_service_monitor():
    """The Trakt client gets the service's monitor, to stop its waits once Kodi exits."""
    svc = service.Service()
    api = mock.Mock()
    with mock.patch.object(service, "Monitor") as monitor, mock.patch.object(service, "Player"), \
            mock.patch.object(service.shared, "api", api):
        svc._start_hooks()
    assert api.client.monitor is monitor.return_value


def test_exit_during_the_startup_delay_ends_the_run():
    """Kodi exiting during the startup delay ends the run before anything is set up."""
    svc = service.Service()
    with mock.patch.object(service, "get_setting_int", return_value=5), \
            mock.patch.object(service, "xbmc") as kodi, \
            mock.patch.object(service, "TraktAPI") as api:
        kodi.Monitor.return_value.waitForAbort.return_value = True
        svc.run()
    kodi.Monitor.return_value.waitForAbort.assert_called_once_with(5)
    api.assert_not_called()
