from unittest import mock

import pytest

from lib.sync import episodes as episodes_module
from lib.sync.episodes import EpisodeSync
from tests.helpers import Text


def test_show_description_lists_episode_codes():
    """A show's log description names its tvdb id and every episode as SxxEyy, with fallbacks."""
    describe = EpisodeSync.__new__(EpisodeSync)._describe
    show = {
        "title": "Lost", "ids": {"tvdb": "73739"},
        "seasons": [{"number": 1, "episodes": [{"number": 2}, {"number": 3}]}],
    }
    assert describe(show) == "Lost [tvdb: 73739] - S01E02, S01E03"
    assert describe({"title": "Lost", "ids": {}}) == "Lost [tvdb: No id] - All"


def _mark(queued=0, **settings):
    """Mark Kodi episodes watched with settings and queued stops mocked; return mark and unwatch."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    settings = {"kodi_episode_playcount": True, "rewatch_aware_sync": True,
                "scrobble_episode": True, **settings}
    with mock.patch.object(episodes_module, "get_setting_bool", side_effect=settings.get), \
            mock.patch.object(episodes_module, "get_string", return_value=Text()), \
            mock.patch.object(episodes_module, "ScrobbleQueue", return_value=[None] * queued), \
            mock.patch.object(episodes_module, "episodes_to_mark_watched",
                              return_value=[]) as mark, \
            mock.patch.object(episodes_module, "episodes_to_unwatch", return_value=[]) as unwatch:
        EpisodeSync(sync)._mark_watched_in_kodi({}, {}, {}, 0, 10)
    return mark, unwatch


@pytest.mark.parametrize("rewatch", [True, False])
def test_episode_watched_marking_follows_the_rewatch_setting(rewatch):
    """Marking skips plays from before a rewatch, and unwatching runs, only with rewatch sync on."""
    mark, unwatch = _mark(rewatch_aware_sync=rewatch)
    assert mark.call_args.kwargs["apply_rewatch_filter"] is rewatch
    assert unwatch.called is rewatch


@pytest.mark.parametrize("push", [True, False])
def test_rewatch_unwatching_keeps_unplayed_episodes_while_the_history_push_is_on(push):
    """Episodes Trakt never played stay watched when the history push sends them this sync."""
    _, unwatch = _mark(sync_playcount_episodes_to_trakt=push)
    assert unwatch.call_args.kwargs["keep_unplayed"] is push


def test_rewatch_unwatching_waits_for_kodi_plays_to_reach_trakt():
    """Nothing is unwatched while episode scrobbling is off or a failed stop waits for retry."""
    _, unwatch = _mark(scrobble_episode=False)
    unwatch.assert_not_called()
    _, unwatch = _mark(queued=1)
    unwatch.assert_not_called()
