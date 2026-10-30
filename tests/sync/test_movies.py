from unittest import mock

from lib.sync import movies as movies_module
from lib.sync.movies import MovieSync
from lib.trakt.objects import TraktObject
from tests.helpers import HEAT, Text


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
    sync.api.get_collected_movies.return_value = {}
    sync.api.get_watched_movies.return_value = None
    kodi = [{"title": "Heat", "year": 1995, "ids": {"imdb": "tt0113277"}, "watched": 1}]
    with mock.patch.object(MovieSync, "_load_kodi", return_value=kodi), \
            mock.patch.object(movies_module, "get_setting_bool", return_value=True):
        assert MovieSync(sync).run() is False
    sync.api.add_to_history.assert_not_called()
    sync.api.add_to_collection.assert_not_called()


def test_history_step_sends_movies_watched_in_kodi():
    """Movies watched in Kodi but not on Trakt are sent as history adds."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    with mock.patch.object(movies_module, "get_setting_bool", return_value=True), \
            mock.patch.object(movies_module, "get_string", return_value=Text()):
        MovieSync(sync)._add_to_trakt_history([dict(HEAT)], [], 59, 69)
    write, items, kind = sync.send.call_args.args[:3]
    assert (write, [item["title"] for item in items], kind) == (
        sync.api.add_to_history, ["Heat"], "movies")


def test_ratings_reach_trakt_with_no_movies_there():
    """Kodi movie ratings go to Trakt even when the account has no movies there yet."""
    sync = mock.Mock()
    sync.is_canceled.return_value = False
    with mock.patch.object(movies_module, "get_setting_bool",
                           side_effect=lambda name: name == "sync_ratings_to_trakt"), \
            mock.patch.object(movies_module, "get_string", return_value=Text()):
        MovieSync(sync)._sync_ratings([], [dict(HEAT, rating=8)], 92, 99)
    assert sync.send.call_args.args[0] is sync.api.add_ratings


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
    with mock.patch.object(movies_module, "get_setting_bool", return_value=True), \
            mock.patch.object(movies_module, "get_string", return_value=Text()), \
            mock.patch.object(movies_module, "movies_to_mark_watched", return_value=updates), \
            mock.patch.object(movies_module, "batch_request"):
        MovieSync(sync)._mark_watched_in_kodi([], [], 70, 80)
    counted = [c.args[:2] for c in sync.update_count.call_args_list]
    assert counted == [(50, 120), (100, 120), (120, 120)]
