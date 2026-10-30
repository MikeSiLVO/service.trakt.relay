import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRINGS = os.path.join(ROOT, "resources", "language", "resource.language.en_gb", "strings.po")
PATTERNS = {
    "py": r"get_string\((\d+)\)",
    "xml": r"\$ADDON\[service\.trakt\.relay (\d+)\]|(?:label|help)=\"(\d+)\"",
}


def _used_ids():
    """Map every add-on string id the code, dialogs and settings use to the files using it."""
    used = {}
    files = glob.glob(os.path.join(ROOT, "lib", "**", "*.py"), recursive=True)
    files += glob.glob(os.path.join(ROOT, "resources", "**", "*.xml"), recursive=True)
    for path in files:
        pattern = PATTERNS[path.rsplit(".", 1)[1]]
        for match in re.finditer(pattern, open(path, encoding="utf-8").read()):
            used.setdefault(int(next(g for g in match.groups() if g)), set()).add(path)
    return used


def test_every_used_string_id_is_defined():
    """Every add-on string id in the code, dialogs and settings has an en_GB entry."""
    po = open(STRINGS, encoding="utf-8").read()
    defined = {int(i) for i in re.findall(r'msgctxt "#(\d+)"', po)}
    assert {i: files for i, files in _used_ids().items() if i not in defined} == {}


def _entries(path):
    """Map each msgctxt in a strings.po to its msgid and msgstr."""
    entries = {}
    for block in re.split(r"\n\s*\n", open(path, encoding="utf-8").read()):
        fields = {}
        for name in ("msgctxt", "msgid", "msgstr"):
            m = re.search(r'^%s ((?:".*"\n?)+)' % name, block + "\n", re.M)
            if m:
                fields[name] = json.loads('"' + "".join(re.findall(r'"(.*)"', m.group(1))) + '"')
        if "msgctxt" in fields:
            entries[fields["msgctxt"]] = (fields.get("msgid", ""), fields.get("msgstr", ""))
    return entries


def test_translations_keep_the_english_placeholders_in_order():
    """Every translation keeps the English %-placeholders in the same order."""
    english = {c: msgid for c, (msgid, _) in _entries(STRINGS).items()}
    placeholder = re.compile(r"%\.?\d*[sdif]")
    broken = []
    for path in glob.glob(os.path.join(ROOT, "resources", "language", "*", "strings.po")):
        for ctxt, (_, text) in _entries(path).items():
            if text and ctxt in english and \
                    placeholder.findall(text) != placeholder.findall(english[ctxt]):
                broken.append((os.path.basename(os.path.dirname(path)), ctxt))
    assert broken == []


def test_every_strings_file_is_in_id_order():
    """Each strings.po lists the named entries first, then the numbered ones in id order."""
    unordered = []
    for path in glob.glob(os.path.join(ROOT, "resources", "language", "*", "strings.po")):
        ctxts = list(_entries(path))
        numbered = [int(c[1:]) for c in ctxts if c.startswith("#")]
        named = [c for c in ctxts if not c.startswith("#")]
        if ctxts != named + ["#%d" % i for i in sorted(numbered)]:
            unordered.append(os.path.basename(os.path.dirname(path)))
    assert unordered == []


def test_every_language_records_the_current_english():
    """Each language file records the current English text for every string it lists."""
    english = {c: msgid for c, (msgid, _) in _entries(STRINGS).items()}
    stale = []
    for path in glob.glob(os.path.join(ROOT, "resources", "language", "*", "strings.po")):
        for ctxt, (msgid, _) in _entries(path).items():
            if ctxt in english and msgid != english[ctxt]:
                stale.append((os.path.basename(os.path.dirname(path)), ctxt))
    assert stale == []
