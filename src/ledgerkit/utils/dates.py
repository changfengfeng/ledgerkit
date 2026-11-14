"""Date helpers: parsing, month arithmetic and period specifications."""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta

from ..errors import LedgerError

_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_SLASH_YMD_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_DOT_DMY_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_SLASH_US_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")
_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class DateParseError(LedgerError, ValueError):
    code = "invalid_date"


def _make(year: int, month: int, day: int, original: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise DateParseError(f"invalid date {original!r}: {exc}") from None


def parse_date(value: str | date | datetime, dayfirst: bool = False) -> date:
    """Parse common date spellings.

    Supported: ``2024-03-01``, ``2024/03/01``, ``20240301``, ``01.03.2024``
    (always day first) and ``03/01/2024`` (month first unless ``dayfirst``).
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    for regex in (_ISO_RE, _SLASH_YMD_RE, _COMPACT_RE):
        match = regex.match(text)
        if match:
            y, m, d = (int(g) for g in match.groups())
            return _make(y, m, d, text)
    match = _DOT_DMY_RE.match(text)
    if match:
        d, m, y = (int(g) for g in match.groups())
        return _make(y, m, d, text)
    match = _SLASH_US_RE.match(text)
    if match:
        a, b, y = (int(g) for g in match.groups())
        if y < 100:
            y += 2000 if y < 70 else 1900
        month, day = (b, a) if dayfirst else (a, b)
        return _make(y, month, day, text)
    raise DateParseError(f"unrecognised date: {value!r}")


def parse_date_with_format(value: str, fmt: str) -> date:
    try:
        return datetime.strptime(value.strip(), fmt).date()
    except ValueError as exc:
        raise DateParseError(f"date {value!r} does not match {fmt!r}: {exc}") from None


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def add_months(day: date, months: int, anchor_day: int | None = None) -> date:
    """Shift ``day`` by ``months`` months, clamping to the end of the month.

    ``anchor_day`` lets callers keep the original day-of-month across repeated
    shifts (e.g. 31 Jan -> 29 Feb -> 31 Mar) instead of drifting to the 29th.
    """
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    target_day = anchor_day if anchor_day is not None else day.day
    return date(year, month, min(target_day, days_in_month(year, month)))


def add_years(day: date, years: int) -> date:
    return add_months(day, years * 12)


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=days_in_month(day.year, day.month))


def quarter_of(day: date) -> int:
    return (day.month - 1) // 3 + 1


def quarter_start(day: date) -> date:
    return date(day.year, 3 * (quarter_of(day) - 1) + 1, 1)


def quarter_end(day: date) -> date:
    start = quarter_start(day)
    return month_end(add_months(start, 2))


def year_start(day: date) -> date:
    return date(day.year, 1, 1)


def year_end(day: date) -> date:
    return date(day.year, 12, 31)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def iter_days(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def iter_months(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Yield (first_day, last_day) for each calendar month touching the range."""
    current = month_start(start)
    while current <= end:
        yield current, month_end(current)
        current = add_months(current, 1)


def iter_quarters(start: date, end: date) -> Iterator[tuple[date, date]]:
    current = quarter_start(start)
    while current <= end:
        yield current, quarter_end(current)
        current = add_months(current, 3)


def is_business_day(day: date, holidays: frozenset[date] | set[date] = frozenset()) -> bool:
    return day.weekday() < 5 and day not in holidays


def add_business_days(day: date, count: int, holidays: frozenset[date] | set[date] = frozenset()) -> date:
    step = 1 if count >= 0 else -1
    remaining = abs(count)
    current = day
    while remaining:
        current += timedelta(days=step)
        if is_business_day(current, holidays):
            remaining -= 1
    return current


_PERIOD_YEAR = re.compile(r"^(\d{4})$")
_PERIOD_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_PERIOD_QUARTER = re.compile(r"^(\d{4})-?Q([1-4])$", re.IGNORECASE)
_PERIOD_RANGE = re.compile(r"^(.+?)\.\.(.+)$")


def parse_period(spec: str, today: date | None = None) -> tuple[date, date]:
    """Parse a period specification into an inclusive (start, end) range.

    Examples: ``2024``, ``2024-03``, ``2024-Q2``, ``2024-01-01..2024-06-30``,
    ``this-month``, ``last-month``, ``this-year``, ``last-year``, ``ytd``.
    """
    text = spec.strip()
    now = today or date.today()
    lowered = text.lower()
    if lowered == "this-month":
        return month_start(now), month_end(now)
    if lowered == "last-month":
        prev = add_months(month_start(now), -1)
        return prev, month_end(prev)
    if lowered == "this-year":
        return year_start(now), year_end(now)
    if lowered == "last-year":
        prev = date(now.year - 1, 1, 1)
        return prev, year_end(prev)
    if lowered == "ytd":
        return year_start(now), now
    if lowered == "this-quarter":
        return quarter_start(now), quarter_end(now)
    match = _PERIOD_RANGE.match(text)
    if match:
        start = parse_period(match.group(1), now)[0]
        end = parse_period(match.group(2), now)[1]
        if end < start:
            raise DateParseError(f"period end before start: {spec!r}")
        return start, end
    match = _PERIOD_YEAR.match(text)
    if match:
        year = int(match.group(1))
        return date(year, 1, 1), date(year, 12, 31)
    match = _PERIOD_MONTH.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        start = _make(year, month, 1, text)
        return start, month_end(start)
    match = _PERIOD_QUARTER.match(text)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        start = date(year, 3 * (quarter - 1) + 1, 1)
        return start, quarter_end(start)
    day = parse_date(text)
    return day, day


def format_period(start: date, end: date) -> str:
    if start == month_start(start) and end == month_end(start):
        return start.strftime("%Y-%m")
    if start == quarter_start(start) and end == quarter_end(start):
        return f"{start.year}-Q{quarter_of(start)}"
    if start == year_start(start) and end == year_end(start):
        return str(start.year)
    return f"{start.isoformat()}..{end.isoformat()}"
