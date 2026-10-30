from unittest import mock

import pytest

from lib.service import actions
from lib.trakt.objects import TraktObject


def _rate(media_type, ids):
    """Rate a library item with Trakt and the item lookup mocked; return the api mock."""
    item = {"media_type": media_type, "dbid": 5, "ids": ids, "season": 1, "number": 2}
    hit = mock.Mock(keys=[("trakt", 432)])
    summary = mock.Mock(keys=[("trakt", 432)], **{"to_dict.return_value": {"title": "Fight Club"}})
    api = mock.Mock(**{"search_by_id.return_value": [hit],
                       "get_movie_summary.return_value": summary,
                       "get_show_summary.return_value": summary,
                       "get_episode_summary.return_value": summary})
    with mock.patch.object(actions.shared, "api", api), mock.patch.object(actions, "rate_media"), \
            mock.patch.object(actions, "_library_item", return_value=item):
        actions.rate_manually({"action": "rate", "rating": "7"})
    return api


@pytest.mark.parametrize("media_type, search_type", [
    ("movie", "movie"),
    ("show", "show"),
    ("season", "show"),
    ("episode", "show"),
])
def test_rating_searches_only_the_item_type(media_type, search_type):
    """A rating searches by id among movies for a movie, and among shows otherwise."""
    api = _rate(media_type, {"tmdb": 550})
    api.search_by_id.assert_called_once_with(550, "tmdb", search_type)


def test_rating_by_trakt_id_skips_the_search():
    """A rating whose ids hold the Trakt id looks the item up without searching."""
    api = _rate("movie", {"trakt": 432, "tmdb": 550})
    api.search_by_id.assert_not_called()
    api.get_movie_summary.assert_called_once_with(432)


def test_rating_stops_when_trakt_has_no_summary():
    """A failed summary lookup rates nothing."""
    api = mock.Mock(**{"get_movie_summary.return_value": TraktObject({})})
    item = {"media_type": "movie", "dbid": 5, "ids": {"trakt": 1}}
    with mock.patch.object(actions.shared, "api", api), \
            mock.patch.object(actions, "rate_media") as rate_media, \
            mock.patch.object(actions, "_library_item", return_value=item):
        actions.rate_manually({"action": "rate"})
    rate_media.assert_not_called()


def test_remote_rating_reads_season_and_episode():
    """A rating by remote id takes its season and episode from the script's arguments."""
    hit = mock.Mock(keys=[("trakt", 432)])
    summary = mock.Mock(keys=[("trakt", 99)], **{"to_dict.return_value": {"title": "Pilot"}})
    api = mock.Mock(**{"search_by_id.return_value": [hit],
                       "get_episode_summary.return_value": summary})
    with mock.patch.object(actions.shared, "api", api), \
            mock.patch.object(actions, "rate_media") as rate_media:
        actions.rate_manually({"action": "rate", "media_type": "episode", "remoteid": "81189",
                               "season": "1", "episode": "2", "rating": "8"})
    api.search_by_id.assert_called_once_with("81189", "tvdb", "show")
    api.get_episode_summary.assert_called_once_with(432, 1, 2)
    assert rate_media.call_args.kwargs == {"rating": 8}


def _library(data):
    """Look up a library item with Kodi's season and show details stubbed; return item and mocks."""
    season = mock.Mock(return_value={"tvshowid": 3, "season": 2})
    show = mock.Mock(return_value={"label": "Show", "uniqueid": {"tvdb": "81189"}})
    with mock.patch.object(actions, "get_season_details", season), \
            mock.patch.object(actions, "get_show_details", show):
        return actions._library_item(data), season, show


def test_library_season_carries_its_show_ids():
    """A focused season resolves to its show's ids and title, found by the season's tvshowid."""
    item, _, show = _library({"action": "rate", "dbtype": "season", "dbid": "9"})
    assert item == {"media_type": "season", "dbid": 9, "season": 2, "tvshowid": 3,
                    "ids": {"tvdb": "81189"}, "title": "Show"}
    show.assert_called_once_with(3, ["uniqueid"])


def test_item_without_a_library_id_is_not_looked_up():
    """A focused item with a DBTYPE but no DBID asks Kodi nothing."""
    item, season, show = _library({"action": "rate", "dbtype": "season", "dbid": ""})
    assert item is None
    season.assert_not_called()
    show.assert_not_called()


def _watchlist(item, reply):
    """Add a library item to the watchlist with Trakt replying as given; return api and notify."""
    api = mock.Mock(**{"add_to_watchlist.return_value": reply})
    strings = {32228: "Watchlist limit of %i reached"}
    with mock.patch.object(actions.shared, "api", api), \
            mock.patch.object(actions, "_library_item", return_value=item), \
            mock.patch.object(actions, "get_string",
                              side_effect=lambda i: strings.get(i, str(i))), \
            mock.patch.object(actions, "notify") as notify:
        actions.add_to_watchlist({"action": "addtowatchlist"})
    return api, notify


