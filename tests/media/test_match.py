import pytest

from lib.media.match import (
    build_media_index,
    find_in_index,
    fuzzy_match,
    split_title_year,
)
from tests.helpers import load_fixture


@pytest.mark.parametrize("title, parts", [
    ("ShowTitle (2014)", ("ShowTitle", "2014")),
    ("ShowTitle", ("", "")),
])
def test_split_title_year_needs_a_year_in_brackets(title, parts):
    """A title splits into title and year only when it ends in a year in brackets."""
    assert split_title_year(title) == parts


def test_ids_only_finds_nothing_for_other_ids():
    """Matching by ids alone finds nothing when every id differs."""
    data1 = load_fixture("movies_local_blind.json")
    data2 = load_fixture("movies_remote_blind_no_match.json")

    assert find_in_index(data1, build_media_index(data2), False) is None


def test_ids_only_finds_a_shared_id():
    """Matching by ids alone finds the item sharing an id."""
    data1 = load_fixture("movies_local_blind.json")
    data2 = load_fixture("movies_remote_blind_match.json")

    assert find_in_index(data1, build_media_index(data2), False) == data1


def test_ids_only_finds_nothing_in_an_empty_index():
    """An empty index finds nothing, matching by ids alone."""
    data1 = load_fixture("movies_local_chaos.json")
    data2 = []

    assert find_in_index(data1, build_media_index(data2), False) is None


def test_ids_only_ignores_a_same_title_and_year():
    """Matching by ids alone ignores a movie of the same title and year with other ids."""
    data1 = load_fixture("movies_local_chaos.json")
    data2 = load_fixture("movies_remote_chaos_match.json")

    assert find_in_index(data1, build_media_index(data2), False) is None


def test_title_year_finds_nothing_for_another_year():
    """Matching by title and year finds nothing when the ids and year differ."""
    data1 = load_fixture("movies_local_blind.json")
    data2 = load_fixture("movies_remote_blind_no_match.json")

    assert find_in_index(data1, build_media_index(data2), True) is None


def test_title_year_finds_a_shared_id():
    """Matching by title and year still finds the item sharing an id."""
    data1 = load_fixture("movies_local_blind.json")
    data2 = load_fixture("movies_remote_blind_match.json")

    assert find_in_index(data1, build_media_index(data2), True) == data1


def test_title_year_finds_nothing_in_an_empty_index():
    """An empty index finds nothing, matching by title and year."""
    data1 = load_fixture("movies_local_chaos.json")
    data2 = []

    assert find_in_index(data1, build_media_index(data2), True) is None


def test_title_year_finds_a_same_title_and_year():
    """Matching by title and year finds a movie of the same title and year with other ids."""
    data1 = load_fixture("movies_local_chaos.json")
    data2 = load_fixture("movies_remote_chaos_match.json")

    assert find_in_index(data1, build_media_index(data2), True) == data2[0]


def test_index_keeps_the_first_of_two_duplicates():
    """Of two items with the same ids, the index keeps the first."""
    first = {"title": "Chaos", "year": 2005, "ids": {"imdb": "tt0402910"}, "n": 1}
    second = {"title": "Chaos", "year": 2005, "ids": {"imdb": "tt0402910"}, "n": 2}

    assert find_in_index(dict(first), build_media_index([first, second]), True) is first


def test_fuzzy_match_allows_a_dropped_article():
    """Titles match despite a dropped article, but not when they differ."""
    assert fuzzy_match("The Dark Knight", "The Dark Knight")
    assert fuzzy_match("The Dark Knight", "Dark Knight")
    assert not fuzzy_match("The Dark Knight", "The Joker")
