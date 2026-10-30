from unittest import mock

import pytest

from lib.kodi.settings import BOTH_WAYS, KODI_TO_TRAKT, TRAKT_TO_KODI
from lib.sync import episodes as episodes_module
from lib.sync.episodes import EpisodeSync
from tests.helpers import Text, settings


def test_show_description_lists_episode_codes():
    """A show's log description names its tvdb id and every episode as SxxEyy, with fallbacks."""
    describe = EpisodeSync.__new__(EpisodeSync)._describe
    show = {
        "title": "Lost", "ids": {"tvdb": "73739"},
        "seasons": [{"number": 1, "episodes": [{"number": 2}, {"number": 3}]}],
    }
    assert describe(show) == "Lost [tvdb: 73739] - S01E02, S01E03"
    assert describe({"title": "Lost", "ids": {}}) == "Lost [tvdb: No id] - All"


UNWATCH = {"episodeid": 4, "playcount": 0, "lastplayed": ""}


def _mark(direction=BOTH_WAYS, queued=0, **values):
    """Mark Kodi episodes watched with settings and queued stops mocked; return the mocks."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    sync.confirm_removals.side_effect = lambda removals, message_id: removals
    values = {"sync_watched_episodes": direction, "rewatch_aware_sync": True,
              "scrobble_episode": True, **values}
    with settings(**values), \
            mock.patch.object(episodes_module, "get_string", return_value=Text()), \
            mock.patch.object(episodes_module, "ScrobbleQueue", return_value=[None] * queued), \
            mock.patch.object(episodes_module, "episodes_to_mark_watched",
                              return_value=[]) as mark, \
            mock.patch.object(episodes_module, "episodes_to_unwatch",
                              return_value=[UNWATCH]) as unwatch, \
            mock.patch.object(episodes_module, "episodes_to_mirror",
                              return_value=([], [UNWATCH])) as mirror, \
            mock.patch.object(episodes_module, "batch_request") as batch:
        EpisodeSync(sync)._mark_watched_in_kodi({}, {}, {}, None, 0, 10)
    written = [update for c in batch.call_args_list for update in c.args[1]]
    return mock.Mock(mark=mark, unwatch=unwatch, mirror=mirror, written=written, sync=sync)


@pytest.mark.parametrize("rewatch", [True, False])
def test_both_ways_marking_follows_the_rewatch_setting(rewatch):
    """Both ways skips plays from before a rewatch, and unwatches, only with rewatch sync on."""
    run = _mark(rewatch_aware_sync=rewatch)
    assert run.mark.call_args.kwargs["apply_rewatch_filter"] is rewatch
    assert run.unwatch.called is rewatch
    run.mirror.assert_not_called()


def test_rewatch_unwatching_waits_for_kodi_plays_to_reach_trakt():
    """Nothing is unwatched while episode scrobbling is off or a failed stop waits for retry."""
    assert _mark(scrobble_episode=False).written == []
    assert _mark(queued=1).written == []


def test_trakt_to_kodi_mirrors_with_rewatches_and_confirms_its_unwatches():
    """Trakt to Kodi mirrors Trakt, following rewatches, and sends its unwatches for confirming."""
    run = _mark(direction=TRAKT_TO_KODI)
    run.mark.assert_not_called()
    assert run.mirror.call_args.args[4] is True
    run.sync.confirm_removals.assert_called_once_with([UNWATCH], 32264)
    assert run.written == [UNWATCH]


def test_mirror_unwatching_waits_for_failed_scrobbles():
    """Trakt to Kodi unwatches nothing while a failed stop waits for retry."""
    assert _mark(direction=TRAKT_TO_KODI, queued=1).written == []


@pytest.mark.parametrize("direction", [0, KODI_TO_TRAKT])
def test_watched_marking_skips_without_trakt_to_kodi(direction):
    """Off and Kodi to Trakt leave Kodi's watched status alone."""
    run = _mark(direction=direction)
    run.mark.assert_not_called()
    run.mirror.assert_not_called()