ADDED = {"added": {"movies": 1, "shows": 1}, "not_found": {"movies": [], "shows": []}}


def test_season_watchlist_adds_the_season_under_its_show():
    """Adding a season to the watchlist sends its number under the show's ids."""
    item = {"media_type": "season", "dbid": 9, "season": 2, "tvshowid": 3,
            "ids": {"tvdb": "81189"}, "title": "Show"}
    api, notify = _watchlist(item, ADDED)
    api.add_to_watchlist.assert_called_once_with(
        {"shows": [{"ids": {"tvdb": "81189"}, "seasons": [{"number": 2}]}]})
    notify.assert_called_once_with("32165", "Show - Season 2")


def test_movie_watchlist_sends_the_library_ids():
    """A movie goes to the watchlist by its library ids, without a Trakt summary lookup."""
    item = {"media_type": "movie", "dbid": 5, "ids": {"tmdb": "4951"},
            "title": "10 Things I Hate About You", "year": 1999}
    api, notify = _watchlist(item, ADDED)
    api.get_movie_summary.assert_not_called()
    api.add_to_watchlist.assert_called_once_with({"movies": [{"ids": {"tmdb": "4951"}}]})
    notify.assert_called_once_with("32165", "10 Things I Hate About You (1999)")


def test_full_watchlist_names_its_limit():
    """A watchlist add refused for the account limit names the limit instead of a plain failure."""
    item = {"media_type": "movie", "dbid": 5, "ids": {"tmdb": "949"}, "title": "Heat",
            "year": 1995}
    _, notify = _watchlist(item, {"limit": 100})
    notify.assert_called_once_with("Watchlist limit of 100 reached", "Heat (1995)")


def test_watchlist_item_trakt_could_not_match_notifies_a_failure():
    """An item Trakt lists as not found is reported as a failed watchlist add."""
    item = {"media_type": "movie", "dbid": 5, "ids": {"tmdb": "4951"}, "title": "X", "year": 1999}
    reply = {"added": {"movies": 0}, "not_found": {"movies": [{"ids": {"tmdb": 4951}}]}}
    _, notify = _watchlist(item, reply)
    notify.assert_called_once_with("32166", "X (1999)")


SHOW = {"media_type": "show", "dbid": 3, "tvshowid": 3, "ids": {"tvdb": "81189"},
        "title": "Breaking Bad"}


def _rewatch(item=SHOW, confirmed=True, reply=None):
    """Start a rewatch with Trakt and Kodi mocked; return api, Kodi batch and notify mocks."""
    api = mock.Mock(**{"search_by_id.return_value": [mock.Mock(keys=[("trakt", 1388)])],
                       "start_rewatch.return_value": reply})
    episodes = {"episodes": [{"episodeid": 7, "playcount": 2}, {"episodeid": 8, "playcount": 0}]}
    with mock.patch.object(actions.shared, "api", api), \
            mock.patch.object(actions, "_library_item", return_value=item), \
            mock.patch.object(actions.xbmcgui, "Dialog") as dialog, \
            mock.patch.object(actions, "request", return_value=episodes), \
            mock.patch.object(actions, "batch_request") as batch, \
            mock.patch.object(actions, "get_string", side_effect=str), \
            mock.patch.object(actions, "notify") as notify:
        dialog.return_value.yesno.return_value = confirmed
        actions.start_rewatch({"action": "startrewatch"})
    return api, batch, notify


def test_rewatch_resets_trakt_then_unwatches_the_watched_episodes():
    """A confirmed rewatch resets the show on Trakt by its Trakt id, then unwatches in Kodi."""
    api, batch, notify = _rewatch(reply={"reset_at": "2026-10-27T12:00:00.000Z"})
    api.search_by_id.assert_called_once_with("81189", "tvdb", "show")
    api.start_rewatch.assert_called_once_with(1388)
    batch.assert_called_once_with("VideoLibrary.SetEpisodeDetails",
                                  [{"episodeid": 7, "playcount": 0, "lastplayed": ""}])
    notify.assert_called_once_with("32222", "Breaking Bad")


def test_rewatch_trakt_refuses_leaves_kodi_alone():
    """A reset Trakt refuses changes nothing in Kodi and notifies the failure."""
    _, batch, notify = _rewatch(reply=None)
    batch.assert_not_called()
    notify.assert_called_once_with("32223", "Breaking Bad")


def test_rewatch_needs_confirming_and_a_show():
    """A declined confirmation or an item that is not a show asks Trakt nothing."""
    api, _, _ = _rewatch(confirmed=False)
    api.start_rewatch.assert_not_called()
    movie = {"media_type": "movie", "dbid": 5, "ids": {"tmdb": "550"}, "title": "Fight Club"}
    api, _, _ = _rewatch(item=movie)
    api.start_rewatch.assert_not_called()
