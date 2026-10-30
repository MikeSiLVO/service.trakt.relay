import http.client
import logging
import urllib.error
from email.message import Message
from unittest import mock

import pytest

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


def _rate_limited(retry_after):
    """A 429 carrying the given Retry-After."""
    headers = Message()
    headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://api.trakt.tv/sync/history", 429, "Too Many Requests", headers, None)


def test_rate_limit_wait_ends_when_kodi_exits():
    """A rate-limit wait cut short by Kodi exiting sends no retry and returns None."""
    client = TraktClient("id", "ua")
    client.monitor = mock.Mock(**{"waitForAbort.return_value": True})
    opener = mock.Mock(**{"open.side_effect": [_rate_limited("30")]})

    with mock.patch("lib.trakt.client.urllib.request.build_opener", return_value=opener):
        assert client.request("POST", "/sync/history", {}) is None
    client.monitor.waitForAbort.assert_called_once_with(30)
    assert opener.open.call_count == 1


def test_zero_rate_limit_wait_never_waits_for_exit():
    """A Retry-After of 0 retries at once, since waitForAbort(0) would wait until Kodi exits."""
    client = TraktClient("id", "ua")
    client.monitor = mock.Mock()
    response = mock.Mock()
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=None)
    response.read.return_value = b'{"ok": true}'
    opener = mock.Mock(**{"open.side_effect": [_rate_limited("0"), response]})

    with mock.patch("lib.trakt.client.urllib.request.build_opener", return_value=opener):
        assert client.request("POST", "/sync/history", {}) == {"ok": True}
    client.monitor.waitForAbort.assert_not_called()


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


@pytest.mark.parametrize("open_error, body", [
    (ConnectionResetError("reset"), None),
    (http.client.RemoteDisconnected("closed"), None),
    (None, http.client.IncompleteRead(b'{"ok"')),
    (None, b"<html>blocked</html>"),
])
def test_broken_reply_returns_none(open_error, body):
    """A reset, a cut-off reply or a non-JSON body returns None instead of raising."""
    client = TraktClient("id", "ua")
    response = mock.Mock()
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=None)
    if isinstance(body, Exception):
        response.read.side_effect = body
    else:
        response.read.return_value = body
    opener = mock.Mock()
    opener.open = mock.Mock(side_effect=open_error, return_value=response)

    with mock.patch("lib.trakt.client.urllib.request.build_opener", return_value=opener):
        assert client.request("POST", "/scrobble/stop", {}) is None
        assert client.request("POST", "/scrobble/stop", {}, include_error_code=True) == (
            None, None)


def test_error_hook_gets_each_status():
    """The error hook gets the status of a failed request, which still returns None."""
    client = TraktClient("id", "ua")
    client.on_error = hook = mock.Mock()
    locked = urllib.error.HTTPError(
        "https://api.trakt.tv/sync/history", 423, "Locked", Message(), None)
    opener = mock.Mock(**{"open.side_effect": locked})

    with mock.patch("lib.trakt.client.urllib.request.build_opener", return_value=opener):
        assert client.request("POST", "/sync/history", {}) is None
    hook.assert_called_once_with(423)


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
