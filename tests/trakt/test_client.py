import logging
import urllib.error
from email.message import Message
from unittest import mock

from lib.trakt.client import TraktClient


def test_build_path_omits_none_params():
    """Params set to None stay out of the built path."""
    client = TraktClient("id", "ua")

    assert (
        client.build_path("/search/show", {"query": "Batman", "years": None})
        == "/search/show?query=Batman"
    )


def test_build_path_appends_to_an_existing_query():
    """Params join a path that already has a query with an ampersand."""
    client = TraktClient("id", "ua")

    assert (
        client.build_path("/search/show?query=Batman", {"page": 2, "limit": 100})
        == "/search/show?query=Batman&page=2&limit=100"
    )


def test_rate_limit_is_waited_out_and_resent_once():
    """A 429 with Retry-After is waited out and the request sent once more."""
    client = TraktClient("id", "ua")
    retry_headers = Message()
    retry_headers["Retry-After"] = "1"
    rate_limit = urllib.error.HTTPError(
        "https://api.trakt.tv/search/movie",
        429,
        "Too Many Requests",
        retry_headers,
        None,
    )
    response = mock.Mock()
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=None)
    response.read.return_value = b'{"ok": true}'
    opener = mock.Mock()
    opener.open = mock.Mock(side_effect=[rate_limit, response])

    with mock.patch(
        "lib.trakt.client.urllib.request.build_opener", return_value=opener
    ):
        with mock.patch("lib.trakt.client.time.sleep") as sleep:
            result = client.request("GET", "/search/movie")

    assert result == {"ok": True}
    sleep.assert_called_once_with(1)
    assert opener.open.call_count == 2


def test_network_error_returns_none():
    """A network error returns None instead of raising."""
    client = TraktClient("id", "ua")
    opener = mock.Mock()
    opener.open = mock.Mock(side_effect=urllib.error.URLError("offline"))

    with mock.patch(
        "lib.trakt.client.urllib.request.build_opener", return_value=opener
    ):
        result = client.request("GET", "/users/settings")

    assert result is None


def test_error_code_caller_gets_the_status_unlogged(caplog):
    """A caller taking the error code gets the HTTP status back, with no failure logged."""
    client = TraktClient("id", "ua")
    pending = urllib.error.HTTPError(
        "https://api.trakt.tv/oauth/device/token", 400, "Bad Request", Message(), None)
    opener = mock.Mock()
    opener.open = mock.Mock(side_effect=pending)
    caplog.set_level(logging.DEBUG, logger="lib.trakt.client")

    with mock.patch("lib.trakt.client.urllib.request.build_opener", return_value=opener):
        result = client.request("POST", "/oauth/device/token", {}, include_error_code=True)

    assert result == (None, 400)
    assert "failed" not in caplog.text
