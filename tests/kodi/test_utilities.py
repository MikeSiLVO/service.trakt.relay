import os
import xml.etree.ElementTree as ET
from unittest import mock

from lib.kodi import utilities

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def test_notify_shows_a_kodi_notification():
    """A notice shows as a Kodi notification for 5 seconds by default."""
    with mock.patch.object(utilities.xbmcgui, "Dialog") as dialog:
        utilities.notify("header", "message")
    dialog.return_value.notification.assert_called_once_with("header", "message", mock.ANY, 5000)


def test_remove_empty_settings_only_removes_an_empty_file(tmp_path):
    """An empty user settings file is removed; one with content, or none at all, is left alone."""
    path = tmp_path / "settings.xml"
    with mock.patch.object(utilities.xbmcvfs, "translatePath", return_value=str(tmp_path)):
        path.write_text("")
        assert utilities.remove_empty_settings() and not path.exists()
        path.write_text("<settings version=\"2\" />")
        assert not utilities.remove_empty_settings() and path.exists()
        path.unlink()
        assert not utilities.remove_empty_settings()


def test_notify_shows_the_red_icon_while_set():
    """With the OG theme on, a notice shows the red Trakt icon the add-on ships."""
    with mock.patch.object(utilities.xbmcgui, "Dialog") as dialog, \
            mock.patch.object(utilities, "get_setting_bool", return_value=True):
        utilities.notify("header", "message")
    icon = dialog.return_value.notification.call_args.args[2]
    assert icon.endswith("icon-red.png") and os.path.isfile(os.path.join(ROOT, icon))


def _excluded(fullpath, **settings):
    """Run is_excluded with only the given settings set."""
    def setting(name):
        return settings.get(name, "")

    def flag(name):
        return settings.get(name, "") is True
    with mock.patch.object(utilities, "get_setting", setting), \
            mock.patch.object(utilities, "get_setting_bool", flag):
        return utilities.is_excluded(fullpath)


def test_excluded_path_first_and_last_slot():
    """A path set in the first or the twelfth exclusion slot excludes files under it."""
    assert _excluded("C:/a/video.mkv", ExcludePath="C:/a/", ExcludePathOption=True)
    assert _excluded("C:/a/video.mkv", ExcludePath12="C:/a/", ExcludePathOption12=True)


def test_excluded_path_special_chars():
    """An exclusion path with accented letters and a percent sign still matches."""
    assert _excluded("C:/öäüß%6/video.mkv", ExcludePath3="C:/öäüß%6/", ExcludePathOption3=True)


def test_excluded_path_elsewhere_or_disabled():
    """A file outside the exclusion path, or under a disabled one, is not excluded."""
    assert not _excluded("C:/b/video.mkv", ExcludePath="C:/a/", ExcludePathOption=True)
    assert not _excluded("C:/a/video.mkv", ExcludePath2="C:/a/", ExcludePathOption2=False)


def _proxy(relay=None, kodi=None):
    """Run get_proxy_url with the given Relay and Kodi settings."""
    relay, kodi = relay or {}, kodi or {}

    def setting(name):
        return relay.get(name, "")

    def flag(name):
        return relay.get(name) is True
    with mock.patch.object(utilities, "get_setting", setting), \
            mock.patch.object(utilities, "get_setting_bool", flag), \
            mock.patch.object(utilities, "get_kodi_setting", kodi.get):
        return utilities.get_proxy_url()


def test_proxy_override_encodes_credentials():
    """Relay's own proxy builds the URL with its username and password URL-encoded."""
    url = _proxy(relay={"proxy_override": True, "proxy_uri": "https://proxy.lan",
                        "proxy_port": "3128", "proxy_user": "me@home",
                        "proxy_passwd": "p:ss/w"})
    assert url == "https://me%40home:p%3Ass%2Fw@proxy.lan:3128"


def test_proxy_from_kodi_http_type():
    """Kodi's HTTP proxy is used when Relay sets none of its own."""
    kodi = {"network.usehttpproxy": True, "network.httpproxytype": 0,
            "network.httpproxyserver": "proxy.lan", "network.httpproxyport": 8080}
    assert _proxy(kodi=kodi) == "http://proxy.lan:8080"


def test_proxy_ignores_other_kodi_types_and_missing_port():
    """No proxy comes from a non-HTTP Kodi proxy, an override without a port, or nothing set."""
    kodi = {"network.usehttpproxy": True, "network.httpproxytype": 5,
            "network.httpproxyserver": "proxy.lan", "network.httpproxyport": 8080}
    assert _proxy(kodi=kodi) is None
    assert _proxy(relay={"proxy_override": True, "proxy_uri": "proxy.lan"}) is None
    assert _proxy() is None


def test_proxy_reads_settings_that_exist():
    """Every Relay setting the proxy URL reads is defined in settings.xml."""
    read = set()
    with mock.patch.object(utilities, "get_setting", lambda name: read.add(name) or "x"), \
            mock.patch.object(utilities, "get_setting_bool", lambda name: read.add(name) or True):
        utilities.get_proxy_url()
    root = ET.parse(os.path.join(ROOT, "resources", "settings.xml")).getroot()
    assert read and read <= {setting.get("id") for setting in root.iter("setting")}


def _playing(item=None, **labels):
    """Run get_playing_details for a non-library item with the given player infolabels."""
    window = mock.Mock()
    window.getProperty.return_value = ""
    with mock.patch.object(utilities.xbmc, "getInfoLabel",
                           lambda name: labels.get(name.split(".")[1], "")), \
            mock.patch.object(utilities.xbmcgui, "Window", return_value=window):
        return utilities.get_playing_details(
            item or {"item": {"type": "unknown", "label": "x"}})


def test_playing_episode_from_labels():
    """A non-library episode is read from the player's show, season and episode labels."""
    media_type, data = _playing(TVShowTitle="Lost", Season="1", Episode="2", EpisodeName="Pilot")
    assert media_type == "episode"
    assert (data["showtitle"], data["season"], data["episode"]) == ("Lost", 1, 2)


def test_playing_movie_from_year_label():
    """A movie is read from the player's year, the title cleaned of its year suffix."""
    media_type, data = _playing(Title="Birthday (2019)", Year="2019")
    assert media_type == data["type"] == "movie"
    assert (data["title"], data["year"]) == ("Birthday", 2019)


def test_playing_recording_from_labels():
    """A PVR recording is read from the player labels like any non-library episode."""
    media_type, data = _playing({"item": {"type": "recording", "id": 3, "label": "x"}},
                                TVShowTitle="Lost", Season="1", Episode="2", EpisodeName="Pilot")
    assert media_type == data["type"] == "episode"
    assert (data["showtitle"], data["season"], data["episode"]) == ("Lost", 1, 2)


def test_playing_file_label_skipped():
    """An item without season, episode, year or ids is skipped, whatever its title reads like."""
    for title in ("Some home video", "Lost.S01E02.720p", "bingie_intro_1080p", "Birthday (2019)"):
        assert _playing(Title=title) == ("", {})
