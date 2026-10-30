import threading
import urllib.error
from typing import cast
from email.message import Message
from unittest import mock

import pytest

from lib.trakt.api import TraktAPI
from lib.trakt.client import TraktClient
from lib.sync.compare import compare_movies


def test_movie_merge_keeps_both_payloads():
    """Merging two sync payloads for one movie keeps the fields of both."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_movie(
        store,
        {
            "collected_at": "2024-01-01",
            "movie": {"title": "Movie", "ids": {"trakt": 1}},
        },
        ("collected_at",),
    )
    api._merge_movie(
        store,
        {"plays": 2, "movie": {"title": "Movie", "ids": {"trakt": 1}}},
        ("plays",),
    )

    movie = store[1]
    assert movie.to_dict()["collected_at"] == "2024-01-01"
    assert movie.to_dict()["plays"] == 2


def test_collected_movie_merge_fills_the_defaults():
    """A collected-only movie gets collected set and the other fields at their defaults."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_movie(
        store,
        {
            "collected_at": "2024-01-01",
            "movie": {"title": "Movie", "year": 2024, "ids": {"trakt": 1}},
        },
        ("collected_at",),
    )

    movie = store[1].to_dict()
    assert movie["collected"] == 1
    assert movie["watched"] == 0
    assert movie["plays"] == 0
    assert movie["in_watchlist"] == 0
    assert movie["progress"] is None
    assert movie["last_watched_at"] is None
    assert movie["paused_at"] is None


def test_watched_movie_merge_fills_the_defaults():
    """A watched-only movie gets watched set and the other fields at their defaults."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_movie(
        store,
        {"plays": 2, "movie": {"title": "Movie", "ids": {"trakt": 1}}},
        ("plays",),
    )

    movie = store[1].to_dict()
    assert movie["watched"] == 1
    assert movie["collected"] == 0
    assert movie["plays"] == 2
    assert movie["in_watchlist"] == 0
    assert movie["progress"] is None
    assert movie["collected_at"] is None
    assert movie["paused_at"] is None


def test_collected_only_trakt_movie_does_not_break_watched_compare():
    """A movie Trakt only has collected still counts as unwatched in the watched compare."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_movie(
        store,
        {
            "collected_at": "2024-01-01",
            "movie": {"title": "Movie", "year": 2024, "ids": {"trakt": 1}},
        },
        ("collected_at",),
    )

    kodi_movies = [
        {
            "title": "Movie",
            "year": 2024,
            "ids": {"trakt": 1},
            "watched": 1,
            "plays": 1,
            "movieid": 10,
        }
    ]
    trakt_movies = [movie.to_dict() for movie in store.values()]

    assert (
        compare_movies(kodi_movies, trakt_movies, True, watched=True)
        == kodi_movies
    )


def test_merge_show_payloads_merges_nested_episodes():
    """Merging two show payloads merges the fields of the same episode."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_show(
        store,
        {
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "seasons": [
                {"number": 1, "episodes": [{"number": 2, "collected_at": "now"}]}
            ],
        },
        (),
    )
    api._merge_show(
        store,
        {
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "seasons": [{"number": 1, "episodes": [{"number": 2, "plays": 3}]}],
        },
        (),
    )

    episode = store[1].to_dict()["seasons"][0]["episodes"][0]
    assert episode["collected_at"] == "now"
    assert episode["plays"] == 3


def test_collected_episode_merge_fills_the_defaults():
    """A collected-only episode gets collected set and the other fields at their defaults."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_show(
        store,
        {
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "seasons": [
                {
                    "number": 1,
                    "episodes": [{"number": 2, "collected_at": "now"}],
                }
            ],
        },
        ("collected_at",),
    )

    episode = store[1].to_dict()["seasons"][0]["episodes"][0]
    assert episode["collected"] == 1
    assert episode["watched"] == 0
    assert episode["plays"] == 0
    assert episode["in_watchlist"] == 0
    assert episode["progress"] is None
    assert episode["last_watched_at"] is None
    assert episode["paused_at"] is None


