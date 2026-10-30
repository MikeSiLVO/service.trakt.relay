import logging
import os
import threading
import time
from json import dumps, loads
from typing import Any, Dict, Iterable, List, Optional
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from lib.kodi.client import ADDON
from lib.kodi.utilities import database_path, get_proxy_url, notify

from lib.trakt.client import TraktClient
from lib.trakt.keys import CLIENT_ID, deobfuscate
from lib.trakt.objects import TraktObject, TraktSeason
from lib.trakt.ratings import NO_SEASON, RatingsCache, rating_changes
from lib.kodi.settings import get_setting, get_string, set_setting

logger = logging.getLogger(__name__)

SEARCH_TYPES = ("movie", "show", "episode", "person", "list")
RATINGS_PAGE_SIZE = 250
RATINGS_WORKERS = 4


class TraktAPI:
    """Trakt API calls for Relay, holding the OAuth token and refreshing it on a 401."""
    authorization: Optional[Dict] = None
    client: TraktClient
    ratings: RatingsCache
    _refresh_lock = threading.Lock()

    def __init__(self) -> None:
        logger.info("Initializing.")

        proxy_url = get_proxy_url()

        client_id = os.environ.get("TRAKT_CLIENT_ID") or deobfuscate(CLIENT_ID)
        user_agent = "Kodi service.trakt.relay/%s" % ADDON.getAddonInfo("version")
        self.client = TraktClient(client_id, user_agent, proxy_url)
        self.client.on_error = self._check_error
        self._locked = threading.Event()
        self.ratings = RatingsCache(database_path())

        if get_setting("authorization"):
            self.authorization = loads(get_setting("authorization"))

    @property
    def connected(self) -> bool:
        """True when an OAuth token is held."""
        return bool(self.authorization)

    def _exiting(self) -> bool:
        """True when Kodi is exiting, by the monitor the service hands the client."""
        return self.client.monitor is not None and self.client.monitor.abortRequested()

    def _check_error(self, code: int) -> None:
        """Check a Trakt error status, notifying once a session that the account is locked."""
        if code == 423 and not self._locked.is_set():
            self._locked.set()
            logger.warning("Trakt reports the account locked or deactivated")
            notify(ADDON.getAddonInfo("name"), get_string(32227))

    def request_device_code(self) -> Optional[Dict]:
        """Request a device code for the user to enter, or None when Trakt can't be reached."""
        return self.client.request(
            "POST", "/oauth/device/code", {"client_id": self.client.client_id}, timeout=90
        )

    def wait_for_token(self, code: Dict, stop: threading.Event) -> bool:
        """Wait for the token, save it and the username; False if refused, expired or stopped."""
        deadline = time.time() + code.get("expires_in", 600)
        interval = code.get("interval", 5)
        while not stop.is_set() and time.time() < deadline:
            token, error_code = self.client.request(
                "POST",
                "/oauth/device/token",
                {
                    "code": code.get("device_code"),
                    "client_id": self.client.client_id,
                },
                timeout=90,
                include_error_code=True,
            )
            if token and token.get("access_token"):
                self._save_token(token)
                logger.debug("Authentication complete: token received")
                self.update_user()
                return True
            if error_code in (404, 409, 410, 418):
                logger.debug("Device authentication stopped with status %s" % error_code)
                return False
            if error_code == 429:
                interval += 1
            stop.wait(interval)
        return False

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        authorized: bool = False,
        timeout: int = 30,
        include_headers: bool = False,
        include_error_code: bool = False,
    ) -> Any:
        """Send a request, with status or headers if asked; on a 401 refresh and resend once."""
        authorization = self.authorization if authorized else None
        try:
            return self.client.request(
                method,
                path,
                body,
                authorization=authorization,
                timeout=timeout,
                include_headers=include_headers,
                include_error_code=include_error_code,
            )
        except urllib.error.HTTPError as exc:
            if not authorization or not self._refresh_token(authorization):
                logger.debug("Trakt request failed: %s %s -> %s" % (method, path, exc.code))
                return None
        try:
            return self.client.request(
                method,
                path,
                body,
                authorization=self.authorization,
                timeout=timeout,
                include_headers=include_headers,
                include_error_code=include_error_code,
            )
        except urllib.error.HTTPError as exc:
            logger.debug("Trakt request failed after refresh: %s %s -> %s"
                         % (method, path, exc.code))
            return None

    def _refresh_token(self, stale: Dict) -> bool:
        """Refresh token unless another thread did; refusal drops it and username. True if fresh."""
        with self._refresh_lock:
            if self.authorization != stale:
                return self.authorization is not None
            refreshed, error_code = self.client.request(
                "POST",
                "/oauth/token",
                {
                    "refresh_token": stale.get("refresh_token"),
                    "client_id": self.client.client_id,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "refresh_token",
                },
                timeout=90,
                include_error_code=True,
            )
            # 400 is invalid_grant, a dead token; any other failure may be transient
            if error_code == 400:
                logger.warning("Trakt refused the token refresh; connect again")
                self.authorization = None
                set_setting("authorization", "")
                set_setting("user", "")
                return False
            if not refreshed:
                return False
            self._save_token(refreshed)
            logger.debug("Token refreshed")
            return True

    def _get(
        self, path: str, authorized: bool = False, timeout: int = 30, include_headers: bool = False
    ) -> Any:
        """GET from Trakt, signed with the user's token only when asked."""
        return self._request(
            "GET", path, authorized=authorized, timeout=timeout, include_headers=include_headers
        )

    def _post(self, path: str, body: Dict, authorized: bool = True, timeout: int = 30) -> Any:
        """POST to Trakt, signed with the user's token unless told not to."""
        return self._request("POST", path, body=body, authorized=authorized, timeout=timeout)

    def _get_all_pages(
        self,
        path: str,
        authorized: bool = False,
        timeout: int = 90,
        limit: int = 100,
        params: Optional[Dict] = None,
    ) -> Optional[List]:
        """GET every page of a paginated endpoint as one list; None on a failed page or exit."""
        results = []
        page = 1
        page_count = 1
        while page <= page_count:
            if self._exiting():
                return None
            query = {"page": page, "limit": limit}
            if params:
                query.update(params)
            page_path = self.client.build_path(path, query)
            response = self._get(
                page_path,
                authorized=authorized,
                timeout=timeout,
                include_headers=True,
            )
            if not response:
                return None

            data, headers = response
            # page count header overstates some sync endpoints
            if not data:
                break
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)

            try:
                page_count = int(headers.get("X-Pagination-Page-Count", page_count))
            except (TypeError, ValueError):
                page_count = page
            page += 1

        return results

    def _get_all_ratings(self, media_type: str) -> Optional[List]:
        """Get the user's ratings of one type, four pages at once; None on a failed page or exit."""
        path = "/users/me/ratings/%s" % media_type

        def page(number: int) -> Any:
            if self._exiting():
                return None
            query = {"page": number, "limit": RATINGS_PAGE_SIZE}
            return self._get(
                self.client.build_path(path, query),
                authorized=True,
                timeout=90,
                include_headers=True,
            )

        first = page(1)
        if not first:
            return None
        items, headers = first
        try:
            page_count = int(headers.get("X-Pagination-Page-Count", 1))
        except (TypeError, ValueError):
            page_count = 1
        results = list(items or [])
        with ThreadPoolExecutor(RATINGS_WORKERS) as pool:
            for response in pool.map(page, range(2, page_count + 1)):
                if not response:
                    return None
                results.extend(response[0] or [])
        return results

    def _merge_movie(self, store: Dict, item: Dict, metadata_keys: Iterable[str]) -> None:
        """Merge a sync movie by Trakt id, keeping the listed item fields and normalizing it."""
        media = dict(item.get("movie") or item)
        for key in metadata_keys:
            if key in item:
                media[key] = item[key]
        ids = media.get("ids") or {}
        key = ids.get("trakt") or media.get("title") or len(store)
        existing = store.get(key)
        if existing:
            merged = existing.to_dict()
            merged.update(media)
        else:
            merged = media
        self._normalize_video(merged, media)
        store[key] = TraktObject(merged)

    def _normalize_video(self, merged: Dict, incoming: Dict) -> None:
        """Normalize a video's watched and collected flags and fill its missing fields."""
        if "plays" in incoming:
            merged["watched"] = 1 if incoming["plays"] > 0 else 0
        elif "watched" not in merged:
            merged["watched"] = 1 if merged.get("plays", 0) > 0 else 0
        if "plays" not in merged:
            merged["plays"] = 0
        if "collected_at" in incoming:
            merged["collected"] = 1
        elif "collected" not in merged:
            merged["collected"] = 1 if merged.get("collected_at") else 0
        merged.setdefault("in_watchlist", 0)
        merged.setdefault("progress", None)
        merged.setdefault("last_watched_at", None)
        merged.setdefault("collected_at", None)
        merged.setdefault("paused_at", None)

    def _merge_show(
        self, store: Dict, item: Dict, metadata_keys: Iterable[str]
    ) -> None:
        """Merge a sync show, season or episode item under its show by Trakt id."""
        show = dict(item.get("show") or item)
        incoming_seasons = item.get("seasons", show.get("seasons", []))
        if item.get("season"):
            season = dict(item["season"])
            for key in metadata_keys:
                if key in item:
                    season[key] = item[key]
            incoming_seasons = [season]
        elif item.get("episode"):
            episode = dict(item["episode"])
            for key in metadata_keys:
                if key in item:
                    episode[key] = item[key]
            incoming_seasons = [
                {"number": episode.get("season"), "episodes": [episode]}
            ]
        else:
            for key in metadata_keys:
                if key in item:
                    show[key] = item[key]
        ids = show.get("ids") or {}
        key = ids.get("trakt") or show.get("title") or len(store)
        existing = store.get(key)
        merged = existing.to_dict() if existing else {}
        merged.update(show)
        merged["seasons"] = self._merge_seasons(
            merged.get("seasons", []), incoming_seasons
        )
        store[key] = TraktObject(merged)

    def _merge_seasons(self, existing: List, incoming: List) -> List:
        """Merge two season lists by number, merging each season's episodes too."""
        seasons = {season.get("number"): dict(season) for season in existing or []}
        for season in incoming or []:
            number = season.get("number")
            merged = seasons.get(number, {"number": number})
            merged["episodes"] = self._merge_episodes(
                merged.get("episodes", []), season.get("episodes", [])
            )
            for key, value in season.items():
                if key != "episodes":
                    merged[key] = value
            seasons[number] = merged
        return list(seasons.values())

    def _merge_episodes(self, existing: List, incoming: List) -> List:
        """Merge two episode lists by number, normalizing each result."""
        episodes = {episode.get("number"): dict(episode) for episode in existing or []}
        for episode in incoming or []:
            number = episode.get("number")
            merged = episodes.get(number, {"number": number})
            merged.update(episode)
            self._normalize_video(merged, episode)
            episodes[number] = merged
        return list(episodes.values())

    def _save_token(self, token: Dict) -> None:
        """Save new OAuth credentials for later calls and across restarts."""
        self.authorization = token
        set_setting("authorization", dumps(token))

    def update_user(self) -> None:
        """Update the stored username from the account, or empty it when it can't be read."""
        user = self.get_user()
        if user and "user" in user:
            set_setting("user", user["user"]["username"])
        else:
            set_setting("user", "")

    def scrobble_episode(
        self, show: Dict, episode: Dict, progress: float, status: str
    ) -> Optional[Dict]:
        """Scrobble an episode start, pause or stop; anything else returns None."""
        if status not in ("start", "pause", "stop"):
            logger.debug("scrobble() Bad scrobble status")
            return None
        return self._post_scrobble(
            status, {"show": show, "episode": episode, "progress": progress}
        )

    def scrobble_movie(self, movie: Dict, progress: float, status: str) -> Optional[Dict]:
        """Scrobble a movie start, pause or stop; anything else returns None."""
        if status not in ("start", "pause", "stop"):
            logger.debug("scrobble() Bad scrobble status")
            return None
        return self._post_scrobble(status, {"movie": movie, "progress": progress})

    def _post_scrobble(self, status: str, body: Dict) -> Optional[Dict]:
        """Post a scrobble, with Trakt's 409 for an item it just scrobbled read as a duplicate."""
        reply = self._request(
            "POST", "/scrobble/%s" % status, body=body, authorized=True, include_error_code=True
        )
        data, error_code = reply or (None, None)
        if error_code == 409:
            return {"duplicate": True}
        return data

    def get_collected_shows(self, shows: Dict) -> Optional[Dict]:
        """Get the user's collected shows into the store; None if a page fails."""
        items = self._get_all_pages("/sync/collection/shows", authorized=True, timeout=90)
        if items is None:
            return None
        for item in items:
            self._merge_show(shows, item, ("collected_at",))
        return shows

    def get_collected_movies(self, movies: Dict) -> Optional[Dict]:
        """Get the user's collected movies into the store; None if a page fails."""
        items = self._get_all_pages("/sync/collection/movies", authorized=True, timeout=90)
        if items is None:
            return None
        for item in items:
            self._merge_movie(movies, item, ("collected_at",))
        return movies

    def get_watched_shows(self, shows: Dict) -> Optional[Dict]:
        """Get the user's watched shows with episode plays into the store; None if a page fails."""
        # without extended=progress Trakt returns show-level plays only, no episodes
        items = self._get_all_pages(
            "/sync/watched/shows", authorized=True, timeout=90, params={"extended": "progress"}
        )
        if items is None:
            return None
        for item in items:
            self._merge_show(
                shows, item, ("plays", "last_watched_at", "last_updated_at", "reset_at")
            )
        return shows

    def get_watched_movies(self, movies: Dict) -> Optional[Dict]:
        """Get the user's watched movies into the store; None if a page fails."""
        items = self._get_all_pages("/sync/watched/movies", authorized=True, timeout=90)
        if items is None:
            return None
        for item in items:
            self._merge_movie(movies, item, ("plays", "last_watched_at", "last_updated_at"))
        return movies

    def get_rated_shows(self, shows: Dict) -> Optional[Dict]:
        """Get the user's rated shows into the store; None if a page fails."""
        items = self._get_all_ratings("shows")
        if items is None:
            return None
        for item in items:
            self._merge_show(shows, item, ("rated_at", "rating"))
        return shows

    def get_rated_episodes(self, shows: Dict) -> Optional[Dict]:
        """Get the user's rated episodes into the store; None if a page fails."""
        items = self._get_all_ratings("episodes")
        if items is None:
            return None
        for item in items:
            self._merge_show(shows, item, ("rated_at", "rating"))
        return shows

    def get_rated_movies(self, movies: Dict) -> Optional[Dict]:
        """Get the user's rated movies into the store; None if a page fails."""
        items = self._get_all_ratings("movies")
        if items is None:
            return None
        for item in items:
            self._merge_movie(movies, item, ("rated_at", "rating"))
        return movies

    def add_to_collection(self, items: Dict) -> Optional[Dict]:
        """Add to the collection, returning Trakt's counts or None on failure."""
        return self._post("/sync/collection", items)

    def remove_from_collection(self, items: Dict) -> Optional[Dict]:
        """Remove from the collection, returning Trakt's counts or None on failure."""
        return self._post("/sync/collection/remove", items)

    def add_to_history(self, items: Dict) -> Optional[Dict]:
        """Add to the watch history, returning Trakt's counts or None on failure."""
        return self._post("/sync/history", items)

    def get_history(self, media_type: str, start_at: str, end_at: str) -> Optional[List]:
        """Get the user's movie or episode plays between two UTC times; None if a page fails."""
        return self._get_all_pages("/sync/history/%ss" % media_type, authorized=True,
                                   params={"start_at": start_at, "end_at": end_at})

    def delete_playback_progress(self, playback_id: Any) -> Any:
        """Delete one paused item's entry from Trakt's playback progress."""
        return self._request(
            "DELETE",
            "/sync/playback/%s" % urllib.parse.quote(str(playback_id), safe=""),
            authorized=True,
        )

    def delete_item_playback_progress(self, media_type: str, trakt_id: Any) -> Any:
        """Delete an item's playback progress entry, False when none matches or the lookup fails."""
        try:
            if media_type == "movie":
                for item in self.get_movie_playback_progress() or []:
                    if dict(item.keys).get("trakt") == trakt_id:
                        logger.debug(
                            "Removing playback progress for movie trakt:%s (playback id: %s)"
                            % (trakt_id, item.id)
                        )
                        return self.delete_playback_progress(item.id)
            elif media_type == "episode":
                for show in self.get_episode_playback_progress() or []:
                    for episode in show.episodes.values():
                        if dict(episode.keys).get("trakt") == trakt_id:
                            logger.debug(
                                "Removing playback progress for episode trakt:%s (playback id: %s)"
                                % (trakt_id, episode.id)
                            )
                            return self.delete_playback_progress(episode.id)
        except Exception as ex:
            logger.debug("Failed to remove playback progress: %s" % str(ex))
        return False

    def add_to_watchlist(self, items: Dict) -> Optional[Dict]:
        """Add to the watchlist, returning Trakt's counts, the limit if full, or None on failure."""
        reply = self._request(
            "POST", "/sync/watchlist", body=items, authorized=True, include_error_code=True
        )
        data, error_code = reply or (None, None)
        if error_code == 420:
            limits = (self.get_user() or {}).get("limits") or {}
            limit = (limits.get("watchlist") or {}).get("item_count")
            return {"limit": limit} if limit else None
        return data

    def get_user_rating(
        self, media_type: str, trakt_id: Any, season: int = NO_SEASON
    ) -> Dict:
        """Get the user's rating of an item by Trakt id from the cache, refreshed on a change."""
        if not trakt_id:
            return {}
        kind = media_type + "s"
        self._refresh_ratings([kind])
        return self.ratings.get(kind, int(trakt_id), season)

    def _rated_at(self) -> Dict[str, str]:
        """Read Trakt's last rating change per kind, or {} when it can't be read."""
        activities = self.get_last_activities() or {}
        return {
            kind: value["rated_at"]
            for kind, value in activities.items()
            if isinstance(value, dict) and value.get("rated_at")
        }

    def _refresh_ratings(self, kinds: Iterable[str]) -> None:
        """Refresh each kind's ratings whose Trakt change time differs from the cached one."""
        current = self._rated_at()
        for kind in kinds:
            stored = self.ratings.stamp(kind)
            stamp = current.get(kind)
            if stored is not None and stamp in (None, stored):
                continue
            items = self._get_all_ratings(kind)
            if items is not None:
                self.ratings.replace(kind, items, stamp or "")

    def _write_ratings(self, path: str, items: Dict, remove: bool) -> Optional[Dict]:
        """Write ratings to Trakt and cache, restamping kinds whose items all key by Trakt id."""
        changes, unkeyed = rating_changes(items)
        keyed = {change[0] for change in changes} - unkeyed
        self._refresh_ratings(keyed)
        result = self._post(path, items)
        if result is None:
            return None
        for kind, trakt_id, season, rating in changes:
            self.ratings.put(kind, trakt_id, season, None if remove else rating)
        current = self._rated_at()
        for kind in keyed:
            if kind in current:
                self.ratings.set_stamp(kind, current[kind])
        return result

    def add_ratings(self, items: Dict) -> Optional[Dict]:
        """Add ratings on Trakt and locally, returning Trakt's counts or None on failure."""
        return self._write_ratings("/sync/ratings", items, remove=False)

    def remove_ratings(self, items: Dict) -> Optional[Dict]:
        """Remove ratings on Trakt and locally, returning Trakt's counts or None on failure."""
        return self._write_ratings("/sync/ratings/remove", items, remove=True)

    def get_movie_playback_progress(self) -> Optional[List[TraktObject]]:
        """Get the user's paused movies with progress and playback id; None if a page fails."""
        items = self._get_all_pages("/sync/playback/movies", authorized=True, timeout=90)
        if items is None:
            return None
        movies = []
        for item in items:
            movie = dict(item.get("movie") or {})
            for key in ("progress", "paused_at", "id"):
                if key in item:
                    movie[key] = item[key]
            movies.append(TraktObject(movie))

        return movies

    def get_episode_playback_progress(self) -> Optional[List[TraktObject]]:
        """Get the user's paused episodes, each wrapped in its show; None if a page fails."""
        items = self._get_all_pages("/sync/playback/episodes", authorized=True, timeout=90)
        if items is None:
            return None
        shows = []
        for item in items:
            show = dict(item.get("show") or {})
            episode = dict(item.get("episode") or {})
            for key in ("progress", "paused_at", "id"):
                if key in item:
                    episode[key] = item[key]
            episode.setdefault("season", episode.get("season") or item.get("season"))
            show["seasons"] = self._merge_seasons(
                show.get("seasons", []),
                [{"number": episode.get("season"), "episodes": [episode]}],
            )
            shows.append(TraktObject(show))

        return shows

    def get_movie_summary(
        self, movie_id: str, extended: Optional[str] = None
    ) -> TraktObject:
        """Get a movie's summary, or an empty object when the lookup fails."""
        path = "/movies/%s" % urllib.parse.quote(str(movie_id), safe="")
        path = self.client.build_path(path, {"extended": extended})
        result = self._get(path)
        if result is not None:
            result.setdefault("watched", False)
        return TraktObject(result or {})

    def start_rewatch(self, show_id: str) -> Optional[Dict]:
        """Start a rewatch of a show, returning Trakt's reply or None on failure."""
        return self._post(
            "/shows/%s/progress/watched/reset" % urllib.parse.quote(str(show_id), safe=""), {})

    def get_show_summary(self, show_id: str) -> TraktObject:
        """Get a show's summary, or an empty object when the lookup fails."""
        result = self._get("/shows/%s" % urllib.parse.quote(str(show_id), safe=""))
        if result is not None:
            result.setdefault("seasons", [])
        return TraktObject(result or {})

    def get_show_seasons(self, show_id: str) -> List:
        """Get a show's seasons with all their episodes."""
        result = self._get(
            "/shows/%s/seasons?extended=episodes"
            % urllib.parse.quote(str(show_id), safe=""),
            timeout=90,
        )
        return [TraktSeason(season) for season in result or []]

    def get_episode_summary(
        self, show_id: str, season: int, episode: int, extended: Optional[str] = None
    ) -> Any:
        """Get an episode's summary, or an empty object when the lookup fails."""
        path = "/shows/%s/seasons/%s/episodes/%s" % (
            urllib.parse.quote(str(show_id), safe=""),
            season,
            episode,
        )
        path = self.client.build_path(path, {"extended": extended})
        result = self._get(path)
        if result is not None:
            result.setdefault("season", season)
        return TraktObject(result or {})

    def search_by_id(self, item_id: str, id_type: str, search_type: str) -> Optional[List]:
        """Search Trakt for items of one search type carrying a Trakt, IMDb, TMDB or TVDB id."""
        result = self._get_all_pages(
            "/search/%s/%s"
            % (
                urllib.parse.quote(str(id_type), safe=""),
                urllib.parse.quote(str(item_id), safe=""),
            ),
            timeout=90,
            params={"type": search_type},
        )
        return self._wrap_search_results(result)

    def search_by_text(
        self, query: str, media_type: str, year: Optional[int]
    ) -> Optional[List]:
        """Search Trakt by text for one of its search types, optionally in one year."""
        if media_type not in SEARCH_TYPES:
            logger.debug("Skipping %s text search; Trakt can't search that type" % media_type)
            return None
        path = self.client.build_path(
            "/search/%s" % urllib.parse.quote(str(media_type), safe=""),
            {"query": query, "years": year},
        )
        return self._wrap_search_results(self._get_all_pages(path, timeout=90))

    def get_last_activities(self) -> Optional[Dict]:
        """Get the server-side change timestamps, used to skip syncs with nothing new."""
        return self._get("/sync/last_activities", authorized=True)

    def get_user(self) -> Optional[Dict]:
        """Get the account's settings, which carry its username."""
        return self._get("/users/settings", authorized=True)

    def _wrap_search_results(self, result: Any) -> Optional[List]:
        """Wrap search hits as objects, an episode linked to its show."""
        if not result:
            return None
        if not isinstance(result, list):
            result = [result]
        wrapped = []
        for item in result:
            if "episode" in item:
                show = TraktObject(item.get("show") or {})
                episode = dict(item.get("episode") or {})
                episode.setdefault("season", episode.get("season"))
                wrapped.append(TraktObject(episode, show=show))
            elif "show" in item:
                wrapped.append(TraktObject(item.get("show") or {}))
            elif "movie" in item:
                wrapped.append(TraktObject(item.get("movie") or {}))
            else:
                wrapped.append(TraktObject(item))
        return wrapped
