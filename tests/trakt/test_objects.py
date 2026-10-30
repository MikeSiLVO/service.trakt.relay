from lib.trakt.objects import TraktObject, TraktSeason


def test_trakt_object_exposes_fields_ids_and_episodes():
    """A TraktObject exposes fields as attributes, ids as pairs and episodes by number."""
    item = TraktObject(
        {
            "title": "Example Show",
            "ids": {"trakt": 1, "tvdb": 2},
            "seasons": [
                {
                    "number": 1,
                    "episodes": [{"number": 2, "title": "Episode"}],
                }
            ],
        }
    )

    assert item.title == "Example Show"
    assert dict(item.keys) == {"trakt": 1, "tvdb": 2}
    assert item.to_dict()["title"] == "Example Show"
    assert item.episodes[2].title == "Episode"
    assert item.episodes[2].season == 1


def test_season_episodes_key_on_season_and_number():
    """A season's episodes key on their (season, episode) pair."""
    season = TraktSeason({"number": 3, "episodes": [{"number": 7, "title": "Episode"}]})

    assert season.episodes[7].keys[0] == (3, 7)
