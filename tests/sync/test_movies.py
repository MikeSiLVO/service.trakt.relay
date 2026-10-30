from unittest import mock

import pytest

from lib.kodi.settings import BOTH_WAYS, KODI_TO_TRAKT, TRAKT_TO_KODI
from lib.sync import movies as movies_module
from lib.sync.movies import MovieSync
from lib.trakt.objects import TraktObject
from tests.helpers import HEAT, Text, settings


def test_movie_half_stops_before_trakt_when_kodi_has_no_movies():
    """An empty Kodi movie library ends the movie half before any Trakt call."""
    sync = mock.Mock()
    movie_sync = MovieSync(sync)
    with mock.patch.object(movies_module, "request", return_value=None):
        assert movie_sync.run() is False
    sync.api.get_collected_movies.assert_not_called()


def test_failed_trakt_load_stops_the_movie_half_before_any_write():
    """A watched list that fails to load ends the movie half before anything is sent to Trakt."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    sync.api.get_collected_movies.return_value = {}
    sync.api.get_watched_movies.return_value = None
    kodi = [{"title": "Heat", "year": 1995, "ids": {"imdb": "tt0113277"}, "watched": 1}]
    with mock.patch.object(MovieSync, "_load_kodi", return_value=kodi), \
            mock.patch.object(movies_module, "get_setting_bool", return_value=True):
        assert MovieSync(sync).run() is False
    sync.api.add_to_history.assert_not_called()
    sync.api.add_to_collection.assert_not_called()


def test_cancel_between_trakt_loads_stops_the_rest():
    """A cancel or Kodi exit after the collection loads skips the watched and rated loads."""
    sync = mock.Mock()
    sync.is_canceled.return_value = True
    sync.api.get_collected_movies.return_value = {}
    with mock.patch.object(movies_module, "get_setting_bool", return_value=True), \
            mock.patch.object(movies_module, "get_string", return_value=Text()):
        assert MovieSync(sync)._load_trakt() is None
    sync.api.get_watched_movies.assert_not_called()
    sync.api.get_rated_movies.assert_not_called()


@pytest.mark.parametrize("direction, sent", [(0, False), (TRAKT_TO_KODI, False),
                                             (KODI_TO_TRAKT, True), (BOTH_WAYS, True)])
def test_history_step_sends_movies_watched_in_kodi(direction, sent):
    """Movies watched in Kodi but not on Trakt go to its history when watched goes to Trakt."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    with settings(sync_watched_movies=direction), \
            mock.patch.object(movies_module, "get_string", return_value=Text()):
        MovieSync(sync)._add_to_trakt_history([dict(HEAT)], [], 59, 69)
    assert sync.send.called is sent
    if sent:
        write, items, kind = sync.send.call_args.args[:3]
        assert (write, [item["title"] for item in items], kind) == (
            sync.api.add_to_history, ["Heat"], "movies")


def _ratings(direction, trakt, kodi):
    """Sync movie ratings as set, removals passed through; return Trakt sends, Kodi writes."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    sync.confirm_removals.side_effect = lambda removals, message_id: removals
    with settings(sync_ratings=direction), \
            mock.patch.object(movies_module, "get_string", return_value=Text()), \
            mock.patch.object(movies_module, "batch_request") as batch:
        MovieSync(sync)._sync_ratings(trakt, kodi, 92, 99)
    sent = [(m["title"], m["rating"]) for c in sync.send.call_args_list for m in c.args[1]]
    written = [(u["movieid"], u["userrating"]) for c in batch.call_args_list for u in c.args[1]]
    return sent, written


def test_ratings_reach_trakt_with_no_movies_there():
    """Kodi movie ratings go to Trakt even when the account has no movies there yet."""
    assert _ratings(KODI_TO_TRAKT, [], [dict(HEAT, rating=8)]) == ([("Heat", 8)], [])


def test_both_ways_keeps_trakts_rating_where_they_differ():
    """Both ways sends Trakt only a rating it lacks, and a different one comes back from Trakt."""
    alien = {"title": "Alien", "year": 1979, "ids": {"imdb": "tt0078748"}, "movieid": 8,
             "collected": 1}
    trakt = [dict(HEAT, rating=6)]
    kodi = [dict(HEAT, rating=8), dict(alien, rating=9)]
    assert _ratings(BOTH_WAYS, trakt, kodi) == ([("Alien", 9)], [(7, 6)])


def test_trakt_to_kodi_clears_kodi_ratings_trakt_lacks_only_on_items_with_ids():
    """Mirroring clears a Kodi rating Trakt lacks, but never one on an item matched by title."""
    untitled = {"title": "Blind", "year": 2017, "ids": {}, "movieid": 9, "collected": 1}
    kodi = [dict(HEAT, rating=8), dict(untitled, rating=5)]
    assert _ratings(TRAKT_TO_KODI, [], kodi) == ([], [(7, 0)])


def _mirror(trakt, kodi, queued=0):
    """Mark Kodi movies watched by mirroring Trakt, removals passed through; return the writes."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    sync.confirm_removals.side_effect = lambda removals, message_id: removals
    with settings(sync_watched_movies=TRAKT_TO_KODI), \
            mock.patch.object(movies_module, "get_string", return_value=Text()), \
            mock.patch.object(movies_module, "ScrobbleQueue", return_value=[None] * queued), \
            mock.patch.object(movies_module, "batch_request") as batch:
        MovieSync(sync)._mark_watched_in_kodi(trakt, kodi, None, 70, 80)
    return [u for c in batch.call_args_list for u in c.args[1]]


def test_trakt_to_kodi_unwatches_a_movie_trakt_never_played():
    """Mirroring unwatches a Kodi movie with no plays on Trakt, unless a failed stop waits."""
    assert _mirror([], [dict(HEAT, plays=1)]) == [
        {"movieid": 7, "playcount": 0, "lastplayed": ""}]
    assert _mirror([], [dict(HEAT, plays=1)], queued=1) == []


def test_progress_skips_a_movie_whose_runtime_cannot_be_found():
    """A paused movie with no runtime in Kodi or on Trakt is skipped without raising."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    sync.api.get_movie_summary.return_value = TraktObject({})
    paused = {"movies": [dict(HEAT, progress=40, ids={"imdb": "tt0113277", "trakt": 1})]}
    kodi = [dict(HEAT, runtime=0)]
    with mock.patch.object(movies_module, "get_setting_bool", return_value=True), \
            mock.patch.object(movies_module, "get_string", return_value=Text()), \
            mock.patch.object(movies_module, "get_play_states", return_value=[None]), \
            mock.patch.object(movies_module, "batch_request") as batch:
        MovieSync(sync)._set_kodi_progress(paused, kodi, 81, 91)
    batch.assert_not_called()


def test_watched_step_counts_each_chunk_of_movies():
    """Marking movies watched counts the movies each chunk brings, ending at the total."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    updates = [{"movieid": n, "playcount": 1} for n in range(120)]
    with settings(sync_watched_movies=BOTH_WAYS), \
            mock.patch.object(movies_module, "get_string", return_value=Text()), \
            mock.patch.object(movies_module, "movies_to_mark_watched", return_value=updates), \
            mock.patch.object(movies_module, "batch_request"):
        MovieSync(sync)._mark_watched_in_kodi([], [], None, 70, 80)
    counted = [c.args[:2] for c in sync.update_count.call_args_list]
    assert counted == [(50, 120), (100, 120), (120, 120)]
