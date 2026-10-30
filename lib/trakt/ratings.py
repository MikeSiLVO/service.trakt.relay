"""The user's Trakt ratings, kept in Relay's database until Trakt reports a change."""

import sqlite3
from contextlib import contextmanager
from typing import Dict, Generator, Iterable, List, Optional, Set, Tuple

CREATE_RATINGS = (
    "CREATE TABLE IF NOT EXISTS ratings ("
    "  kind TEXT NOT NULL,"
    "  trakt_id INTEGER NOT NULL,"
    "  season INTEGER NOT NULL,"
    "  rating INTEGER NOT NULL,"
    "  rated_at TEXT,"
    "  PRIMARY KEY (kind, trakt_id, season)"
    ")"
)
CREATE_STAMPS = (
    "CREATE TABLE IF NOT EXISTS rating_stamps (kind TEXT PRIMARY KEY, rated_at TEXT NOT NULL)"
)
NO_SEASON = -1

Change = Tuple[str, int, int, Optional[int]]


def row_key(item: Dict) -> Optional[Tuple[str, int, int]]:
    """Key a Trakt rating item by kind, Trakt id and season, or None when it lacks an id."""
    kind = item.get("type") or ""
    if kind == "season":
        trakt_id = (item.get("show") or {}).get("ids", {}).get("trakt")
        season = (item.get("season") or {}).get("number")
        if trakt_id is None or season is None:
            return None
        return "seasons", trakt_id, season
    trakt_id = (item.get(kind) or {}).get("ids", {}).get("trakt")
    if trakt_id is None:
        return None
    return kind + "s", trakt_id, NO_SEASON


def rating_changes(items: Dict) -> Tuple[List[Change], Set[str]]:
    """Split ratings into per-item changes and the kinds with an item it can't key by Trakt id."""
    changes: List[Change] = []
    unkeyed: Set[str] = set()

    def add(kind: str, entry: Dict, trakt_id: Optional[int], season: int = NO_SEASON) -> None:
        if trakt_id is None:
            unkeyed.add(kind)
        else:
            changes.append((kind, trakt_id, season, entry.get("rating")))

    for movie in items.get("movies") or []:
        add("movies", movie, (movie.get("ids") or {}).get("trakt"))
    for episode in items.get("episodes") or []:
        add("episodes", episode, (episode.get("ids") or {}).get("trakt"))
    for show in items.get("shows") or []:
        trakt_id = (show.get("ids") or {}).get("trakt")
        seasons = show.get("seasons") or []
        if "rating" in show or not seasons:
            add("shows", show, trakt_id)
        for season in seasons:
            if season.get("episodes"):
                unkeyed.add("episodes")
            if "rating" in season or not season.get("episodes"):
                number = season.get("number")
                if number is None:
                    unkeyed.add("seasons")
                else:
                    add("seasons", season, trakt_id, number)
    return changes, unkeyed


class RatingsCache:
    """Ratings by Trakt id, each kind stamped with the Trakt change time it is current with."""

    def __init__(self, path: str) -> None:
        self.path = path
        with self._connect() as conn:
            conn.execute(CREATE_RATINGS)
            conn.execute(CREATE_STAMPS)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a connection for one transaction, committed on success, then closed."""
        conn = sqlite3.connect(self.path, timeout=60)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def stamp(self, kind: str) -> Optional[str]:
        """Trakt change time the kind's ratings are current with, or None when never stamped."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rated_at FROM rating_stamps WHERE kind = ?", (kind,)
            ).fetchone()
        return row[0] if row else None

    def set_stamp(self, kind: str, rated_at: str) -> None:
        """Set the Trakt change time the kind's ratings are current with."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rating_stamps (kind, rated_at) VALUES (?, ?)",
                (kind, rated_at),
            )

    def replace(self, kind: str, items: Iterable[Dict], rated_at: str) -> None:
        """Replace every rating of the kind with the fetched items and stamp it."""
        rows = []
        for item in items:
            key = row_key(item)
            if key and key[0] == kind and item.get("rating"):
                rows.append(key + (item["rating"], item.get("rated_at")))
        with self._connect() as conn:
            conn.execute("DELETE FROM ratings WHERE kind = ?", (kind,))
            conn.executemany("INSERT OR REPLACE INTO ratings VALUES (?, ?, ?, ?, ?)", rows)
            conn.execute(
                "INSERT OR REPLACE INTO rating_stamps (kind, rated_at) VALUES (?, ?)",
                (kind, rated_at),
            )

    def get(self, kind: str, trakt_id: int, season: int = NO_SEASON) -> Dict:
        """Get the rating and its time for one item, or {} when unrated."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rating, rated_at FROM ratings"
                " WHERE kind = ? AND trakt_id = ? AND season = ?",
                (kind, trakt_id, season),
            ).fetchone()
        return {"rating": row[0], "rated_at": row[1]} if row else {}

    def put(self, kind: str, trakt_id: int, season: int, rating: Optional[int]) -> None:
        """Put one item's rating, or delete it when the rating is empty."""
        with self._connect() as conn:
            if rating:
                conn.execute(
                    "INSERT OR REPLACE INTO ratings VALUES (?, ?, ?, ?, NULL)",
                    (kind, trakt_id, season, rating),
                )
            else:
                conn.execute(
                    "DELETE FROM ratings WHERE kind = ? AND trakt_id = ? AND season = ?",
                    (kind, trakt_id, season),
                )
