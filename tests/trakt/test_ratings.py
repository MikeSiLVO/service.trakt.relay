from typing import cast
from unittest import mock

from lib.trakt.api import TraktAPI
from lib.trakt.ratings import NO_SEASON, RatingsCache, rating_changes

MOVIE_8 = {"type": "movie", "rating": 8, "rated_at": "t1", "movie": {"ids": {"trakt": 10}}}
SEASON_6 = {
    "type": "season", "rating": 6, "rated_at": "t1",
    "show": {"ids": {"trakt": 20}}, "season": {"number": 2},
}


def _api(tmp_path, stamps, fetched=None):
    """A TraktAPI on a fresh cache, with Trakt's change times and ratings list mocked."""
    api = TraktAPI.__new__(TraktAPI)
    api.ratings = RatingsCache(str(tmp_path / "relay.db"))
    api.get_last_activities = mock.Mock(
        return_value={kind: {"rated_at": stamp} for kind, stamp in stamps.items()}
    )
    api._get_all_ratings = mock.Mock(return_value=fetched)
    api._post = mock.Mock(return_value={"added": {}})
    return api


def _post_moves_stamp(api, kind, stamp):
    """Make Trakt's change time for the kind move when a post lands, as a real write does."""
    def post(path, items):
        cast(mock.Mock, api.get_last_activities).return_value = {kind: {"rated_at": stamp}}
        return {"added": {}}

    api._post = mock.Mock(side_effect=post)


def _fetches(api):
    """How many times the ratings list was fetched."""
    return cast(mock.Mock, api._get_all_ratings).call_count


def test_cache_keeps_movies_and_seasons_by_trakt_id(tmp_path):
    """Fetched ratings are kept by Trakt id, seasons under their show and number."""
    cache = RatingsCache(str(tmp_path / "relay.db"))
    cache.replace("movies", [MOVIE_8], "t1")
    cache.replace("seasons", [SEASON_6], "t1")
    assert cache.get("movies", 10) == {"rating": 8, "rated_at": "t1"}
    assert cache.get("seasons", 20, 2)["rating"] == 6
    assert cache.get("movies", 11) == {}
    assert cache.stamp("movies") == "t1" and cache.stamp("shows") is None


def test_lookup_refetches_only_when_trakt_reports_a_change(tmp_path):
    """A lookup fetches on first use and again only after Trakt's change time moves."""
    api = _api(tmp_path, {"movies": "t1"}, fetched=[MOVIE_8])
    assert api.get_user_rating("movie", "10")["rating"] == 8
    assert api.get_user_rating("movie", 10)["rating"] == 8
    assert _fetches(api) == 1
    cast(mock.Mock, api.get_last_activities).return_value = {"movies": {"rated_at": "t2"}}
    api.get_user_rating("movie", 10)
    assert _fetches(api) == 2


def test_lookup_keeps_the_cache_when_trakt_cannot_be_read(tmp_path):
    """Unreadable change times or a failed fetch leave the cached ratings in use."""
    api = _api(tmp_path, {"movies": "t1"}, fetched=[MOVIE_8])
    api.get_user_rating("movie", 10)
    cast(mock.Mock, api.get_last_activities).return_value = None
    assert api.get_user_rating("movie", 10)["rating"] == 8
    cast(mock.Mock, api.get_last_activities).return_value = {"movies": {"rated_at": "t2"}}
    cast(mock.Mock, api._get_all_ratings).return_value = None
    assert api.get_user_rating("movie", 10)["rating"] == 8
    assert _fetches(api) == 2


def test_own_rating_is_cached_and_restamped(tmp_path):
    """A rating Relay sends is cached and restamped, with no refetch on the next lookup."""
    api = _api(tmp_path, {"movies": "t1"}, fetched=[])
    api.get_user_rating("movie", 10)
    _post_moves_stamp(api, "movies", "t2")
    api.add_ratings({"movies": [{"ids": {"trakt": 10}, "rating": 9}]})
    assert api.get_user_rating("movie", 10)["rating"] == 9
    assert _fetches(api) == 1
    api.remove_ratings({"movies": [{"ids": {"trakt": 10}, "rating": 0}]})
    assert api.get_user_rating("movie", 10) == {}


def test_rating_without_trakt_id_keeps_the_old_stamp(tmp_path):
    """A rating without a Trakt id leaves its kind's stamp where it was."""
    api = _api(tmp_path, {"movies": "t1"}, fetched=[])
    api.get_user_rating("movie", 10)
    _post_moves_stamp(api, "movies", "t2")
    api.add_ratings({"movies": [{"ids": {"imdb": "tt1"}, "rating": 9}]})
    assert api.ratings.stamp("movies") == "t1"


def test_failed_post_changes_nothing(tmp_path):
    """A rating Trakt refuses leaves the cache as it was."""
    api = _api(tmp_path, {"movies": "t1"}, fetched=[MOVIE_8])
    cast(mock.Mock, api._post).return_value = None
    assert api.add_ratings({"movies": [{"ids": {"trakt": 10}, "rating": 3}]}) is None
    assert api.ratings.get("movies", 10)["rating"] == 8


def test_payload_splits_into_keyed_changes_and_unkeyed_kinds():
    """Shows, seasons and episodes with Trakt ids become changes; nested episodes can't."""
    changes, unkeyed = rating_changes({
        "shows": [
            {"ids": {"trakt": 20}, "rating": 7},
            {"ids": {"trakt": 21}, "seasons": [{"number": 1, "rating": 5}]},
            {"ids": {"trakt": 22}, "seasons": [{"number": 1, "episodes": [{"number": 1}]}]},
        ],
        "episodes": [{"ids": {"trakt": 30}, "rating": 4}],
    })
    assert changes == [
        ("episodes", 30, NO_SEASON, 4), ("shows", 20, NO_SEASON, 7), ("seasons", 21, 1, 5)
    ]
    assert unkeyed == {"episodes"}
