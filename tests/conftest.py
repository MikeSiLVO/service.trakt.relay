import sys
from unittest import mock


class WindowXMLDialog:
    """Base class stand-in, since add-on dialogs subclass it."""

    def __init__(self, *args) -> None:
        pass


class Monitor:
    """Base class stand-in, since the service monitor subclasses it."""


class Player:
    """Base class stand-in, since the service player subclasses it; other calls answer mocks."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __getattr__(self, name):
        return mock.Mock()


xbmc = mock.Mock()
xbmc.Monitor = Monitor
xbmc.Player = Player
xbmcgui = mock.Mock()
xbmcgui.WindowXMLDialog = WindowXMLDialog
xbmcaddon = mock.Mock()
xbmcaddon.Addon.return_value.getAddonInfo.side_effect = \
    lambda key: {"id": "service.trakt.relay", "name": "Trakt Relay"}.get(key, "")
sys.modules.update({"xbmc": xbmc, "xbmcgui": xbmcgui, "xbmcaddon": xbmcaddon})
