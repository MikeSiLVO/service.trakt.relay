"""Helpers and test data shared by tests in more than one folder."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
HEAT = {"title": "Heat", "year": 1995, "ids": {"imdb": "tt0113277"}, "movieid": 7,
        "collected": 1, "watched": 1}


def load_fixture(name):
    """Load a JSON fixture from the fixtures folder."""
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class Text(str):
    """A progress string that takes any format arguments and stays as it is."""

    def __mod__(self, values):
        return self
