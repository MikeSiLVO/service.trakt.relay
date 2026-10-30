import json
import subprocess
import sys
from unittest import mock

from lib.script import script
from tests.helpers import ROOT

IMPORT_PROBE = """
import sys, types

class Stub(types.ModuleType):
    def __getattr__(self, name):
        return lambda *args, **kwargs: Stub(name)

for name in ("xbmc", "xbmcaddon", "xbmcgui"):
    sys.modules[name] = Stub(name)
before = set(sys.modules)
import lib.script.script
print(" ".join(sorted(set(sys.modules) - before)))
"""


def test_script_entry_loads_no_other_lib_module():
    """The script entry loads no other lib module, nor typing or logging."""
    loaded = subprocess.run([sys.executable, "-c", IMPORT_PROBE], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.split()
    assert [name for name in loaded if name.startswith("lib")] == [
        "lib", "lib.script", "lib.script.script"]
    assert "typing" not in loaded and "logging" not in loaded


def _main(args=None, argv=("script.py",), dbtype="movie", dbid="5", choice=0):
    """Run the script entry with Kodi mocked; return the events it sent."""
    labels = {"ListItem.DBTYPE": dbtype, "ListItem.DBID": dbid}
    with mock.patch.object(script.sys, "argv", list(argv)), \
            mock.patch.object(script.xbmc, "getInfoLabel", side_effect=labels.get), \
            mock.patch.object(script.xbmcgui, "Dialog") as dialog, \
            mock.patch.object(script, "send_event") as send_event:
        dialog.return_value.contextmenu.return_value = choice
        script.main(args)
    return [call.args[0] for call in send_event.call_args_list]


def test_bare_run_syncs():
    """Running the script with no arguments starts a sync."""
    assert _main() == [{"action": "sync"}]


def test_arguments_are_read_case_blind():
    """Argument keys and the action are lowercased, and an argument without = is skipped."""
    events = _main(argv=("script.py", " Action=RATE", "media_type=movie", "remoteid=tt0147800",
                         "junk"))
    assert events == [{"action": "rate", "media_type": "movie", "remoteid": "tt0147800"}]


def test_item_action_carries_the_focused_item():
    """An action on an item, with none named in the arguments, sends the focused DBTYPE and DBID."""
    events = _main(argv=("script.py", "action=addtowatchlist"), dbtype="season", dbid="9")
    assert events == [{"action": "addtowatchlist", "dbtype": "season", "dbid": "9"}]


def test_context_menu_sends_the_chosen_action():
    """The context menu lists a show's actions and sends the chosen one with the show."""
    events = _main({"action": "contextmenu"}, dbtype="tvshow", choice=2)
    assert events == [{"action": "startrewatch", "dbtype": "tvshow", "dbid": "5"}]


def test_movie_context_menu_leaves_out_the_rewatch():
    """A movie's context menu leaves out the rewatch, which acts on a whole show."""
    assert _main({"action": "contextmenu"}, dbtype="movie", choice=2) == [{"action": "sync"}]


def test_show_context_menu_leaves_out_the_rewatch_while_rewatch_sync_is_off():
    """A show's context menu offers no rewatch while rewatch sync is off."""
    with mock.patch.object(script.ADDON, "getSettingBool", return_value=False):
        events = _main({"action": "contextmenu"}, dbtype="tvshow", choice=2)
    assert events == [{"action": "sync"}]


def test_closed_context_menu_sends_nothing():
    """Closing the context menu without a choice sends no event."""
    assert _main({"action": "contextmenu"}, choice=-1) == []


def test_arguments_without_an_action_send_nothing():
    """Arguments that name no action send no event."""
    assert _main(argv=("script.py", "media_type=movie")) == []


def test_send_event_notifies_the_service():
    """Sending an event broadcasts it as a Kodi notification from Relay."""
    with mock.patch.object(script.xbmc, "executeJSONRPC") as execute:
        script.send_event({"action": "sync"})
    call = json.loads(execute.call_args.args[0])
    assert call["method"] == "JSONRPC.NotifyAll"
    assert call["params"] == {"sender": script.ADDON_ID, "message": script.EVENT,
                              "data": {"action": "sync"}}
