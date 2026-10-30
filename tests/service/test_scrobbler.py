from unittest import mock

import pytest

from lib.service import scrobbler


def _scrobbler(watched_time):
    """Make a scrobbler playing a 100-minute library movie, watched for the given seconds."""
    s = scrobbler.Scrobbler.__new__(scrobbler.Scrobbler)
    s.api = mock.Mock(**{"scrobble_movie.return_value": None})
    s.scrobble_queue = mock.Mock()
    s.playing, s.paused, s.pvr, s.multi_part, s.stop_pending = True, False, False, False, False
    s.video = {"type": "movie", "id": 1}
    s.video_info = {"title": "Movie", "year": 2020, "ids": {"tmdb": 1}}
    s.show_info = None
    s.duration, s.watched_time = 6000, watched_time
    s.to_rate, s.playlist_index, s.part = [], 0, 0
    s.last_check, s.seek_due = 0, None
    return s


@pytest.mark.parametrize("watched_time", [0, 30])
def test_stop_under_one_percent_is_sent(watched_time):
    """A stop under 1% goes to Trakt, which clears the watching state on it."""
    s = _scrobbler(watched_time)
    info = s.video_info
    with mock.patch.object(scrobbler, "get_setting_bool", return_value=True), \
            mock.patch.object(scrobbler, "check_rating"):
        s.playback_ended()
    s.api.scrobble_movie.assert_called_once_with(info, watched_time / 60, "stop")


def test_started_playback_does_not_sleep():
    """Loading a started video never sleeps the service thread."""
    s = _scrobbler(0)
    with mock.patch.object(scrobbler, "xbmc") as kodi, \
            mock.patch.object(scrobbler, "get_setting_bool", return_value=True), \
            mock.patch.object(scrobbler, "get_movie_details", return_value=None), \
            mock.patch.object(s, "_scrobble"):
        s.playback_started({"type": "movie", "id": 1})
    kodi.sleep.assert_not_called()


def test_seek_burst_sends_one_start_once_settled():
    """Seeks in quick succession send a single start, 2 seconds after the last one."""
    s = _scrobbler(600)
    s.last_check = 10 ** 9
    with mock.patch.object(scrobbler, "xbmc") as kodi, \
            mock.patch.object(s, "_scrobble") as scrobble:
        kodi.Player.return_value.getTime.return_value = 600.0
        kodi.PlayList.return_value.getposition.return_value = 0
        for now in (100.0, 100.5, 101.0):
            with mock.patch.object(scrobbler.time, "time", return_value=now):
                s.playback_seek()
        with mock.patch.object(scrobbler.time, "time", return_value=102.9):
            s.check_transition()
        scrobble.assert_not_called()
        with mock.patch.object(scrobbler.time, "time", return_value=103.0):
            s.check_transition()
            s.check_transition()
    scrobble.assert_called_once_with("start")


def test_seek_updates_the_watched_time_at_once():
    """A seek back to the start sets the watched time straight away, ready for a quick stop."""
    s = _scrobbler(3000)
    s.last_check = 10 ** 9
    with mock.patch.object(scrobbler, "xbmc") as kodi, mock.patch.object(s, "_scrobble"):
        kodi.Player.return_value.getTime.return_value = 0.5
        kodi.PlayList.return_value.getposition.return_value = 0
        s.playback_seek()
    assert s.watched_time == 0.5


def test_seek_to_the_end_scrobbles_one_full_stop():
    """A seek to the end then the natural end sends a stop at 100% and no late start."""
    s = _scrobbler(3000)
    s.last_check = 10 ** 9
    info = s.video_info
    with mock.patch.object(scrobbler, "xbmc") as kodi, \
            mock.patch.object(scrobbler, "get_setting_bool", return_value=True), \
            mock.patch.object(scrobbler, "check_rating"):
        kodi.Player.return_value.getTime.return_value = 5990.0
        kodi.PlayList.return_value.getposition.return_value = 0
        with mock.patch.object(scrobbler.time, "time", return_value=100.0):
            s.playback_seek()
            s.playback_ended(natural=True)
        with mock.patch.object(scrobbler.time, "time", return_value=103.0):
            s.check_transition()
    s.api.scrobble_movie.assert_called_once_with(info, 100.0, "stop")


PROGRAM = {"VideoPlayer.Title": "Show", "VideoPlayer.EpisodeName": "Pilot",
           "VideoPlayer.Season": "1", "VideoPlayer.Episode": "2",
           "PVR.EpgEventElapsedTime(hh:mm:ss)": "00:00:30",
           "PVR.EpgEventDuration(hh:mm:ss)": "00:30:00"}


def _live(labels, found=None):
    """Run a live TV check; return the scrobbler, scrobble and start mocks, and stop times."""
    s = _scrobbler(1790)
    s.pvr, s.duration, s.last_check = True, 1800, 0
    s.video = {"type": "episode", "title": "Pilot", "showtitle": "Show", "season": 1, "episode": 2}
    s.video_info = {"title": "Pilot", "season": 1, "number": 2}
    s.show_info = {"title": "Show"}
    s.program = ("Show", "Pilot", "1", "2")
    with mock.patch.object(scrobbler, "xbmc") as kodi, \
            mock.patch.object(scrobbler, "get_playing_item",
                              return_value={"item": {"type": "channel", "id": 1}}), \
            mock.patch.object(scrobbler, "identify", return_value=found), \
            mock.patch.object(s, "playback_started") as started, \
            mock.patch.object(s, "_scrobble") as scrobble:
        kodi.getInfoLabel.side_effect = lambda name: labels.get(name, "")
        scrobble.side_effect = lambda status: stopped_at.append(s.watched_time)
        stopped_at = []
        s.check_transition()
    return s, scrobble, started, stopped_at


