from lib.trakt import keys


def test_obfuscate():
    """A key obfuscates to its fixed list of numbers."""
    assert keys.obfuscate("test") == [54, 39, 49, 54]


def test_deobfuscate():
    """A list of numbers deobfuscates back to its key."""
    assert keys.deobfuscate([54, 39, 49, 54]) == "test"


def test_obfuscate_empty():
    """An empty key obfuscates to an empty list."""
    assert keys.obfuscate("") == []


def test_deobfuscate_empty():
    """Anything but a list deobfuscates to an empty key."""
    assert keys.deobfuscate("") == ""
    assert keys.deobfuscate(None) == ""
    assert keys.deobfuscate("not a list") == ""


def test_round_trip_gives_the_key_back():
    """Deobfuscating an obfuscated key gives the key back."""
    original = "Hello, World!"
    assert keys.deobfuscate(keys.obfuscate(original)) == original
