from unittest import mock

import pytest

from lib.dialogs import setup
from tests.helpers import settings


def test_current_choices_read_each_row_and_leave_a_split_row_open():
    """Each row shows its settings' choice, and none where movies and episodes differ."""
    with settings(sync_watched_movies=1, sync_watched_episodes=3, sync_ratings=2,
                  trakt_movie_playback=True, trakt_episode_playback=True, sync_on_update=False):
        assert setup._current_choices() == {"watched": None, "ratings": 2, "resume": 1, "scan": 0}


def test_save_writes_each_row_to_its_settings_and_skips_an_open_row():
    """Saving writes each row, toggles as true or false, skips open rows, marks the setup saved."""
    with mock.patch.object(setup, "set_setting") as set_setting:
        setup._save({"watched": None, "ratings": 3, "resume": 1, "scan": 0})
    assert [c.args for c in set_setting.call_args_list] == [
        ("sync_ratings", 3), ("trakt_movie_playback", "true"),
        ("trakt_episode_playback", "true"), ("sync_on_update", "false"),
        ("sync_setup_saved", "true")]


def _dialog(choices):
    """A setup dialog with the given choices; return it with its mocked property and close."""
    dialog = setup.SyncSetupDialog(setup.SETUP_XML, "", "Default", "1080i")
    dialog.choices = dict(choices)
    set_property = dialog.setProperty = mock.Mock()
    close = dialog.close = mock.Mock()
    return dialog, set_property, close


def test_clicked_choice_replaces_its_rows_choice():
    """Clicking a choice records it for its row and marks it, leaving the dialog open."""
    dialog, set_property, close = _dialog({"watched": 0, "ratings": 0})
    dialog.onClick(202)
    assert dialog.choices == {"watched": 0, "ratings": 2}
    set_property.assert_called_once_with("ratings", "2")
    close.assert_not_called()


@pytest.mark.parametrize("control_id, sync_now", [(setup.SAVE, False), (setup.SAVE_AND_SYNC, True)])
def test_save_buttons_close_and_flag_a_sync_only_for_save_and_sync(control_id, sync_now):
    """Both save buttons close the dialog as saved; only Save and sync asks for a sync."""
    dialog, _, close = _dialog({})
    dialog.onClick(control_id)
    assert (dialog.saved, dialog.sync_now) == (True, sync_now)
    close.assert_called_once_with()


def _run(saved, sync_now):
    """Run the setup with a dialog closed as given; return the patched save and event sender."""
    dialog = mock.Mock(saved=saved, sync_now=sync_now)
    with mock.patch.object(setup, "SyncSetupDialog", return_value=dialog), \
            mock.patch.object(setup, "set_theme"), \
            mock.patch.object(setup, "ADDON"), \
            mock.patch.object(setup, "_current_choices", return_value={}), \
            mock.patch.object(setup, "_save") as save, \
            mock.patch.object(setup, "send_event") as send_event:
        setup.run()
    return save, send_event


def test_run_saves_and_syncs_only_as_the_dialog_was_closed():
    """Back saves nothing; Save saves; Save and sync also asks the service for a sync."""
    save, send_event = _run(saved=False, sync_now=False)
    save.assert_not_called()
    send_event.assert_not_called()
    save, send_event = _run(saved=True, sync_now=False)
    save.assert_called_once()
    send_event.assert_not_called()
    _, send_event = _run(saved=True, sync_now=True)
    send_event.assert_called_once_with({"action": "sync"})


@pytest.mark.parametrize("saved", [True, False])
def test_offer_shows_the_setup_until_it_is_saved(saved):
    """The setup is offered only while it has never been saved."""
    with mock.patch.object(setup, "get_setting_bool", return_value=saved), \
            mock.patch.object(setup, "run") as run:
        setup.offer()
    assert run.called is not saved
