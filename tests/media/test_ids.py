import pytest

from lib.media.ids import pick_best_id, guess_ids
from tests.helpers import load_fixture


@pytest.mark.parametrize("item_id, media_type, id_type", [
    ("tt1431045", "movie", "imdb"),
    ("20077", "movie", "tmdb"),
    ("4346770", "show", "tvdb"),
    ("tron-legacy-2010", "movie", "slug"),
])
def test_guess_ids_reads_the_type_from_the_id(item_id, media_type, id_type):
    """A tt id is IMDb, a number is TMDB for a movie or TVDB otherwise, and the rest a slug."""
    assert guess_ids(item_id, media_type) == ({id_type: item_id}, id_type)


def test_best_id_prefers_trakt():
    """A show's Trakt id is picked over its other ids."""
    data = load_fixture("shows.json")
    assert pick_best_id(data[1]["show"]["ids"], "show") == (1395, "trakt")