def test_watched_episode_merge_fills_the_defaults():
    """A watched-only episode gets watched set and the other fields at their defaults."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_show(
        store,
        {
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "seasons": [{"number": 1, "episodes": [{"number": 2, "plays": 3}]}],
        },
        ("plays",),
    )

    episode = store[1].to_dict()["seasons"][0]["episodes"][0]
    assert episode["watched"] == 1
    assert episode["collected"] == 0
    assert episode["plays"] == 3
    assert episode["in_watchlist"] == 0
    assert episode["progress"] is None
    assert episode["collected_at"] is None
    assert episode["paused_at"] is None


def test_merge_show_preserves_episode_flags_across_collection_and_watched_payloads():
    """An episode both collected and watched keeps both flags after the two merges."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_show(
        store,
        {
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "seasons": [
                {
                    "number": 1,
                    "episodes": [{"number": 2, "collected_at": "now"}],
                }
            ],
        },
        ("collected_at",),
    )
    api._merge_show(
        store,
        {
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "seasons": [{"number": 1, "episodes": [{"number": 2, "plays": 3}]}],
        },
        ("plays",),
    )

    episode = store[1].to_dict()["seasons"][0]["episodes"][0]
    assert episode["collected"] == 1
    assert episode["watched"] == 1
    assert episode["plays"] == 3


def test_merge_episode_rating_payload_attaches_rating_to_episode():
    """An episode rating lands on the episode, not on its show."""
    api = TraktAPI.__new__(TraktAPI)
    store = {}

    api._merge_show(
        store,
        {
            "rated_at": "2024-01-01",
            "rating": 8,
            "show": {"title": "Show", "ids": {"trakt": 1}},
            "episode": {"season": 1, "number": 2, "title": "Episode"},
        },
        ("rated_at", "rating"),
    )

    show = store[1].to_dict()
    episode = show["seasons"][0]["episodes"][0]
    assert "rating" not in show
    assert episode["rating"] == 8
    assert episode["rated_at"] == "2024-01-01"


def test_show_summary_without_seasons_gets_an_empty_list():
    """A show summary without seasons gets an empty season list."""
    api = TraktAPI.__new__(TraktAPI)
    api._get = mock.Mock(return_value={"title": "Show", "ids": {"trakt": 1}})

    show = api.get_show_summary("1").to_dict()

    assert show["seasons"] == []


def test_wrap_search_episode_result_exposes_show_and_pk():
    """An episode search hit links its show and carries its (season, episode) pair."""
    api = TraktAPI.__new__(TraktAPI)

    result = api._wrap_search_results(
        [
            {
                "show": {"title": "Show", "ids": {"trakt": 1}},
                "episode": {"season": 4, "number": 5, "title": "Episode"},
            }
        ]
    )

    assert result is not None
    assert result[0].show.title == "Show"
    assert result[0].pk == (4, 5)
    assert result[0].title == "Episode"


def test_text_query_searches_episodes():
    """A text search for episodes is sent and its hit links the episode to its show."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = TraktClient("id", "ua")
    hit = {"type": "episode", "show": {"title": "Breaking Bad"},
           "episode": {"season": 5, "number": 14, "title": "Ozymandias"}}
    api._get_all_pages = mock.Mock(return_value=[hit])

    result = api.search_by_text("Ozymandias", "episode", None)

    api._get_all_pages.assert_called_once_with("/search/episode?query=Ozymandias", timeout=90)
    assert result is not None
    assert result[0].show.title == "Breaking Bad"
    assert result[0].pk == (5, 14)


def test_text_query_skips_type_trakt_cannot_search():
    """A text search for a type outside Trakt's search types returns None without a request."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = TraktClient("id", "ua")
    api._get_all_pages = mock.Mock(return_value=[])

    assert api.search_by_text("Season 5", "season", None) is None
    api._get_all_pages.assert_not_called()


def test_get_all_pages_follows_pagination_headers():
    """Paging follows the page count header and joins every page's items."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = TraktClient("id", "ua")
    api._get = mock.Mock(
        side_effect=[
            ([{"title": "One"}], {"X-Pagination-Page-Count": "2"}),
            ([{"title": "Two"}], {"X-Pagination-Page-Count": "2"}),
        ]
    )

    result = api._get_all_pages("/sync/collection/movies", authorized=True)

    assert result == [{"title": "One"}, {"title": "Two"}]
    api._get.assert_has_calls(
        [
            mock.call(
                "/sync/collection/movies?page=1&limit=100",
                authorized=True,
                timeout=90,
                include_headers=True,
            ),
            mock.call(
                "/sync/collection/movies?page=2&limit=100",
                authorized=True,
                timeout=90,
                include_headers=True,
            ),
        ]
    )


def test_get_all_pages_merges_extra_params_into_query():
    """Extra params go into each page's query beside page and limit."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = TraktClient("id", "ua")
    api._get = mock.Mock(return_value=([{"title": "One"}], {}))

    api._get_all_pages(
        "/sync/watched/shows", authorized=True, params={"extended": "progress"}
    )

    api._get.assert_called_once_with(
        "/sync/watched/shows?page=1&limit=100&extended=progress",
        authorized=True,
        timeout=90,
        include_headers=True,
    )


