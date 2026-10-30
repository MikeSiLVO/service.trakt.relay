"""Rating prompt, and sending ratings to Trakt and the Kodi library."""

import xbmcgui
from lib.media import utilities
from lib.kodi import utilities as kodiUtilities
from typing import Dict, List, Optional, cast
from lib.trakt import shared
import logging
from lib.kodi.client import ADDON, request
from lib.kodi.settings import get_setting_bool, get_setting_float, get_setting_int, get_string

logger = logging.getLogger(__name__)

RATING_XML = "script-trakt-relay-rating.xml"
RATING_BUTTONS = {100 + rating: rating for rating in range(1, 11)}
NAME_LABEL = 100
CHOICE_LABEL = 99
RATING_LABELS = {
    1: 32028, 2: 32029, 3: 32030, 4: 32031, 5: 32032,
    6: 32033, 7: 32034, 8: 32035, 9: 32036, 10: 32027,
}
UNRATE_LABELS = {"movie": 32037, "show": 32038, "season": 32132, "episode": 32039}


def check_rating(
    media_type: str, items_to_rate: List[Dict], watched_time: float, total_time: float
) -> None:
    """Check if a played video's type and watch time call for a rating, then ask for one."""
    logger.debug("Rating Check called for '%s'" % media_type)
    if not get_setting_bool("rate_%s" % media_type):
        logger.debug("'%s' is configured to not be rated." % media_type)
        return
    if items_to_rate is None:
        logger.debug("Summary information is empty, aborting.")
        return
    if total_time <= 0:
        logger.debug("Total time is zero or negative, aborting rating check.")
        return
    watched = (watched_time / total_time) * 100
    if watched >= get_setting_float("rate_min_view_time"):
        rate_media(media_type, items_to_rate)
    else:
        logger.debug(
            "'%s' does not meet minimum view time for rating (watched: %0.2f%%, minimum: %0.2f%%)"
            % (media_type, watched, get_setting_float("rate_min_view_time"))
        )


def rate_media(
    media_type: str,
    items_to_rate: List[Dict],
    unrate: bool = False,
    rating: Optional[int] = None,
) -> None:
    """Rate each item: apply the given rating, unrate, or ask; re-rating follows its setting."""
    if not utilities.is_valid_media_type(media_type):
        logger.debug("Not a valid media type")
        return

    for summary_info in items_to_rate:
        if 'user' not in summary_info:
            logger.debug("No user data, skipping item")
            continue

        name = utilities.format_item_name(media_type, summary_info)

        logger.debug("Summary Info %s" % summary_info)

        if unrate:
            user_ratings = summary_info['user']['ratings']
            if user_ratings and user_ratings.get('rating', 0) > 0:
                logger.debug("'%s' is being unrated." % name)
                _rate_on_trakt(0, media_type, summary_info, unrate=True)
            else:
                logger.debug("'%s' has not been rated, so not unrating." % name)

            continue

        rerate = get_setting_bool('rate_rerate')
        current_rating = 0
        if summary_info['user']['ratings']:
            current_rating = summary_info['user']['ratings'].get('rating', 0)

        if rating is not None:
            if current_rating == 0:
                logger.debug(
                    "Rating for '%s' is being set to '%d' manually." % (name, rating))
                _rate_on_trakt(rating, media_type, summary_info)
            else:
                if rerate:
                    if current_rating != rating:
                        logger.debug(
                            "Rating for '%s' is being set to '%d' manually." % (name, rating))
                        _rate_on_trakt(rating, media_type, summary_info)
                    else:
                        kodiUtilities.notify(
                            get_string(32041) % current_rating, name)
                        logger.debug(
                            "'%s' already has a rating of '%d'." % (name, rating))
                else:
                    kodiUtilities.notify(
                        get_string(32041) % current_rating, name)
                    logger.debug("'%s' is already rated." % name)
            continue

        if current_rating:
            if not rerate:
                logger.debug("'%s' has already been rated." % name)
                kodiUtilities.notify(get_string(32041) % current_rating, name)
                continue
            else:
                logger.debug("'%s' is being re-rated." % name)

        dialog = RatingDialog(RATING_XML, ADDON.getAddonInfo('path'), "Default", "1080i")
        kodiUtilities.set_theme(dialog)
        dialog.media_type = media_type
        dialog.media = summary_info
        dialog.rerate = rerate
        dialog.doModal()
        chosen = dialog.rating
        if chosen:
            if rerate and current_rating > 0 and chosen == current_rating:
                _rate_on_trakt(0, media_type, summary_info, unrate=True)
            else:
                _rate_on_trakt(chosen, media_type, summary_info)
        else:
            logger.debug("Rating dialog was closed with no rating.")

        del dialog


