from unittest import mock

from lib.dialogs import rating

MOVIE = {"title": "Heat", "year": 1995, "ids": {"trakt": 1}}


def _movie(current):
    """A movie summary carrying the user's current rating, 0 for unrated."""
    return dict(MOVIE, user={"ratings": {"rating": current} if current else {}})


def _rate(items, answers, rerate=True, **kwargs):
    """Rate movies, the dialog giving each answer in turn; return the ratings sent and cleared."""
    def dialog(xml_file, path, skin, resolution):
        return mock.Mock(rating=answers.pop(0))

    api = mock.Mock()
    with mock.patch.object(rating, "RatingDialog", side_effect=dialog), \
            mock.patch.object(rating.shared, "api", api), \
            mock.patch.object(rating, "request"), \
            mock.patch.object(rating, "kodiUtilities"), \
            mock.patch.object(rating, "ADDON"), \
            mock.patch.object(rating, "get_setting_bool", return_value=rerate):
        rating.rate_media("movie", items, **kwargs)
    sent = [c.args[0]["movies"][0]["rating"] for c in api.add_ratings.call_args_list]
    cleared = [c.args[0]["movies"][0]["rating"] for c in api.remove_ratings.call_args_list]
    return sent, cleared


def test_rerating_to_current_value_unrates():
    """With re-rating on, picking the current rating clears it and any other value replaces it."""
    assert _rate([_movie(8)], [8]) == ([], [0])
    assert _rate([_movie(8)], [6]) == ([6], [])


def test_each_item_gets_its_own_answer():
    """Each unrated item gets the dialog and its own answer."""
    assert _rate([_movie(0), _movie(0)], [7, 9]) == ([7, 9], [])


def test_given_rating_and_unrate_cover_every_item():
    """A given rating and an unrate cover every item, rating the unrated and clearing the rated."""
    assert _rate([_movie(0), _movie(0)], [], rating=5) == ([5, 5], [])
    assert _rate([_movie(4), _movie(0)], [], unrate=True) == ([], [0])


def test_library_season_rating_is_copied_to_kodi():
    """A season rating reaches Trakt under its show and the library season's user rating."""
    season = {"title": "Lost", "year": 2004, "ids": {"trakt": 2}, "season": 3, "seasonid": 40}
    api = mock.Mock()
    with mock.patch.object(rating.shared, "api", api), \
            mock.patch.object(rating, "request") as request, \
            mock.patch.object(rating, "kodiUtilities"):
        rating._rate_on_trakt(7, "season", season)
    assert api.add_ratings.call_args.args[0]["shows"][0]["seasons"] == [{"rating": 7, "number": 3}]
    request.assert_called_once_with(
        "VideoLibrary.SetSeasonDetails", {"seasonid": 40, "userrating": 7})


def test_non_library_item_leaves_kodi_alone():
    """An item outside the library, with no Kodi id, is rated on Trakt without a Kodi call."""
    with mock.patch.object(rating.shared, "api"), \
            mock.patch.object(rating, "request") as request, \
            mock.patch.object(rating, "kodiUtilities"):
        rating._rate_on_trakt(7, "movie", dict(MOVIE, movieid=None))
    request.assert_not_called()


def test_rating_notice_carries_the_rating():
    """A rating Trakt accepts is notified with its value, the item's name below."""
    with mock.patch.object(rating.shared, "api") as api, \
            mock.patch.object(rating, "request"), \
            mock.patch.object(rating, "get_string", return_value="Rated %i"), \
            mock.patch.object(rating, "kodiUtilities") as kodi:
        api.add_ratings.return_value = {"added": {"movies": 1}, "not_found": {}}
        rating._rate_on_trakt(7, "movie", dict(MOVIE, movieid=3))
    kodi.notify.assert_called_once_with("Rated 7", "Heat (1995)")


def _focus(control_id, current):
    """Focus a heart in the re-rating dialog for an episode; return the label it shows."""
    dialog = rating.RatingDialog(rating.RATING_XML, "", "Default", "1080i")
    dialog.media_type = "episode"
    dialog.media = {"user": {"ratings": {"rating": current} if current else {}}}
    dialog.rerate = True
    dialog.getControl = mock.Mock()
    with mock.patch.object(rating, "get_string", side_effect=str):
        dialog.onFocus(control_id)
    return dialog.getControl.return_value.setLabel.call_args.args[0]


def test_focused_heart_shows_its_number_and_word_or_unrate():
    """A focused heart shows its rating and word, or the unrate offer on the current rating."""
    assert _focus(107, 8) == "[B]7  32034[/B]"
    assert _focus(108, 8) == "[B]32039[/B]"
    assert _focus(110, 0) == "[B]10  32027[/B]"