def _rating_pages(*pages):
    """A TraktAPI whose ratings list answers each page in turn, None standing for a failure."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = TraktClient("id", "ua")
    replies = {
        "/users/me/ratings/movies?page=%d&limit=250" % number: (
            None if items is None else (items, {"X-Pagination-Page-Count": str(len(pages))})
        )
        for number, items in enumerate(pages, 1)
    }
    api._get = mock.Mock(side_effect=lambda path, **kwargs: replies[path])
    return api


def test_ratings_come_from_the_unfiltered_list_in_page_order():
    """Ratings come from the unfiltered list, every page merged in page order."""
    api = _rating_pages([1, 2], [3], [4, 5])
    assert api._get_all_ratings("movies") == [1, 2, 3, 4, 5]


def test_ratings_failing_on_any_page_give_none():
    """A failed page gives None, never a partial list."""
    assert _rating_pages([1], None, [3])._get_all_ratings("movies") is None
    assert _rating_pages(None)._get_all_ratings("movies") is None


def test_movie_playback_progress_uses_pagination():
    """Paused movies are paged and carry their progress."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(
        return_value=[
            {"progress": 50, "movie": {"title": "Movie", "ids": {"trakt": 1}}}
        ]
    )

    result = api.get_movie_playback_progress()

    api._get_all_pages.assert_called_once_with(
        "/sync/playback/movies", authorized=True, timeout=90
    )
    assert result is not None
    assert result[0].to_dict()["progress"] == 50


def test_episode_playback_progress_uses_pagination():
    """Paused episodes are paged and carry their progress under their show."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(
        return_value=[
            {
                "progress": 50,
                "show": {"title": "Show", "ids": {"trakt": 1}},
                "episode": {"season": 1, "number": 2, "title": "Episode"},
            }
        ]
    )

    result = api.get_episode_playback_progress()

    api._get_all_pages.assert_called_once_with(
        "/sync/playback/episodes", authorized=True, timeout=90
    )
    assert result is not None
    episode = result[0].to_dict()["seasons"][0]["episodes"][0]
    assert episode["progress"] == 50


def test_get_watched_shows_pages_with_progress():
    """Watched shows are paged with extended=progress, giving per-episode plays."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(
        return_value=[
            {
                "plays": 3,
                "last_watched_at": "2026-01-01T00:00:00.000Z",
                "show": {"title": "Show", "ids": {"trakt": 1}},
                "seasons": [{"number": 1, "episodes": [{"number": 1, "plays": 1}]}],
            }
        ]
    )

    result = api.get_watched_shows({})

    api._get_all_pages.assert_called_once_with(
        "/sync/watched/shows",
        authorized=True,
        timeout=90,
        params={"extended": "progress"},
    )
    assert result is not None
    episode = result[1].to_dict()["seasons"][0]["episodes"][0]
    assert episode["plays"] == 1


def test_get_collected_shows_pages():
    """Collected shows are paged from /sync/collection/shows with the user's token."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(return_value=[])

    api.get_collected_shows({})

    api._get_all_pages.assert_called_once_with(
        "/sync/collection/shows", authorized=True, timeout=90
    )


def test_get_watched_movies_pages():
    """Watched movies are paged from /sync/watched/movies with the user's token."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(return_value=[])

    api.get_watched_movies({})

    api._get_all_pages.assert_called_once_with(
        "/sync/watched/movies", authorized=True, timeout=90
    )


def test_add_to_history_posts_with_user_token():
    """History adds are posted to /sync/history with the user's token."""
    api = TraktAPI.__new__(TraktAPI)
    api._request = mock.Mock(return_value={})

    api.add_to_history({"movies": []})

    api._request.assert_called_once_with(
        "POST", "/sync/history", body={"movies": []}, authorized=True, timeout=30
    )


def test_start_rewatch_posts_a_progress_reset():
    """A rewatch is started by posting an empty body to the show's progress reset."""
    api = TraktAPI.__new__(TraktAPI)
    api._request = mock.Mock(return_value={})

    api.start_rewatch("432")

    api._request.assert_called_once_with(
        "POST", "/shows/432/progress/watched/reset", body={}, authorized=True, timeout=30
    )