def _rate_on_trakt(
    rating: int, media_type: str, media: Dict, unrate: bool = False
) -> None:
    """Rate or unrate an item on Trakt, copy the rating to its library entry, and notify."""
    logger.debug("Sending rating (%s) to Trakt.tv" % rating)

    params = dict(media)
    if utilities.is_movie(media_type):
        key = 'movies'
        params['rating'] = rating
        if media.get('movieid'):
            request(
                "VideoLibrary.SetMovieDetails",
                {"movieid": media['movieid'], "userrating": rating},
            )
    elif utilities.is_show(media_type):
        key = 'shows'
        params.pop("seasons", None)
        params['rating'] = rating
        if media.get('tvshowid'):
            request(
                "VideoLibrary.SetTVShowDetails",
                {"tvshowid": media['tvshowid'], "userrating": rating},
            )
    elif utilities.is_season(media_type):
        key = 'shows'
        params['seasons'] = [{'rating': rating, 'number': media['season']}]
        if media.get('seasonid'):
            request(
                "VideoLibrary.SetSeasonDetails",
                {"seasonid": media['seasonid'], "userrating": rating},
            )
    elif utilities.is_episode(media_type):
        key = 'episodes'
        params['rating'] = rating
        if media.get('episodeid'):
            request(
                "VideoLibrary.SetEpisodeDetails",
                {"episodeid": media['episodeid'], "userrating": rating},
            )
    else:
        return
    root = {key: [params]}

    if not unrate:
        data = shared.api.add_ratings(root)
    else:
        data = shared.api.remove_ratings(root)

    if data:
        name = utilities.format_item_name(media_type, media)
        not_found = data.get('not_found', {})
        if not (
            not_found.get('movies') or not_found.get('episodes') or not_found.get('shows')
        ):

            if not unrate:
                kodiUtilities.notify(get_string(32040) % rating, name)
            else:
                kodiUtilities.notify(get_string(32042), name)
        else:
            kodiUtilities.notify(get_string(32044), name)
    else:
        name = utilities.format_item_name(media_type, media)
        logger.debug("Failed to submit rating for '%s' to Trakt" % name)
        kodiUtilities.notify(get_string(32044), name)


class RatingDialog(xbmcgui.WindowXMLDialog):
    """Dialog asking for a 1 to 10 rating, focused on the current or default rating."""

    media_type: str
    media: Dict
    rerate: bool
    rating: Optional[int] = None

    def onInit(self) -> None:
        """Show the item's name and focus its current rating when re-rating, else the default."""
        name = utilities.format_item_name(self.media_type, self.media)
        cast(xbmcgui.ControlLabel, self.getControl(NAME_LABEL)).setLabel(name)

        rating = get_setting_int('rating_default')
        user_ratings = self.media['user']['ratings']
        if self.rerate and user_ratings and user_ratings.get('rating', 0) > 0:
            rating = int(user_ratings['rating'])
        self.setFocusId(100 + rating)

    def onClick(self, control_id: int) -> None:
        """Take the clicked rating and close."""
        if control_id in RATING_BUTTONS:
            self.rating = RATING_BUTTONS[control_id]
            self.close()

    def onFocus(self, control_id: int) -> None:
        """Describe the focused rating, or offer to unrate when it matches the current one."""
        label = ''
        if control_id in RATING_BUTTONS:
            rating = RATING_BUTTONS[control_id]
            label = "[B]%d  %s[/B]" % (rating, get_string(RATING_LABELS[rating]))
            user_ratings = self.media['user']['ratings']
            if self.rerate and user_ratings and user_ratings.get('rating') == rating:
                label = "[B]%s[/B]" % get_string(UNRATE_LABELS[self.media_type])
        cast(xbmcgui.ControlLabel, self.getControl(CHOICE_LABEL)).setLabel(label)
