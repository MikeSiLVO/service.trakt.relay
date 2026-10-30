from unittest import mock

from lib.kodi import settings


def test_show_settings_opens_the_dialog():
    """Showing settings opens the add-on settings dialog."""
    with mock.patch.object(settings, "ADDON") as addon:
        settings.show_settings()
    addon.openSettings.assert_called_once_with()


def test_reload_settings_reads_through_a_fresh_addon():
    """After a reload, settings are read from a newly made Addon."""
    fresh = mock.Mock(**{"getSetting.return_value": " new "})
    with mock.patch.object(settings, "ADDON"), \
            mock.patch.object(settings.xbmcaddon, "Addon", return_value=fresh):
        settings.reload_settings()
        assert settings.get_setting("x") == "new"


def test_set_setting_skips_a_value_already_stored():
    """Setting a value already stored writes nothing; a new value is stored as a string."""
    with mock.patch.object(settings, "ADDON") as addon:
        addon.getSetting.return_value = "1"
        settings.set_setting("x", 1)
        addon.setSetting.assert_not_called()
        settings.set_setting("x", 2)
    addon.setSetting.assert_called_once_with("x", "2")
