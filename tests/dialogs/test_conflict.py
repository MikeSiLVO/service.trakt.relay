from unittest import mock

from lib.dialogs import conflict

ON = (True, True)
BUSY = (True, False)
OFF = (False, True)


def _run(ticks, connected=True, disable=True):
    """Tick a warning a second apart per (enabled, idle) pair; return dialog, request, checks."""
    clock = {"now": 0.0, "tick": OFF}
    checks = []

    def condition(text):
        enabled, idle = clock["tick"]
        if "AddonIsEnabled" in text:
            checks.append(clock["now"])
            return enabled
        return idle

    xbmc = mock.Mock()
    xbmc.getCondVisibility.side_effect = condition
    dialog = mock.Mock()
    dialog.return_value.yesno.return_value = disable
    with mock.patch.object(conflict, "xbmc", xbmc), \
            mock.patch("lib.kodi.utilities.xbmc", xbmc), \
            mock.patch.object(conflict, "time", mock.Mock(time=lambda: clock["now"])), \
            mock.patch.object(conflict.xbmcgui, "Dialog", dialog), \
            mock.patch.object(conflict.shared, "api", mock.Mock(connected=connected)), \
            mock.patch.object(conflict, "get_string", side_effect=str), \
            mock.patch.object(conflict, "ADDON"), \
            mock.patch.object(conflict, "request") as request:
        warning = conflict.ConflictWarning()
        for tick in ticks:
            clock["tick"] = tick
            warning.tick()
            clock["now"] += 1
    return dialog.return_value.yesno, request, checks


def test_warns_on_the_third_idle_tick_in_a_row():
    """A busy tick restarts the count; it warns once, not again while the add-on stays on."""
    yesno, _, _ = _run([ON, ON, BUSY, ON, ON])
    yesno.assert_not_called()
    yesno, _, _ = _run([ON] * 150, disable=False)
    yesno.assert_called_once()


def test_checks_the_add_on_once_a_minute():
    """The add-on is checked at startup and then once every 60 seconds."""
    _, _, checks = _run([ON] * 121, disable=False)
    assert checks == [0, 60, 120]


def test_waits_while_relay_is_not_connected():
    """A Relay not connected to Trakt never shows the warning."""
    yesno, _, _ = _run([ON] * 5, connected=False)
    yesno.assert_not_called()


def test_disable_turns_the_add_on_off_and_warns_on_a_re_enable():
    """Disable turns the Trakt add-on off; finding it on again at the next check warns again."""
    yesno, request, _ = _run([ON] * 63)
    assert yesno.call_count == 2
    request.assert_called_with(
        "Addons.SetAddonEnabled", {"addonid": "script.trakt", "enabled": False})
    assert request.call_count == 2


def test_keep_both_warns_again_only_after_the_add_on_turns_off_and_on():
    """Keep both leaves the add-on on; it warns again once a check finds it off, then on."""
    yesno, request, _ = _run([ON] * 60 + [OFF] * 60 + [ON] * 3, disable=False)
    assert yesno.call_count == 2
    request.assert_not_called()
