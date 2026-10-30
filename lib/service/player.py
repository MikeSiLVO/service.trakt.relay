"""Kodi player hooks that identify what plays and queue its events."""
import logging
import time
from typing import Any, Callable, Dict, Optional
import xbmc
import xbmcgui

from lib.trakt import shared
from lib.kodi.utilities import get_playing_details, is_excluded
from lib.kodi.client import request
from lib.kodi.settings import get_setting_int

logger = logging.getLogger(__name__)

START_DELAY = 2


class Player(xbmc.Player):
    """Kodi player that turns playback events into queued actions for the scrobbler."""
    _playing: bool = False
    playlist_index: Optional[int] = None
    _start_due: Optional[float] = None
    action: Callable[[Dict], None]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.action = kwargs["action"]
        logger.debug("Player hooks ready.")

    def onAVStarted(self) -> None:
        """Hold the new video's start until the start offset passes, 2 seconds at the least."""
        logger.info("Playback started")
        offset = get_setting_int("scrobble_start_offset") * 60
        self._start_due = time.time() + max(offset, START_DELAY)

    def queue_due_start(self) -> None:
        """Queue the held start once it falls due, unless the video stopped first."""
        if self._start_due is None or time.time() < self._start_due:
            return
        self._start_due = None
        if not self.isPlayingVideo():
            logger.debug("Playback stopped before reaching the scrobble offset")
            return
        self._queue_start()

    def _queue_start(self) -> None:
        """Queue the playing video's start unless it's excluded, first ending one skipped from."""
        result = self._playing_item()
        if result is None:
            return
        data = identify(result)
        if not data:
            return
        self._follow_playlist()
        self._playing = True
        self.action(data)

    def _playing_item(self) -> Optional[Dict]:
        """Get the playing item from Kodi, or None when it is excluded or playback stopped."""
        result = get_playing_item()
        if result is None:
            return None
        try:
            filename = self.getPlayingFile()
        except Exception:
            logger.debug("Exception trying to get playing filename, player suddenly stopped.")
            return None
        if "script.trakt.exclude" in (result["item"].get("customproperties") or {}):
            logger.debug("'%s' has exclusion property, ignoring." % filename)
            return None
        # a plugin item plays its resolved URL; only its listed path still says plugin://
        path = xbmc.getInfoLabel("Player.FilenameAndPath")
        if is_excluded(filename) or (path and path != filename and is_excluded(path)):
            logger.debug("'%s' is in exclusion settings, ignoring." % (path or filename))
            return None
        return result

    def _follow_playlist(self) -> None:
        """Track the playlist position, ending the previous video when the user skipped from it."""
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        size = playlist.size()
        if size <= 1:
            return
        position = playlist.getposition()
        if self.playlist_index is not None:
            logger.debug("User skipped to another playlist video, forcing playback ended event.")
            self.onPlayBackEnded()
        self.playlist_index = position
        logger.debug("Playlist contains %d item(s), on item %d" % (size, position + 1))

    def onPlayBackEnded(self) -> None:
        """Clear the script.trakt window properties and queue the end of a tracked video."""
        xbmcgui.Window(10000).clearProperty("script.trakt.ids")
        xbmcgui.Window(10000).clearProperty("script.trakt.paused")
        if self._playing:
            logger.debug("Playback ended, still playing video: %s" % self.isPlayingVideo())
            self._playing = False
            self.playlist_index = None
            data = {"action": "ended"}
            self.action(data)

    def onPlayBackStopped(self) -> None:
        """Clear the script.trakt window properties and queue the stop of a tracked video."""
        xbmcgui.Window(10000).clearProperty("script.trakt.ids")
        xbmcgui.Window(10000).clearProperty("script.trakt.paused")
        if self._playing:
            logger.debug(
                "Playback stopped, still playing video: %s" % self.isPlayingVideo()
            )
            self._playing = False
            self.playlist_index = None
            data = {"action": "stopped"}
            self.action(data)

    def onPlayBackPaused(self) -> None:
        """Queue a pause of a tracked video."""
        if self._playing:
            logger.debug(
                "Playback paused, still playing video: %s" % self.isPlayingVideo()
            )
            data = {"action": "paused"}
            self.action(data)

    def onPlayBackResumed(self) -> None:
        """Queue a resume of a tracked video."""
        if self._playing:
            logger.debug(
                "Playback resumed, still playing video: %s" % self.isPlayingVideo()
            )
            data = {"action": "resumed"}
            self.action(data)

    def onPlayBackSeek(self, time: int, offset: int) -> None:
        """Queue a seek in a tracked video."""
        if self._playing:
            logger.debug(
                "Seek to %s, offset %s, still playing video: %s"
                % (str(time), str(offset), self.isPlayingVideo())
            )
            data = {"action": "seek", "time": time, "offset": offset}
            self.action(data)

    def onPlayBackSeekChapter(self, chapter: int) -> None:
        """Queue a chapter seek in a tracked video."""
        if self._playing:
            logger.debug(
                "Chapter seek to %s, still playing video: %s"
                % (str(chapter), self.isPlayingVideo())
            )
            data = {"action": "seekchapter", "chapter": chapter}
            self.action(data)


