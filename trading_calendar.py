from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

try:
    import holidays
except ImportError:  # pragma: no cover - requirements install provides it
    holidays = None

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CLOSE_MINUTE = 15 * 60


@lru_cache(maxsize=16)
def _china_holidays(year: int) -> frozenset[date]:
    if holidays is None:
        return frozenset()
    try:
        calendar = holidays.country_holidays("CN", years=[int(year)])
    except Exception:
        return frozenset()
    return frozenset(day for day in calendar.keys() if isinstance(day, date))


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _china_holidays(day.year)


def latest_completed_trading_day(now: datetime | None = None) -> date:
    current = now or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SHANGHAI)
    else:
        current = current.astimezone(_SHANGHAI)
    candidate = current.date()
    minute = current.hour * 60 + current.minute
    if is_trading_day(candidate) and minute >= _CLOSE_MINUTE:
        return candidate
    candidate -= timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def trading_age_days(asof: date, now: datetime | None = None) -> int:
    """Return completed-session lag, or -1 for an impossible future data date.

    Execution freshness is defined relative to the latest *completed* A-share
    session. Treating a date after that boundary as age zero would allow an
    intraday/future-dated bar to masquerade as completed EOD data. Callers use a
    negative age as invalid/unknown evidence and therefore fail closed.
    """
    target = latest_completed_trading_day(now)
    if asof > target:
        return -1
    if asof == target:
        return 0
    count = 0
    cursor = asof + timedelta(days=1)
    while cursor <= target:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def market_is_closed(now: datetime | None = None) -> bool:
    current = now or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SHANGHAI)
    else:
        current = current.astimezone(_SHANGHAI)
    return (
        not is_trading_day(current.date())
        or current.hour * 60 + current.minute >= _CLOSE_MINUTE
    )
