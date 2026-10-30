from unittest import mock

from lib.service import player as player_module
from lib.service.player import Player
from lib.trakt.objects import TraktObject


def _player(playing=True):
    """A player with its action mocked, playing video or not."""
    action = mock.Mock()
    player = Player(action=action)
    player.isPlayingVideo = mock.Mock(return_value=playing)
    return player, action


def test_start_waits_without_blocking_the_callback():
    """A video start returns at once and queues nothing until it falls due."""
    player, action = _player()
    with mock.patch.object(player_module, "xbmc") as kodi, \
            mock.patch.object(player_module, "get_setting_int", return_value=0):
        player.onAVStarted()
    kodi.sleep.assert_not_called()
    action.assert_not_called()


def test_start_is_queued_once_due():
    """The held start is identified and queued only after the start offset passes."""
    player, _ = _player()
    with mock.patch.object(player_module, "get_setting_int", return_value=1), \
            mock.patch.object(player_module.time, "time", return_value=1000.0):
        player.onAVStarted()
    with mock.patch.object(player, "_queue_start") as queue_start:
        with mock.patch.object(player_module.time, "time", return_value=1059.0):
            player.queue_due_start()
        queue_start.assert_not_called()
        with mock.patch.object(player_module.time, "time", return_value=1060.0):
            player.queue_due_start()
        queue_start.assert_called_once_with()


def test_start_is_dropped_when_playback_stops_first():
    """A video stopped before its start falls due is never queued."""
    player, _ = _player(playing=False)
    with mock.patch.object(player_module, "get_setting_int", return_value=0):
        player.onAVStarted()
    with mock.patch.object(player, "_queue_start") as queue_start, \
            mock.patch.object(player_module.time, "time", return_value=float("inf")):
        player.queue_due_start()
    queue_start.assert_not_called()


def _start(item, replies=None, labels=None, api=None, playlist_size=1, playlist_index=None,
           playing="file.mkv", excluded=lambda path: False):
    """Queue the start of a playing item with Kodi and Trakt stubbed; return the events queued."""
    player, action = _player()
    player.getPlayingFile = mock.Mock(return_value=playing)
    player.playlist_index = playlist_index
    player._playing = playlist_index is not None
    answers = dict({"Player.GetActivePlayers": [{"playerid": 1}],
                    "Player.GetItem": {"item": item}}, **(replies or {}))
    with mock.patch.object(player_module, "request",
                           side_effect=lambda method, _=None: answers[method]), \
            mock.patch.object(player_module, "is_excluded", side_effect=excluded), \
            mock.patch.object(player_module.shared, "api", api or mock.Mock()), \
            mock.patch.object(player_module, "xbmc") as kodi:
        kodi.getInfoLabel.side_effect = lambda name: (labels or {}).get(name, "")
        kodi.PlayList.return_value.size.return_value = playlist_size
        kodi.PlayList.return_value.getposition.return_value = 1
        player._queue_start()
    return [c.args[0] for c in action.call_args_list]


def test_library_episode_sharing_a_file_starts_as_multi_part():
    """A library episode whose file holds other episodes starts with every part listed."""
    events = _start({"type": "episode", "id": 5, "label": "x"}, {
        "VideoLibrary.GetEpisodeDetails": {
            "episodedetails": {"tvshowid": 2, "season": 1, "episode": 3, "file": "f.mkv"}},
        "VideoLibrary.GetEpisodes": {"limits": {"start": 0, "total": 3}, "episodes": [
            {"episodeid": 5, "file": "f.mkv"}, {"episodeid": 6, "file": "f.mkv"},
            {"episodeid": 7, "file": "g.mkv"}]},
    })
    assert events == [{"action": "started", "id": 5, "type": "episode",
                       "multi_episode_data": [5, 6], "multi_episode_count": 2}]


def test_library_movie_starts_by_its_id():
    """A library movie starts with its library id."""
    assert _start({"type": "movie", "id": 9, "label": "x"}) == [
        {"action": "started", "id": 9, "type": "movie"}]


def test_excluded_item_starts_nothing():
    """An item carrying the exclusion property queues no start."""
    item = {"type": "movie", "id": 9, "label": "x",
            "customproperties": {"script.trakt.exclude": "1"}}
    assert _start(item) == []


def test_excluded_plugin_starts_nothing_while_playing_its_resolved_url():
    """A plugin item is excluded by its plugin:// path, though Kodi plays the URL it resolved to."""
    item = {"type": "movie", "id": -1, "label": "x"}
    labels = {"Player.FilenameAndPath": "plugin://plugin.video.x/play/1"}
    assert _start(item, labels=labels, playing="https://cdn.example/x.m3u8",
                  excluded=lambda path: path.startswith("plugin://")) == []


def test_skipping_in_a_playlist_ends_the_previous_video():
    """A start further along a playlist first queues the end of the video before it."""
    events = _start({"type": "movie", "id": 9, "label": "x"}, playlist_size=2, playlist_index=0)
    assert [e["action"] for e in events] == ["ended", "started"]


def test_recording_starts_from_its_player_labels():
    """A PVR recording is identified from the player labels, as live TV is."""
    found = {"action": "started", "type": "episode", "showtitle": "Lost", "season": 1, "episode": 2}
    with mock.patch.object(player_module, "get_playing_details", return_value=("episode", found)):
        events = _start({"type": "recording", "id": 3, "label": "x"})
    assert events == [found]


NAMES_ONLY = {"VideoPlayer.Title": "Lost", "VideoPlayer.EpisodeName": "Pilot"}


def test_program_without_numbers_is_found_by_name():
    """A live TV program with names but no numbers is found on Trakt by episode name and show."""
    hit = mock.Mock(show=mock.Mock(title="Lost", keys=[("trakt", 9)]), pk=(1, 1))
    api = mock.Mock(**{"search_by_text.return_value": [hit]})
    with mock.patch.object(player_module, "get_playing_details", return_value=("", {})):
        events = _start({"type": "channel", "id": 3, "label": "x"}, labels=NAMES_ONLY, api=api)
    assert events == [{"action": "started", "type": "episode", "video_ids": {"trakt": 9},
                       "season": 1, "episode": 1, "episodeTitle": None}]


def test_program_found_through_its_show_is_an_episode():
    """A program found by scanning its show's seasons starts as an episode."""
    pilot = TraktObject({"title": "Pilot"}, keys=[(1, 1)])
    api = mock.Mock(**{
        "search_by_text.side_effect": [[], [mock.Mock(keys=[("trakt", 9)])]],
        "get_show_seasons.return_value": [mock.Mock(episodes={1: pilot})]})
    with mock.patch.object(player_module, "get_playing_details", return_value=("", {})):
        events = _start({"type": "recording", "id": 3, "label": "x"}, labels=NAMES_ONLY, api=api)
    assert events == [{"action": "started", "type": "episode", "video_ids": {"trakt": 9},
                       "season": 1, "episode": 1, "episodeTitle": "Pilot"}]
