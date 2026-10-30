"""Failed stops kept in Relay's database for retry, and the playing item for crash recovery."""
import sqlite3
import logging
import threading
from json import loads, dumps
from _thread import get_ident

from lib.kodi.utilities import database_path

logger = logging.getLogger(__name__)


MAX_RETRIES = 50


class ScrobbleQueue:
    """Failed stops kept for retry as history adds, plus the playing item for crash recovery."""

    _create = (
        "CREATE TABLE IF NOT EXISTS failed_scrobbles ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  media_type TEXT NOT NULL,"
        "  media_info TEXT NOT NULL,"
        "  show_info TEXT,"
        "  watched_at TEXT NOT NULL,"
        "  progress REAL NOT NULL,"
        "  created_at REAL NOT NULL,"
        "  retry_count INTEGER DEFAULT 0,"
        "  dedupe_key TEXT"
        ")"
    )
    _create_active = (
        "CREATE TABLE IF NOT EXISTS active_playback ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  media_type TEXT NOT NULL,"
        "  dbid INTEGER NOT NULL,"
        "  playcount INTEGER NOT NULL,"
        "  media_info TEXT NOT NULL,"
        "  show_info TEXT"
        ")"
    )

    def __init__(self):
        self.path = database_path()
        self._connection_cache = {}
        with self._connection() as conn:
            conn.execute(self._create)
            conn.execute(self._create_active)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(failed_scrobbles)").fetchall()]
            if "dedupe_key" not in cols:
                conn.execute("ALTER TABLE failed_scrobbles ADD COLUMN dedupe_key TEXT")

    def _connection(self):
        """This thread's connection, closing those of threads that have ended."""
        tid = get_ident()
        alive_tids = {t.ident for t in threading.enumerate()}
        stale_tids = [t for t in self._connection_cache if t not in alive_tids]
        for stale_tid in stale_tids:
            try:
                self._connection_cache[stale_tid].close()
            except Exception:
                pass
            del self._connection_cache[stale_tid]
        if tid not in self._connection_cache:
            self._connection_cache[tid] = sqlite3.Connection(self.path, timeout=60)
        return self._connection_cache[tid]

    def add(self, media_type, media_info, show_info, progress, watched_at):
        """Add a failed stop for retry, unless it lacks a usable id or repeats in 10 minutes."""
        if not self._has_usable_id(media_type, media_info, show_info):
            logger.warning(
                "Refusing to queue %s scrobble with no usable IDs" % media_type
            )
            return
        dedupe_key = self._dedupe_key(media_type, media_info, show_info)
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM failed_scrobbles "
                "WHERE media_type = ? AND dedupe_key = ? "
                "AND created_at > strftime('%s', 'now') - 600",
                (media_type, dedupe_key),
            ).fetchone()
            if existing:
                logger.debug(
                    "Skipping duplicate %s scrobble queue (key=%s, existing id=%d)"
                    % (media_type, dedupe_key, existing[0])
                )
                return
            conn.execute(
                "INSERT INTO failed_scrobbles "
                "(media_type, media_info, show_info, watched_at, progress, created_at, dedupe_key) "
                "VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'), ?)",
                (
                    media_type,
                    dumps(media_info),
                    dumps(show_info) if show_info else None,
                    watched_at,
                    progress,
                    dedupe_key,
                ),
            )
        logger.info("Queued failed %s scrobble for retry (key=%s)" % (media_type, dedupe_key))

    @staticmethod
    def _has_usable_id(media_type, media_info, show_info):
        """True when Trakt can match the item by an id, or by title and year."""
        if media_type == "movie":
            ids = (media_info or {}).get("ids") or {}
            if any(ids.get(k) for k in ("trakt", "imdb", "tmdb", "slug")):
                return True
            return bool(media_info.get("title") and media_info.get("year"))
        if media_type == "episode":
            if media_info.get("season") is None or media_info.get("number") is None:
                return False
            ids = (show_info or {}).get("ids") or {}
            if any(ids.get(k) for k in ("trakt", "imdb", "tmdb", "tvdb", "slug")):
                return True
            return bool(show_info and show_info.get("title") and show_info.get("year"))
        return False

    @staticmethod
    def _dedupe_key(media_type, media_info, show_info):
        """Key identifying the item, for spotting a repeated stop."""
        if media_type == "movie":
            ids = (media_info or {}).get("ids") or {}
            for k in ("trakt", "imdb", "tmdb", "slug"):
                if ids.get(k):
                    return "movie:%s:%s" % (k, ids[k])
            return "movie:title:%s:%s" % (media_info.get("title"), media_info.get("year"))
        ids = (show_info or {}).get("ids") or {}
        season = media_info.get("season")
        number = media_info.get("number")
        for k in ("trakt", "imdb", "tmdb", "tvdb", "slug"):
            if ids.get(k):
                return "episode:%s:%s:s%se%s" % (k, ids[k], season, number)
        return "episode:title:%s:%s:s%se%s" % (
            (show_info or {}).get("title"),
            (show_info or {}).get("year"),
            season,
            number,
        )

    def get_pending(self):
        """Get every queued stop, oldest first."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, media_type, media_info, show_info, watched_at, progress, retry_count "
                "FROM failed_scrobbles ORDER BY id"
            ).fetchall()
        return [
            {
                "id": r[0],
                "media_type": r[1],
                "media_info": loads(r[2]),
                "show_info": loads(r[3]) if r[3] else None,
                "watched_at": r[4],
                "progress": r[5],
                "retry_count": r[6],
            }
            for r in rows
        ]

    def remove(self, row_id):
        """Remove a queued stop, whatever its retry count."""
        with self._connection() as conn:
            conn.execute("DELETE FROM failed_scrobbles WHERE id = ?", (row_id,))

    def increment_retry(self, row_id):
        """Increment a stop's retry count, dropping the stop past 50."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE failed_scrobbles SET retry_count = retry_count + 1 WHERE id = ?",
                (row_id,),
            )
            row = conn.execute(
                "SELECT retry_count FROM failed_scrobbles WHERE id = ?", (row_id,)
            ).fetchone()
            if row and row[0] > MAX_RETRIES:
                conn.execute(
                    "DELETE FROM failed_scrobbles WHERE id = ?", (row_id,)
                )
                logger.warning(
                    "Dropped scrobble after %d retries" % MAX_RETRIES
                )

    def set_active(self, media_type, dbid, playcount, media_info, show_info):
        """Record the library item now playing, replacing any earlier one."""
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_playback "
                "(id, media_type, dbid, playcount, media_info, show_info) "
                "VALUES (1, ?, ?, ?, ?, ?)",
                (
                    media_type,
                    dbid,
                    playcount,
                    dumps(media_info),
                    dumps(show_info) if show_info else None,
                ),
            )

    def clear_active(self):
        """Clear the playing item, leaving nothing to recover at startup."""
        with self._connection() as conn:
            conn.execute("DELETE FROM active_playback")

    def pop_active(self):
        """Take the item left playing by a session that never ended it, or None."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT media_type, dbid, playcount, media_info, show_info "
                "FROM active_playback"
            ).fetchone()
            conn.execute("DELETE FROM active_playback")
        if not row:
            return None
        return {
            "media_type": row[0],
            "dbid": row[1],
            "playcount": row[2],
            "media_info": loads(row[3]),
            "show_info": loads(row[4]) if row[4] else None,
        }

    def __len__(self):
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM failed_scrobbles"
            ).fetchone()[0]
