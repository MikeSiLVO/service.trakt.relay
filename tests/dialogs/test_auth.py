import threading
from unittest import mock

from lib.dialogs import auth

CODE = {"user_code": "5055CC52", "verification_url": "https://auth.trakt.tv/activate"}


def _connect(code, approve, settings=False):
    """Connect with a mocked API and a dialog the token wait closes; return the result and mocks."""
    api = mock.Mock(connected=False)
    api.request_device_code.return_value = code
    closed = threading.Event()

    def wait_for_token(code, stop):
        api.connected = approve
        return approve

    api.wait_for_token.side_effect = wait_for_token
    dialog = mock.Mock(open_settings=settings)
    dialog.close.side_effect = closed.set
    dialog.doModal.side_effect = lambda: closed.wait(5)
    with mock.patch.object(auth.shared, "api", api), \
            mock.patch.object(auth, "DeviceAuthDialog", return_value=dialog), \
            mock.patch.object(auth, "ADDON",
                              mock.Mock(**{"getAddonInfo.return_value": "Trakt Relay"})), \
            mock.patch.object(auth, "notify") as notify, \
            mock.patch.object(auth, "show_settings") as show_settings, \
            mock.patch.object(auth, "get_setting", return_value="mikesilvo"), \
            mock.patch.object(auth, "get_string", side_effect=str):
        connected = auth.connect()
    return connected, dialog, notify, show_settings


def test_approved_code_closes_the_dialog_and_connects():
    """An approved code closes the dialog, connects and confirms it with the account name."""
    connected, dialog, notify, show_settings = _connect(CODE, approve=True)
    assert connected is True
    assert dialog.code == "5055CC52"
    dialog.close.assert_called_once_with()
    notify.assert_called_once_with("32152", "mikesilvo", 3000)
    show_settings.assert_not_called()


def test_refused_code_closes_the_dialog_without_connecting():
    """A refused or expired code closes the dialog and leaves Relay unconnected."""
    connected, dialog, notify, show_settings = _connect(CODE, approve=False)
    assert connected is False
    dialog.close.assert_called_once_with()
    notify.assert_not_called()
    show_settings.assert_not_called()


def test_settings_button_opens_the_settings_once_closed():
    """A dialog left by its Settings button opens Relay's settings and stays unconnected."""
    connected, _, notify, show_settings = _connect(CODE, approve=False, settings=True)
    assert connected is False
    notify.assert_not_called()
    show_settings.assert_called_once_with()


def test_unreachable_trakt_reports_an_error_without_a_dialog():
    """No device code means an error notice under the add-on's name and no dialog."""
    connected, dialog, notify, _ = _connect(None, approve=False)
    assert connected is False
    dialog.doModal.assert_not_called()
    notify.assert_called_once_with("Trakt Relay", "32023")


def test_not_connected_notice_shows_once_per_session():
    """The not-connected notice shows on the first call only."""
    with mock.patch.object(auth, "_warned", threading.Event()), \
            mock.patch.object(auth, "notify") as notify, \
            mock.patch.object(auth, "get_string", side_effect=str), \
            mock.patch.object(auth, "ADDON"):
        auth.notify_not_connected()
        auth.notify_not_connected()
    notify.assert_called_once()


def _prompt(conditions, skins=("estuary", "estuary"), connected=False):
    """Tick the connect prompt per idle condition; return it and the patched connect."""
    xbmc = mock.Mock()
    xbmc.getCondVisibility.side_effect = list(conditions)
    xbmc.getSkinDir.side_effect = list(skins)
    with mock.patch.object(auth, "xbmc", xbmc), \
            mock.patch("lib.kodi.utilities.xbmc", xbmc), \
            mock.patch.object(auth.shared, "api", mock.Mock(connected=connected)), \
            mock.patch.object(auth, "connect", return_value=False) as connect:
        prompt = auth.ConnectPrompt()
        for _ in conditions:
            prompt.tick()
    return prompt, connect


def test_prompt_waits_for_three_unbroken_seconds():
    """The connect dialog shows once condition holds 3 ticks running; a break resets the count."""
    _, connect = _prompt([True, True, False, True, True])
    connect.assert_not_called()
    prompt, connect = _prompt([True, True, True])
    connect.assert_called_once_with()
    assert not prompt.pending


def test_prompt_closed_by_a_skin_switch_stays_pending():
    """A connect dialog closed while the skin changed shows again later in the session."""
    prompt, _ = _prompt([True] * 3, skins=("estuary", "fuse"))
    assert prompt.pending


def test_prompt_skipped_once_connected():
    """A connected Relay never shows the connect dialog."""
    _, connect = _prompt([True] * 3, connected=True)
    connect.assert_not_called()
