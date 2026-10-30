import os
import re
import xml.etree.ElementTree as ET
from unittest import mock

from lib.dialogs import auth, rating
from lib.kodi import utilities

SKIN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "skins", "Default")


def _parse(xml_file):
    """Parse a 1080i dialog XML into its control ids and the texture files it names."""
    root = ET.parse(os.path.join(SKIN, "1080i", xml_file)).getroot()
    ids = {int(c.get("id", "")) for c in root.iter("control") if c.get("id")}
    textures = {t.text for t in root.iter() if re.match("(alt|border)?texture", t.tag) and t.text}
    return ids, {t for t in textures if not t.startswith("$INFO")}


def test_rating_dialog_has_its_controls_and_textures():
    """The rating XML has a heart per rating, both labels the code fills and textures that exist."""
    ids, textures = _parse(rating.RATING_XML)
    assert set(rating.RATING_BUTTONS) | {rating.NAME_LABEL, rating.CHOICE_LABEL} <= ids
    assert textures and all(os.path.isfile(os.path.join(SKIN, "media", t)) for t in textures)


def test_auth_dialog_has_its_controls_and_textures():
    """The auth XML has both labels the code fills, its Settings button and textures that exist."""
    ids, textures = _parse(auth.AUTH_XML)
    assert {auth.INSTRUCTION_LABEL, auth.CODE_LABEL, auth.SETTINGS_BUTTON} <= ids
    assert textures and all(os.path.isfile(os.path.join(SKIN, "media", t)) for t in textures)


def test_both_themes_name_logos_that_exist():
    """The purple and the red theme each name a logo in the skin's media."""
    for red in (False, True):
        window = mock.Mock()
        with mock.patch.object(utilities, "get_setting_bool", return_value=red):
            utilities.set_theme(window)
        logo = dict(c.args for c in window.setProperty.call_args_list)["logo"]
        assert os.path.isfile(os.path.join(SKIN, "media", logo))
