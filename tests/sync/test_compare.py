from typing import Optional

import pytest

from lib.sync.compare import (
    compare_episodes,
    compare_movies,
    compare_shows,
    count_episodes,
    episodes_to_unwatch,
    movies_to_mark_watched,
    needs_resume,
    sanitize_movies,
    sanitize_shows,
)
from tests.helpers import HEAT, load_fixture

TITLE_MATCHING = pytest.mark.parametrize("match_title_year", [True, False])


def test_sanitize_movies_strips_kodi_and_state_fields():
    """Sanitizing movies drops the five Kodi and state fields and keeps every other field."""
    stripped = {"collected", "watched", "plays", "movieid", "userrating"}
    movies = load_fixture("movies_unsanatized.json")
    before = [set(movie) for movie in movies]
    sanitize_movies(movies)
    assert all(stripped <= fields for fields in before)
    assert [set(movie) for movie in movies] == [fields - stripped for fields in before]


def test_compare_movies_with_two_same_titled_years():
    """Two same-titled movies of different years both count as missing from an empty target."""
    local = load_fixture("movies_local_same_name.json")

    assert compare_movies(local, [], True) == local


def test_compare_movies_with_one_of_two_same_titled_years_collected():
    """Of two same-titled movies, only the year the target lacks counts as missing."""
    local = load_fixture("movies_local_same_name.json")
    remote = load_fixture("movies_local_same_name_2.json")
    expected = load_fixture("movies_local_same_name_result.json")

    assert compare_movies(local, remote, True) == expected


def test_watched_movie_never_marks_a_same_titled_year():
    """A Trakt play never marks a Kodi movie of the same title but another year."""
    trakt = [{"title": "Life", "year": 2017, "plays": 1, "ids": {"imdb": "tt5442430"}}]
    kodi = [{"title": "Life", "year": 1999, "movieid": 3, "plays": 0,
             "ids": {"imdb": "tt0123964"}}]

    assert movies_to_mark_watched(trakt, kodi, True) == []


def test_watched_movie_without_a_year_marks_by_title():
    """A Trakt play with no year marks the Kodi movie of the same title."""
    trakt = [{"title": "Life", "year": None, "plays": 1, "ids": {},
              "last_watched_at": "2026-10-01T20:00:00.000Z"}]
    kodi = [{"title": "Life", "year": 1999, "movieid": 3, "plays": 0, "ids": {}}]

    assert [update["movieid"] for update in movies_to_mark_watched(trakt, kodi, True)] == [3]


@TITLE_MATCHING
def test_compare_movies_with_an_uncollected_match(match_title_year):
    """A movie the target has but hasn't collected counts as missing."""
    local = load_fixture("movies_local.json")
    remote = load_fixture("movies_remote.json")
    expected = load_fixture("movies_watched.json")

    assert compare_movies(local, remote, match_title_year) == expected


@TITLE_MATCHING
def test_compare_movies_watched_with_an_unwatched_match(match_title_year):
    """A movie watched in the source but not the target is listed."""
    local = load_fixture("movies_local.json")
    remote = load_fixture("movies_remote.json")
    expected = load_fixture("movies_watched.json")

    assert compare_movies(local, remote, match_title_year, watched=True) == expected


@TITLE_MATCHING
def test_compare_movies_playback_with_matches(match_title_year):
    """Playback mode lists every matched movie, to copy its resume point."""
    local = load_fixture("movies_local.json")
    remote = load_fixture("movies_remote.json")

    assert compare_movies(local, remote, match_title_year, playback=True) == local


@TITLE_MATCHING
def test_compare_movies_rating_with_matches(match_title_year):
    """Only the movie whose rating differs from the target's is listed."""
    local = load_fixture("movies_local.json")
    remote = load_fixture("movies_remote.json")
    expected = load_fixture("movies_watched.json")

    assert compare_movies(local, remote, match_title_year, rating=True) == expected


@TITLE_MATCHING
def test_compare_movies_with_an_empty_target(match_title_year):
    """Against an empty target every collected movie is missing."""
    local = load_fixture("movies_local.json")

    assert compare_movies(local, [], match_title_year) == local


@TITLE_MATCHING
def test_compare_movies_watched_with_an_empty_target(match_title_year):
    """Against an empty target only watched movies are listed."""
    local = load_fixture("movies_local.json")
    expected = load_fixture("movies_watched.json")

    assert compare_movies(local, [], match_title_year, watched=True) == expected