def _expired_api():
    """A TraktAPI holding an expired token, with a mocked client and token save."""
    api = TraktAPI.__new__(TraktAPI)
    api.authorization = {"access_token": "expired", "refresh_token": "refresh"}
    api.client = mock.Mock()
    api.client.client_id = "id"
    api._save_token = mock.Mock(side_effect=lambda token: setattr(api, "authorization", token))
    return api


def _unauthorized():
    """A 401 as the Trakt client raises it when the token can be refreshed."""
    return urllib.error.HTTPError(
        "https://api.trakt.tv/sync/history", 401, "Unauthorized", Message(), None
    )


def test_request_refreshes_token_and_resends():
    """A 401 refreshes the token once and resends the call with the new one."""
    api = _expired_api()
    fresh = {"access_token": "new", "refresh_token": "next"}
    api.client.request = mock.Mock(side_effect=[_unauthorized(), (fresh, None), {"added": 1}])

    result = api._request("POST", "/sync/history", body={"movies": []}, authorized=True)

    assert result == {"added": 1}
    cast(mock.Mock, api._save_token).assert_called_once_with(fresh)
    refresh, resend = api.client.request.call_args_list[1:]
    assert "client_secret" not in refresh.args[2]
    assert resend.kwargs["authorization"] == fresh


def test_refresh_skipped_when_another_call_already_refreshed():
    """Refresh is skipped when another thread already replaced the token."""
    api = _expired_api()
    stale = {"access_token": "expired", "refresh_token": "refresh"}
    api.authorization = {"access_token": "new", "refresh_token": "next"}

    assert api._refresh_token(stale) is True
    cast(mock.Mock, api.client.request).assert_not_called()


def test_request_returns_none_when_resend_still_unauthorized():
    """A resend that still gets a 401 returns None instead of raising."""
    api = _expired_api()
    fresh = {"access_token": "new", "refresh_token": "next"}
    api.client.request = mock.Mock(side_effect=[_unauthorized(), (fresh, None), _unauthorized()])

    assert api._request("GET", "/users/settings", authorized=True) is None


def test_refused_refresh_drops_the_token():
    """A refresh Trakt refuses drops the token and the stored username."""
    api = _expired_api()
    api.client.request = mock.Mock(side_effect=[_unauthorized(), (None, 400)])

    with mock.patch("lib.trakt.api.set_setting") as set_setting:
        assert api._request("POST", "/scrobble/stop", authorized=True) is None

    assert not api.connected
    set_setting.assert_has_calls([mock.call("authorization", ""), mock.call("user", "")])


def test_failed_refresh_keeps_the_token():
    """A refresh that fails without Trakt's refusal keeps the token for the next try."""
    api = _expired_api()
    api.client.request = mock.Mock(side_effect=[_unauthorized(), (None, None)])

    with mock.patch("lib.trakt.api.set_setting") as set_setting:
        assert api._request("POST", "/scrobble/stop", authorized=True) is None

    assert api.connected
    set_setting.assert_not_called()


def test_refresh_after_another_thread_dropped_the_token_fails():
    """A waiting refresh fails without a request once another thread dropped the token."""
    api = _expired_api()
    stale = {"access_token": "expired", "refresh_token": "refresh"}
    api.authorization = None

    assert api._refresh_token(stale) is False
    cast(mock.Mock, api.client.request).assert_not_called()


@pytest.mark.parametrize("reply, result", [
    (({"action": "scrobble"}, None), {"action": "scrobble"}),
    ((None, 409), {"duplicate": True}),
    ((None, 500), None),
])
def test_scrobble_reads_trakts_409_as_a_duplicate(reply, result):
    """A scrobble returns Trakt's reply, a duplicate on a 409, or None on any other failure."""
    api = TraktAPI.__new__(TraktAPI)
    api.authorization = {"access_token": "token", "refresh_token": "refresh"}
    api.client = mock.Mock(**{"request.return_value": reply})

    assert api.scrobble_movie({"ids": {"trakt": 1}}, 95.0, "stop") == result


@pytest.mark.parametrize("settings, result", [
    ({"limits": {"watchlist": {"item_count": 100}}}, {"limit": 100}),
    (None, None),
])
def test_watchlist_add_over_the_limit_reads_it(settings, result):
    """A watchlist add Trakt refuses with 420 returns the account's limit, or None if unreadable."""
    api = TraktAPI.__new__(TraktAPI)
    api.authorization = {"access_token": "token", "refresh_token": "refresh"}
    api.client = mock.Mock(**{"request.side_effect": [(None, 420), settings]})

    assert api.add_to_watchlist({"movies": [{"ids": {"tmdb": 949}}]}) == result


