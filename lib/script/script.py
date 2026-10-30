"""Script entry: sends the action and the focused item to the service, which does the work."""
from __future__ import annotations

import json
import sys

import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
EVENT = "event"
ITEM_ACTIONS = ("rate", "unrate", "startrewatch", "addtowatchlist")
RATE_STRINGS = {"movie": 32137, "tvshow": 32138, "season": 32149, "episode": 32139}


def _parse_args() -> dict:
    """Parse the script's key=value arguments, a sync when there are none."""
    if len(sys.argv) == 1:
        return {"action": "sync"}
    args = {}
    for item in sys.argv[1:]:
        key, sep, value = item.partition("=")
        if sep:
            args[key.strip().lower()] = value.strip()
    if "action" in args:
        args["action"] = args["action"].lower()
    return args


def _show_context_menu(dbtype: str) -> str | None:
    """Show the Trakt actions in Kodi's own context menu and return the chosen one, or None."""
    actions = []
    if dbtype in RATE_STRINGS:
        actions += [("addtowatchlist", 32136), ("rate", RATE_STRINGS[dbtype])]
    # with it off the next sync marks the episodes watched again
    if dbtype == "tvshow" and ADDON.getSettingBool("rewatch_aware_sync"):
        actions.append(("startrewatch", 32220))
    actions.append(("sync", 32143))

    index = xbmcgui.Dialog().contextmenu([ADDON.getLocalizedString(i) for _, i in actions])
    return actions[index][0] if index >= 0 else None


def send_event(data: dict) -> None:
    """Send an event to the running service as a Kodi notification."""
    xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "JSONRPC.NotifyAll",
        "params": {"sender": ADDON_ID, "message": EVENT, "data": data},
    }))


def main(args: dict | None = None) -> None:
    """Send the action given, from arguments or the context menu, with the focused item it needs."""
    args = args or _parse_args()
    if "action" not in args:
        xbmc.log("%s: [script] no action in %s" % (ADDON_ID, sys.argv), xbmc.LOGWARNING)
        return
    if args["action"] == "contextmenu":
        choice = _show_context_menu(xbmc.getInfoLabel("ListItem.DBTYPE"))
        if choice is None:
            return
        args["action"] = choice
    if args["action"] in ITEM_ACTIONS and "dbid" not in args and "remoteid" not in args:
        args["dbtype"] = xbmc.getInfoLabel("ListItem.DBTYPE")
        args["dbid"] = xbmc.getInfoLabel("ListItem.DBID")
    send_event(args)
