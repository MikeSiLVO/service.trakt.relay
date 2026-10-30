import xbmc
import xbmcaddon
import json
import logging
from typing import Any, Dict, List, Optional

# Kodi reloads only the first Addon() per id on a settings save
ADDON = xbmcaddon.Addon()

logger = logging.getLogger(__name__)


def _result(method: str, response: Dict) -> Any:
    """Unwrap one JSON-RPC response, logging an error reply and returning None for it."""
    if "error" in response:
        logger.warning("JSON-RPC error for %s: %s" % (method, response["error"]))
        return None
    return response.get("result")


def request(method: str, params: Optional[Dict] = None) -> Any:
    """Send a JSON-RPC call to Kodi and return its result, or None on an error."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}
    return _result(method, json.loads(xbmc.executeJSONRPC(json.dumps(payload))))


def batch_request(method: str, params_list: List[Dict]) -> List[Any]:
    """Send one call per params dict in a single batch; results in input order, None per error."""
    if not params_list:
        return []
    payload = [
        {"jsonrpc": "2.0", "method": method, "params": params, "id": i}
        for i, params in enumerate(params_list)
    ]
    responses = json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
    if not isinstance(responses, list):
        _result(method, responses)
        return [None] * len(params_list)
    by_id = {r.get("id"): r for r in responses if isinstance(r, dict)}
    return [_result(method, by_id.get(i, {})) for i in range(len(params_list))]