def get_playing_item() -> Optional[Dict]:
    """Get Kodi's reply naming the playing item, or None when nothing plays."""
    players = request("Player.GetActivePlayers")
    logger.debug("Active players: %s" % players)
    if not players:
        return None
    result = request(
        "Player.GetItem",
        {"playerid": int(players[0]["playerid"]), "properties": ["customproperties"]},
    )
    logger.debug("Playing item: %s" % result)
    return result or None


def identify(result: Dict) -> Optional[Dict]:
    """Identify a library, non-library or PVR item as its start event, or None."""
    item = result["item"]
    media_type = item["type"]
    if "id" not in item or media_type in ("channel", "recording"):
        data = get_playing_details(result)[1]
        if not data and media_type in ("channel", "recording"):
            return _identify_by_name()
        return data or None
    if media_type in ("episode", "movie"):
        data: Dict[str, Any] = {"action": "started", "id": item["id"], "type": media_type}
        if media_type == "episode":
            data.update(_multi_part(item["id"]))
        return data
    logger.debug("Video type '%s' unrecognized, skipping." % media_type)
    return None


def _multi_part(episode_id: int) -> Dict:
    """Get the multi-part fields of a library episode sharing its file with others, else {}."""
    result = request(
        "VideoLibrary.GetEpisodeDetails",
        {"episodeid": episode_id, "properties": ["tvshowid", "season", "episode", "file"]},
    )
    if not result:
        return {}
    details = result["episodedetails"]
    result = request(
        "VideoLibrary.GetEpisodes",
        {
            "tvshowid": int(details["tvshowid"]),
            "season": int(details["season"]),
            "properties": ["episode", "file"],
            "sort": {"method": "episode"},
        },
    )
    if not result or "episodes" not in result:
        return {}
    parts = [e["episodeid"] for e in result["episodes"] if e["file"] == details["file"]]
    if len(parts) <= 1:
        logger.debug("This is a single episode.")
        return {}
    logger.debug("This episode is part of a multi-part episode.")
    return {"multi_episode_data": parts, "multi_episode_count": len(parts)}


def _identify_by_name() -> Optional[Dict]:
    """Identify a PVR program on Trakt by its show and episode names as a start event, or None."""
    show_name = xbmc.getInfoLabel("VideoPlayer.TVShowTitle") or xbmc.getInfoLabel(
        "VideoPlayer.Title")
    episode_name = xbmc.getInfoLabel("VideoPlayer.EpisodeName")
    if not (show_name and episode_name):
        logger.debug("PVR program has no show and episode names, skipping.")
        return None
    return _found(_find_by_name(show_name, episode_name, xbmc.getInfoLabel("VideoPlayer.Year")))


def _found(data: Optional[Dict]) -> Optional[Dict]:
    """Log what a name search found on Trakt, passing its start event through."""
    if data is None:
        logger.debug("Couldn't find this show and episode on Trakt. Skipping.")
        return None
    logger.debug(
        "Playing a non-library 'episode': show trakt key %s, season: %d, episode: %d"
        % (data["video_ids"], data["season"], data["episode"])
    )
    return data


def _find_by_name(show_name: str, episode_name: str, episode_year: str) -> Optional[Dict]:
    """Find an episode's show ids, season and number on Trakt by its names, or None."""
    try:
        year: Optional[int] = int(episode_year)
    except ValueError:
        year = None
    data: Dict[str, Any] = {"action": "started", "type": "episode", "video_ids": None,
                            "season": None, "episode": None, "episodeTitle": None}
    # Trakt text search returns nothing when show and episode are queried together
    hits = shared.api.search_by_text(episode_name, "episode", year) or []
    logger.debug("Episode search hits: %s" % hits)
    # search can't be scoped to a show
    match = next((hit for hit in hits if hit.show.title == show_name), None)
    if match is not None:
        # scrobbler takes show ids plus season/episode, not the episode ids
        data["video_ids"] = dict(match.show.keys)
        # search result carries season and episode as the pk pair
        data["season"], data["episode"] = match.pk[0], match.pk[1]
        return data
    # matches alternate titles; episode year would only match season 1
    hits = shared.api.search_by_text(show_name, "show", None)
    logger.debug("Show search hits: %s" % hits)
    if not hits:
        return None
    # nothing tells multiple hits apart
    data["video_ids"] = dict(hits[0].keys)
    # Trakt has no episode search within a show
    for season in shared.api.get_show_seasons(data["video_ids"]["trakt"]) or []:
        for episode in season.episodes.values():
            # some entries have no title
            title = getattr(episode, "title", None)
            if title == episode_name:
                # season/episode pair is the first entry in keys
                data["season"], data["episode"] = episode.keys[0]
                data["episodeTitle"] = title
                return data
    return None