def test_live_tv_without_a_program_change_scrobbles_nothing():
    """A minute check on the same live TV program sends no stop or start."""
    _, scrobble, _, _ = _live(PROGRAM)
    scrobble.assert_not_called()


def test_live_tv_change_to_an_unknown_program_stops_the_old_one():
    """A change to a program that can't be identified stops the old one and starts nothing."""
    s, scrobble, _, _ = _live(dict(PROGRAM, **{"VideoPlayer.Title": "News"}))
    scrobble.assert_called_once_with("stop")
    assert s.video_info is None


def test_live_tv_change_stops_the_old_program_at_its_last_time():
    """A new program stops the old one at the time last seen, then starts the new one."""
    new = {"action": "started", "type": "episode", "title": "Next", "showtitle": "Show",
           "season": 1, "episode": 3}
    _, scrobble, started, stopped_at = _live(
        dict(PROGRAM, **{"VideoPlayer.EpisodeName": "Next", "VideoPlayer.Episode": "3"}), new)
    scrobble.assert_called_once_with("stop")
    assert stopped_at == [1790]
    started.assert_called_once_with({k: v for k, v in new.items() if k != "action"})


def test_recording_is_timed_by_the_player():
    """A PVR recording takes its time and length from the player, not the TV guide."""
    s = _scrobbler(0)
    s.pvr = True
    with mock.patch.object(scrobbler, "xbmc") as kodi, \
            mock.patch.object(scrobbler, "get_setting_bool",
                              side_effect=lambda name: name == "scrobble_fallback"), \
            mock.patch.object(s, "_scrobble"):
        kodi.getCondVisibility.return_value = False
        kodi.Player.return_value.getPlayingFile.return_value = "pvr://recordings/tv/active/1"
        kodi.Player.return_value.getTotalTime.return_value = 1800.0
        s.playback_started({"type": "episode", "title": "Pilot", "showtitle": "Lost",
                            "season": 1, "episode": 2})
    assert s.pvr is False
    assert s.duration == 1800.0


def test_live_tv_episode_found_by_name_scrobbles_without_a_title():
    """A live TV episode known by show ids and numbers alone scrobbles without a title check."""
    s = _scrobbler(600)
    s.pvr = True
    s.video = {"type": "episode", "video_ids": {"trakt": 9}, "season": 1, "episode": 1}
    s.video_info, s.show_info = {"season": 1, "number": 1}, {"ids": {"trakt": 9}}
    s.api.scrobble_episode.return_value = {"action": "start", "episode": {"title": "Pilot"}}
    with mock.patch.object(scrobbler, "get_setting_bool",
                           side_effect=lambda name: name == "scrobble_episode"):
        assert s._scrobble("start") == {"action": "start", "episode": {"title": "Pilot"}}
    assert s.stop_pending is False


def test_rating_only_episode_is_fetched_by_its_trakt_id():
    """An episode rated but not scrobbled is fetched by its show's Trakt id, never a TMDB number."""
    s = _scrobbler(0)
    s.video = {"type": "episode", "id": 5}
    s.video_info, s.show_info = {"season": 1, "number": 2}, {"ids": {"tmdb": 1399}}
    s.api.get_show_summary.return_value.to_dict.return_value = {"ids": {"trakt": 1390}}
    s.api.get_episode_summary.return_value.to_dict.return_value = {"ids": {"trakt": 73641}}
    on = {"scrobble_movie", "rate_episode"}
    with mock.patch.object(scrobbler, "get_setting_bool", side_effect=lambda name: name in on), \
            mock.patch.object(scrobbler, "find_trakt_id", return_value=1390) as find:
        result = s._scrobble_start()
    find.assert_called_once_with(1399, "tmdb", "show")
    s.api.get_episode_summary.assert_called_once_with(1390, 1, 2)
    s.api.scrobble_episode.assert_not_called()
    assert result == {"show": {"ids": {"trakt": 1390}},
                      "episode": {"ids": {"trakt": 73641}, "season": 1}}


def test_rating_only_movie_trakt_cannot_fetch_gives_nothing_to_rate():
    """A movie rated but not scrobbled gives nothing to rate when Trakt can't fetch it."""
    s = _scrobbler(0)
    s.api.get_movie_summary.return_value.to_dict.return_value = {}
    with mock.patch.object(scrobbler, "get_setting_bool",
                           side_effect=lambda name: name == "rate_movie"), \
            mock.patch.object(scrobbler, "find_trakt_id", return_value=603):
        assert s._scrobble_start() is None
    s.api.scrobble_movie.assert_not_called()


@pytest.mark.parametrize("reply, header", [
    ({"action": "start"}, "32217"),
    ({"action": "pause", "progress": 42.5}, "Paused at 42%"),
    ({"action": "scrobble", "progress": 95.0}, "32015"),
])
def test_scrobble_notice_says_what_trakt_made_of_it(reply, header):
    """The notice reads watching, paused at the percent, or watched, per Trakt's reply."""
    s = _scrobbler(0)
    strings = {32215: "Paused at %s"}
    with mock.patch.object(scrobbler, "get_setting_bool", return_value=True), \
            mock.patch.object(scrobbler, "get_string",
                              side_effect=lambda i: strings.get(i, str(i))), \
            mock.patch.object(scrobbler, "notify") as notify:
        s._notify_scrobble(dict(reply, movie={"title": "Movie", "year": 2020}))
    notify.assert_called_once_with(header, "Movie (2020)")
