import ast
import glob
import json
import os
from unittest import mock

from lib.kodi import client
from tests.helpers import ROOT


def _kodi(reply):
    """Patch Kodi's JSON-RPC entry point to answer with the given reply."""
    xbmc = mock.Mock()
    xbmc.executeJSONRPC.return_value = json.dumps(reply)
    return mock.patch.object(client, "xbmc", xbmc)


def test_request_sends_a_call_and_returns_its_result():
    """A request sends one JSON-RPC 2.0 call and returns its result."""
    with _kodi({"jsonrpc": "2.0", "id": 1, "result": {"value": 3}}) as xbmc:
        assert client.request("Settings.GetSettingValue", {"setting": "x"}) == {"value": 3}
    sent = json.loads(xbmc.executeJSONRPC.call_args[0][0])
    assert sent == {"jsonrpc": "2.0", "method": "Settings.GetSettingValue",
                    "params": {"setting": "x"}, "id": 1}


def test_request_without_params_sends_empty_object():
    """A request without params sends an empty params object."""
    with _kodi({"jsonrpc": "2.0", "id": 1, "result": []}) as xbmc:
        client.request("Player.GetActivePlayers")
    assert json.loads(xbmc.executeJSONRPC.call_args[0][0])["params"] == {}


def test_request_logs_error_and_returns_none():
    """An error reply is logged with its method and returns None."""
    error = {"code": -32602, "message": "Invalid params."}
    with _kodi({"jsonrpc": "2.0", "id": 1, "error": error}), \
            mock.patch.object(client.logger, "warning") as warning:
        assert client.request("VideoLibrary.SetMovieDetails", {"movieid": 1}) is None
    assert "VideoLibrary.SetMovieDetails" in warning.call_args[0][0]


def test_batch_request_returns_results_in_input_order():
    """A batch returns each call's result in input order, None and a warning per error."""
    reply = [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid params."}},
        {"jsonrpc": "2.0", "id": 0, "result": "OK"},
        {"jsonrpc": "2.0", "id": 2, "result": "OK"},
    ]
    with _kodi(reply) as xbmc, mock.patch.object(client.logger, "warning") as warning:
        params = [{"a": 0}, {"a": 1}, {"a": 2}]
        results = client.batch_request("VideoLibrary.SetEpisodeDetails", params)
    assert results == ["OK", None, "OK"]
    assert warning.call_count == 1
    sent = json.loads(xbmc.executeJSONRPC.call_args[0][0])
    assert [(c["id"], c["params"]) for c in sent] == [(0, {"a": 0}), (1, {"a": 1}), (2, {"a": 2})]
    assert {c["method"] for c in sent} == {"VideoLibrary.SetEpisodeDetails"}


def test_batch_rejected_whole_returns_none_per_call():
    """A batch Kodi rejects whole returns None for every call, warned once."""
    with _kodi({"jsonrpc": "2.0", "id": None, "error": {"code": -32700}}), \
            mock.patch.object(client.logger, "warning") as warning:
        assert client.batch_request("VideoLibrary.SetMovieDetails", [{}, {}]) == [None, None]
    assert warning.call_count == 1


def test_batch_request_empty_sends_nothing():
    """An empty batch sends nothing to Kodi."""
    with _kodi([]) as xbmc:
        assert client.batch_request("VideoLibrary.SetMovieDetails", []) == []
    assert not xbmc.executeJSONRPC.called


def test_addon_made_by_client_settings_and_script():
    """Only the client, the settings reload and the client-free script entry make an Addon."""
    paths = glob.glob(os.path.join(ROOT, "*.py"))
    paths += glob.glob(os.path.join(ROOT, "lib", "**", "*.py"), recursive=True)
    makers = []
    for path in paths:
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "Addon":
                makers.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    assert sorted(makers) == ["lib/kodi/client.py", "lib/kodi/settings.py", "lib/script/script.py"]
