import pytest

from lib.media import utilities
from tests.helpers import load_fixture

TYPE_CHECKS = {"movie": utilities.is_movie, "episode": utilities.is_episode,
               "show": utilities.is_show, "season": utilities.is_season}


@pytest.mark.parametrize("media_type", TYPE_CHECKS)
def test_type_check_matches_only_its_own_type(media_type):
    """Each type check is True for its own type and False for the other three."""
    assert [t for t in TYPE_CHECKS if TYPE_CHECKS[media_type](t)] == [media_type]


def test_only_the_four_trakt_types_are_valid():
    """The four Trakt item types are valid and Kodi's tvshow is not."""
    assert all(utilities.is_valid_media_type(t) for t in TYPE_CHECKS)
    assert not utilities.is_valid_media_type("tvshow")


def test_chunks_of_one_give_a_slice_per_item():
    """Chunks of one give a slice per item, three for the movies fixture."""
    movies = load_fixture("movies.json")
    assert len(utilities.chunks(movies, 1)) == 3


def test_show_name_is_its_title():
    """A show's display name is its title."""
    data = load_fixture("show.json")
    assert utilities.format_item_name("show", data) == "Game of Thrones"


def test_season_name_adds_its_number():
    """A season's display name is its title and season number, or Specials for season 0."""
    data = load_fixture("season.json")
    assert utilities.format_item_name("season", data) == "Regular Show - Season 8"
    assert utilities.format_item_name("season", dict(data, season=0)) == "Regular Show - Specials"


def test_episode_name_leads_with_its_code():
    """An episode's display name is its SxxEyy code, then its title."""
    data = load_fixture("episode.json")
    assert (
        utilities.format_item_name("episode", data) == "S01E01 - Winter Is Coming"
    )


def test_movie_name_adds_its_year():
    """A movie's display name is its title and year."""
    data = load_fixture("movie.json")
    assert utilities.format_item_name("movie", data) == "TRON: Legacy (2010)"