@TITLE_MATCHING
def test_compare_movies_playback_with_an_empty_target(match_title_year):
    """Against an empty target playback mode lists every movie."""
    local = load_fixture("movies_local.json")

    assert compare_movies(local, [], match_title_year, playback=True) == local


@TITLE_MATCHING
def test_compare_movies_rating_with_an_empty_target(match_title_year):
    """Against an empty target every rated movie is listed."""
    local = load_fixture("movies_local.json")

    assert compare_movies(local, [], match_title_year, rating=True) == local


@TITLE_MATCHING
def test_compare_shows_rating_unrated_on_both_sides(match_title_year):
    """A show unrated on both sides gives no rating change."""
    local = load_fixture("compare_shows_local_batman.json")
    remote = load_fixture("compare_shows_remote_batman.json")

    assert compare_shows(local, remote, match_title_year) == {"shows": []}


@TITLE_MATCHING
def test_compare_shows_rating_rated_in_the_source(match_title_year):
    """A source-only show rating is listed with merged ids."""
    local = load_fixture("compare_shows_local_batman_rating.json")
    remote = load_fixture("compare_shows_remote_batman.json")
    expected = load_fixture("compare_shows_compared_batman.json")

    assert compare_shows(local, remote, match_title_year) == expected


PAUSED = {"progress": 40.0, "paused_at": "2026-10-29T20:00:00.000Z"}


@pytest.mark.parametrize("state, needed", [
    (None, True),
    ({"resume": {"position": 0, "total": 0}, "lastplayed": ""}, True),
    ({"resume": {"position": 2405, "total": 6000}, "lastplayed": ""}, False),
    ({"resume": {"position": 600, "total": 6000}, "lastplayed": "2026-10-28 12:00:00"}, True),
    ({"resume": {"position": 0, "total": 0}, "lastplayed": "2026-10-30 12:00:00"}, False),
])
def test_resume_is_set_only_when_trakts_is_new_to_kodi(state, needed):
    """Trakt's paused point is set unless Kodi already resumes there or played the item since."""
    assert needs_resume(PAUSED, state) is needed


def _rated_show(ratings):
    """A show in compare form with each (season, episode) rated as given, 0 for unrated."""
    seasons = {}
    for (season, number), rating in ratings.items():
        seasons.setdefault(season, []).append(
            {"number": number, "ids": {"tvdb": season * 100 + number}, "rating": rating})
    return {"title": "Lost", "year": 2004, "ids": {"tvdb": 73739},
            "seasons": [{"number": s, "episodes": eps} for s, eps in seasons.items()]}


def _rated_episodes(result):
    """The (season, episode, rating) of every episode in a compare result."""
    return [(season["number"], episode["number"], episode["rating"])
            for show in result["shows"] for season in show["seasons"]
            for episode in season["episodes"]]


def test_compare_episodes_rating_lists_rated_episodes_the_target_lacks():
    """Rated episodes the target lacks are listed, in a season it has and in one it lacks."""
    kodi = {"shows": [_rated_show({(1, 1): 8, (1, 2): 7, (1, 3): 0, (2, 1): 9})]}
    trakt = {"shows": [_rated_show({(1, 1): 8})]}

    result = compare_episodes(kodi, trakt, True, rating=True)

    assert _rated_episodes(result) == [(1, 2, 7), (2, 1, 9)]


def test_compare_episodes_rating_restricted_lists_only_episodes_the_target_has():
    """A restricted rating compare lists differing episodes the target has and nothing else."""
    trakt = {"shows": [_rated_show({(1, 1): 9, (1, 2): 7, (2, 1): 6})]}
    kodi = {"shows": [_rated_show({(1, 1): 8})]}

    result = compare_episodes(trakt, kodi, True, restrict=True, rating=True)

    assert _rated_episodes(result) == [(1, 1, 9)]


@TITLE_MATCHING
def test_compare_episodes_with_every_episode_on_both_sides(match_title_year):
    """Episodes both sides have give nothing to add."""
    local = load_fixture("compare_shows_local_batman.json")
    remote = load_fixture("compare_shows_remote_batman.json")

    assert compare_episodes(local, remote, match_title_year) == {"shows": []}


