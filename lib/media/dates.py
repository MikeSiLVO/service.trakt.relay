import logging
from typing import Tuple, Optional
import dateutil.parser
from datetime import datetime, timedelta
from dateutil.tz import tzutc, tzlocal

logger = logging.getLogger(__name__)


def _borrowed_offset(naive: datetime) -> Optional[timedelta]:
    """Local UTC offset on the same month and day in 1971, for dates the OS will not convert."""
    day = min(naive.day, 28) if naive.month == 2 else naive.day
    return tzlocal().utcoffset(naive.replace(year=1971, day=day))


def local_to_utc(value: Optional[str]) -> Optional[str]:
    """Convert a Kodi local timestamp to a UTC string, borrowing the 1971 offset where needed."""
    if value:
        naive = datetime.fromisoformat(value)
        try:
            utc = naive.replace(tzinfo=tzlocal()).astimezone(tzutc())
        except (OSError, OverflowError, ValueError):
            utc = (naive - (_borrowed_offset(naive) or timedelta())).replace(tzinfo=tzutc())
        return str(utc)
    else:
        return value


def utc_to_local(value: Optional[str]) -> Optional[str]:
    """Convert a Trakt UTC timestamp to Kodi's local format, or now when it cannot parse."""
    if value:
        date_format = "%Y-%m-%d %H:%M:%S"
        try:
            naive = dateutil.parser.parse(value).replace(tzinfo=None)
        except (OverflowError, ValueError):
            logger.debug("utc_to_local(): cannot parse %s, using now" % value)
            return datetime.now().strftime(date_format)
        try:
            local = naive.replace(tzinfo=tzutc()).astimezone(tzlocal())
        except (OSError, OverflowError, ValueError):
            local = naive + (_borrowed_offset(naive) or timedelta())
        return local.strftime(date_format)
    else:
        return value


def to_seconds(timedelta_string: str, factors: Tuple[int, ...] = (1, 60, 3600, 86400)) -> float:
    """Convert a [[[days:]hours:]minutes:]seconds string to seconds."""
    return sum(
        x * y
        for x, y in zip(list(map(float, timedelta_string.split(":")[::-1])), factors)
    )