def test_paged_get_stops_once_kodi_exits():
    """A paged GET returns None without another request once Kodi starts exiting."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = mock.Mock()
    api.client.monitor.abortRequested.return_value = True

    assert api._get_all_pages("/sync/watched/shows", authorized=True) is None
    api.client.request.assert_not_called()


def test_locked_account_is_notified_once():
    """Trakt's 423 for a locked account notifies once a session, and other errors never."""
    api = TraktAPI.__new__(TraktAPI)
    api._locked = threading.Event()
    with mock.patch("lib.trakt.api.notify") as notify, \
            mock.patch("lib.trakt.api.get_string", side_effect=str):
        for code in (500, 423, 423, 404):
            api._check_error(code)
    notify.assert_called_once()
    assert notify.call_args.args[1] == "32227"


def _polling_api(*replies):
    """A TraktAPI whose mocked client answers each device-token request with the next reply."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = mock.Mock()
    api.client.client_id = "id"
    api.client.request = mock.Mock(side_effect=list(replies))
    api._save_token = mock.Mock()
    api.update_user = mock.Mock()
    return api


def _running():
    """A stand-in for the stop event that stays unset and returns at once."""
    stop = mock.Mock()
    stop.is_set.return_value = False
    stop.wait.return_value = False
    return stop


CODE = {"device_code": "device", "expires_in": 30, "interval": 5}
TOKEN = {"access_token": "token", "refresh_token": "refresh"}


def test_token_wait_continues_on_pending_then_saves_the_token():
    """The token wait continues through pending replies, then saves the token and the username."""
    api = _polling_api((None, 400), (TOKEN, None))
    stop = _running()

    assert api.wait_for_token(CODE, stop) is True

    stop.wait.assert_called_once_with(5)
    cast(mock.Mock, api._save_token).assert_called_once_with(TOKEN)
    cast(mock.Mock, api.update_user).assert_called_once_with()
    poll = cast(mock.Mock, api.client.request).call_args_list[0]
    assert poll.args[2] == {"code": "device", "client_id": "id"}


def test_token_wait_ends_on_denied_status():
    """The token wait ends unapproved when the user denies the code."""
    api = _polling_api((None, 418))

    assert api.wait_for_token(CODE, _running()) is False

    cast(mock.Mock, api._save_token).assert_not_called()


def test_token_wait_slows_down_on_rate_limit_status():
    """The token wait slows down on a 429, adding a second to its interval."""
    api = _polling_api((None, 429), (TOKEN, None))
    stop = _running()

    assert api.wait_for_token(CODE, stop) is True

    stop.wait.assert_called_once_with(6)


def test_token_wait_ends_when_stopped():
    """The token wait ends at once when stopped, with no further request."""
    stop = threading.Event()

    def pending_then_stopped(*args, **kwargs):
        stop.set()
        return None, 400

    api = _polling_api()
    cast(mock.Mock, api.client.request).side_effect = pending_then_stopped

    assert api.wait_for_token(CODE, stop) is False
    assert cast(mock.Mock, api.client.request).call_count == 1


def test_failed_page_gives_none_not_a_partial_list():
    """A page that fails midway gives None, never the pages before it."""
    api = TraktAPI.__new__(TraktAPI)
    api.client = TraktClient("id", "ua")
    first = ([{"movie": {"ids": {"trakt": 1}}}], {"X-Pagination-Page-Count": "2"})
    api._get = mock.Mock(side_effect=[first, None])

    assert api.get_watched_movies({}) is None


def test_history_window_reads_every_page():
    """A history window GETs every page of the item type's plays between the two times."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(return_value=[])
    assert api.get_history("episode", "a", "b") == []
    api._get_all_pages.assert_called_once_with(
        "/sync/history/episodes", authorized=True, params={"start_at": "a", "end_at": "b"})


def test_id_search_asks_for_one_type():
    """An id search asks Trakt for one search type only."""
    api = TraktAPI.__new__(TraktAPI)
    api._get_all_pages = mock.Mock(return_value=[])
    api.search_by_id("550", "tmdb", "movie")
    api._get_all_pages.assert_called_once_with(
        "/search/tmdb/550", timeout=90, params={"type": "movie"})