@TITLE_MATCHING
def test_compare_episodes_with_one_missing_from_the_target(match_title_year):
    """The one episode the target lacks is listed under its show."""
    local = load_fixture("compare_shows_local_batman.json")
    remote = load_fixture("compare_shows_remote_batman_episode.json")
    expected = load_fixture("compare_shows_batman_episode_to_add.json")

    assert compare_episodes(local, remote, match_title_year) == expected


def _unwatch(keep_unplayed=False, reset_at: Optional[str] = "2026-10-01T00:00:00.000Z"):
    """Unwatch S01E01-03 watched in Kodi against Trakt's plays of E01 and E02; return updates."""
    plays = [{"number": 1, "plays": 2, "last_watched_at": "2026-10-02T00:00:00.000Z"},
             {"number": 2, "plays": 1, "last_watched_at": "2025-01-01T00:00:00.000Z"}]
    trakt = {"shows": [{"title": "Lost", "year": 2004, "ids": {"tvdb": 73739}, "reset_at": reset_at,
                        "seasons": [{"number": 1, "episodes": plays}]}]}
    kodi_episodes = [{"number": n, "plays": 1, "ids": {"episodeid": 10 + n}} for n in (1, 2, 3)]
    kodi = {"shows": [{"title": "Lost", "year": 2004, "ids": {"tvdb": "73739"},
                       "seasons": [{"number": 1, "episodes": kodi_episodes}]}]}
    return episodes_to_unwatch(trakt, kodi, True, keep_unplayed)


def test_rewatch_unwatches_episodes_it_has_not_reached():
    """Episodes last played before the rewatch or never on Trakt are unwatched; rewatched stay."""
    assert _unwatch() == [{"episodeid": n, "playcount": 0, "lastplayed": ""} for n in (12, 13)]


def test_rewatch_unwatching_can_keep_episodes_trakt_never_played():
    """Keeping episodes Trakt never played, only the one played before the rewatch is unwatched."""
    assert [update["episodeid"] for update in _unwatch(keep_unplayed=True)] == [12]


def test_show_not_in_a_rewatch_unwatches_nothing():
    """A show Trakt has no rewatch for keeps every Kodi episode watched."""
    assert _unwatch(reset_at=None) == []


def test_count_episodes_counts_each_one():
    """The local Batman fixture counts six episodes."""
    data1 = load_fixture("compare_shows_local_batman.json")

    assert count_episodes(data1) == 6


def test_count_episodes_one_short():
    """The remote Batman fixture, one episode short, counts five."""
    data1 = load_fixture(
        "compare_shows_remote_batman_episode.json"
    )

    assert count_episodes(data1) == 5


def test_sanitize_shows_strips_episode_fields():
    """Sanitizing shows drops each episode's state fields and Kodi episodeid."""
    shows = {
        "shows": [
            {
                "title": "Batman",
                "seasons": [
                    {
                        "number": 1,
                        "episodes": [
                            {
                                "number": 1,
                                "collected": True,
                                "watched": True,
                                "season": 1,
                                "plays": 1,
                                "ids": {"trakt": 1, "episodeid": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    sanitize_shows(shows)
    episode = shows["shows"][0]["seasons"][0]["episodes"][0]
    assert "collected" not in episode
    assert "watched" not in episode
    assert "season" not in episode
    assert "plays" not in episode
    assert "episodeid" not in episode["ids"]


def test_ratings_sync_never_clears_a_rating():
    """Ratings copy only where the source has one; an unrated side never clears the other."""
    rated, unrated = dict(HEAT, rating=8), dict(HEAT, rating=0)
    assert compare_movies([unrated], [rated], False, restrict=True, rating=True) == []
    assert compare_movies([rated], [unrated], False, restrict=True, rating=True)[0]["rating"] == 8
    show = {"title": "Lost", "year": 2004, "ids": {"tvdb": "73739"}, "tvshowid": 3}
    assert compare_shows({"shows": [show]}, {"shows": [dict(show, rating=9)]}, False,
                         restrict=True) == {"shows": []}
    episode = {"number": 1, "ids": {"episodeid": 5}}
    with_episode = dict(show, seasons=[{"number": 1, "episodes": [dict(episode, rating=0)]}])
    rated_episode = dict(show, seasons=[{"number": 1, "episodes": [dict(episode, rating=6)]}])
    assert compare_episodes({"shows": [with_episode]}, {"shows": [rated_episode]}, False,
                            restrict=True, rating=True) == {"shows": []}
