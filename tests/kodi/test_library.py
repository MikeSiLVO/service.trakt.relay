from unittest import mock

from lib.kodi import library


def _convert(media_type, data, mode="collected"):
    """Run to_trakt_item with no path excluded."""
    with mock.patch.object(library, "is_excluded", return_value=False):
        return library.to_trakt_item(media_type, data, mode)


def test_movie_ids_come_from_uniqueid():
    """A movie's ids come from uniqueid and its playcount sets plays and watched."""
    movie = _convert("movie", {
        "label": "Heat", "title": "Heat", "file": "heat.mkv", "playcount": 2,
        "uniqueid": {"imdb": "tt0113277", "tmdb": "949"},
    })
    assert movie is not None
    assert movie["ids"] == {"imdb": "tt0113277", "tmdb": "949"}
    assert "uniqueid" not in movie and "label" not in movie
    assert (movie["plays"], movie["watched"], movie["collected"]) == (2, 1, 1)


def test_show_ids_come_from_uniqueid():
    """A show's ids come from uniqueid and its user rating becomes its rating."""
    show = _convert("show", {"label": "Lost", "uniqueid": {"tvdb": "73739"}, "userrating": 8})
    assert show is not None
    assert show["ids"] == {"tvdb": "73739"}
    assert show["rating"] == 8


def test_episode_ids_copy_known_types_and_guess_unknown():
    """An episode keeps known id types and guesses the type of an unknown one."""
    base = {"season": 1, "episode": 2, "label": "Pilot", "episodeid": 7, "file": "e.mkv",
            "playcount": 0}
    known = _convert("episode", dict(base, uniqueid={"tvdb": "127", "imdb": "tt1"}))
    assert known is not None
    assert known["ids"] == {"episodeid": 7, "tvdb": "127", "imdb": "tt1"}
    unknown = _convert("episode", dict(base, uniqueid={"unknown": "128"}))
    assert unknown is not None
    assert unknown["ids"] == {"episodeid": 7, "tvdb": "128"}


def test_unwatched_episode_dropped_in_watched_mode():
    """An unwatched episode is left out in watched mode."""
    episode = {"season": 1, "episode": 1, "label": "x", "episodeid": 1, "file": "e.mkv",
               "playcount": 0, "uniqueid": {}}
    assert _convert("episode", episode, mode="watched") is None


def _kodi(replies):
    """Patch request to answer each JSON-RPC method from the given replies."""
    return mock.patch.object(library, "request", lambda method, params: replies.get(method))


def test_episode_details_adds_show_ids_and_year():
    """Episode details carry their show's ids and year."""
    replies = {
        "VideoLibrary.GetEpisodeDetails": {"episodedetails": {"episodeid": 5, "tvshowid": 9}},
        "VideoLibrary.GetTVShowDetails": {"tvshowdetails": {"year": 2004,
                                                            "uniqueid": {"tvdb": "73739"}}},
    }
    with _kodi(replies):
        episode = library.get_episode_details(5, ["season"])
    assert episode == {"episodeid": 5, "tvshowid": 9, "show_ids": {"tvdb": "73739"}, "year": 2004}


def test_episode_details_none_without_episode_or_show():
    """Episode details are None when Kodi lacks the episode or its show."""
    with _kodi({"VideoLibrary.GetEpisodeDetails": {"limits": {}}}):
        assert library.get_episode_details(5, ["tvshowid"]) is None
    with _kodi({"VideoLibrary.GetEpisodeDetails": {"episodedetails": {"tvshowid": 9}}}):
        assert library.get_episode_details(5, ["tvshowid"]) is None
