import ast
import glob
import importlib.util
import os
import sysconfig

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUBS = sysconfig.get_paths()["purelib"]


def _stub(module):
    """Load a Kodistubs module apart from the mocked one the tests use, skipping when absent."""
    path = os.path.join(STUBS, module + ".py")
    if not os.path.exists(path):
        pytest.skip("Kodistubs not installed")
    spec = importlib.util.spec_from_file_location("kodistubs_" + module, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_every_callback_override_is_one_kodi_calls():
    """Each on* method on a Kodi subclass is a callback its Kodi base defines."""
    stubs = {name: _stub(name) for name in ("xbmc", "xbmcgui")}
    unknown = []
    for path in glob.glob(os.path.join(ROOT, "lib", "**", "*.py"), recursive=True):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for base in cls.bases:
                if not (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                        and base.value.id in stubs):
                    continue
                kodi_class = getattr(stubs[base.value.id], base.attr)
                unknown += ["%s.%s" % (cls.name, f.name) for f in cls.body
                            if isinstance(f, ast.FunctionDef) and f.name[:2] == "on"
                            and f.name[2:3].isupper() and not hasattr(kodi_class, f.name)]
    assert unknown == []
