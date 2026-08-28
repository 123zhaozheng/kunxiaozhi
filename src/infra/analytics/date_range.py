"""Date range utilities for analytics - UTC+8 timezone handling.

This module provides pure functions for parsing and manipulating date ranges
using Asia/Shanghai (UTC+8) as the single source of truth for daily boundaries.
All datetime operations return timezone-aware values; string helpers return
ISO-format dates without time components.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
_BUCKET_TZ = "Asia/Shanghai"


def parse_date(value: str) -> date:
    """Parse YYYY-MM-DD date string.

    Args:
        value: Date string in format "YYYY-MM-DD".

    Returns:
        A Python date object.

    Raises:
        ValueError: If the string doesn't match the expected format.
    """
    parts = value.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date format: {value}. Expected YYYY-MM-DD.")

    # Strict length check: YYYY-MM-DD must be exactly 10 chars with leading zeros
    if not (len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2):
        raise ValueError(f"Invalid date format: {value}. Expected YYYY-MM-DD.")

    try:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f"Invalid date format: {value}. Expected YYYY-MM-DD.") from exc

    return date(year, month, day)


def resolve_range(start: str, end: str) -> tuple[datetime, datetime]:
    """Resolve a date range to half-open interval [start, end).

    Parses two YYYY-MM-DD strings, creates timezone-aware datetimes at 00:00:00
    in CST, then returns [start_midnight, next_day_midnight). This produces a
    half-open interval suitable for $gte/$lt MongoDB queries.

    Args:
        start: Start date string in format "YYYY-MM-DD".
        end: End date string in format "YYYY-MM-DD". Must be >= start.

    Returns:
        Tuple (start_dt, end_dt) where start_dt is the first instant of start_date,
        and end_dt is the first instant of the day after end_date.

    Raises:
        ValueError: If formats are invalid or end < start.
    """
    start_parsed = parse_date(start)
    end_parsed = parse_date(end)

    if end_parsed < start_parsed:
        raise ValueError(
            f"End date ({end}) must be >= start date ({start})."
        )

    start_dt = datetime(
        start_parsed.year,
        start_parsed.month,
        start_parsed.day,
        0,
        0,
        0,
        tzinfo=CST,
    )
    end_dt = datetime(
        end_parsed.year,
        end_parsed.month,
        end_parsed.day,
        0,
        0,
        0,
        tzinfo=CST,
    ) + timedelta(days=1)

    return start_dt, end_dt


def previous_range(start: str, end: str) -> tuple[str, str]:
    """Calculate the immediately preceding date range of equal length.

    Given a range "2026-08-22" to "2026-08-28", returns the same-length range
    ending one day before the original start: "2026-08-15" to "2026-08-21".

    Args:
        start: Start date string in format "YYYY-MM-DD".
        end: End date string in format "YYYY-MM-DD".

    Returns:
        Tuple (prev_start, prev_end) as YYYY-MM-DD strings.

    Raises:
        ValueError: If the input range is invalid.
    """
    start_parsed = parse_date(start)
    end_parsed = parse_date(end)

    if end_parsed < start_parsed:
        raise ValueError(
            f"End date ({end}) must be >= start date ({start})."
        )

    # Calculate number of days (inclusive)
    num_days = (end_parsed - start_parsed).days + 1

    # Previous range ends one day before original start
    prev_end_date = start_parsed - timedelta(days=1)
    # Previous range starts (num_days - 1) days before prev_end
    prev_start_date = prev_end_date - timedelta(days=num_days - 1)

    return prev_start_date.strftime("%Y-%m-%d"), prev_end_date.strftime("%Y-%m-%d")


def range_to_date_strings(start: datetime, end: datetime) -> tuple[str, str]:
    """Convert a half-open datetime interval to inclusive YYYY-MM-DD strings (UTC+8).

    Args:
        start: Timezone-aware interval start (inclusive).
        end: Timezone-aware exclusive upper bound.

    Returns:
        Tuple (start_date, end_date) as "YYYY-MM-DD" strings where end_date is
        the last day included in the interval.

    Notes:
        Datetimes may have been normalized to UTC upstream; derivation must
        happen in UTC+8 or the calendar day can shift by 8 hours. When end is
        exactly midnight it is the exclusive bound, so step back one day.
    """
    start_cst = start.astimezone(CST)
    end_cst = end.astimezone(CST)
    start_str = start_cst.strftime("%Y-%m-%d")
    end_inclusive_dt = end_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    if end_inclusive_dt >= end_cst:
        end_inclusive_dt -= timedelta(days=1)
    return start_str, end_inclusive_dt.strftime("%Y-%m-%d")


def day_buckets(start: str, end: str) -> list[str]:
    """Generate every calendar day in a closed range.

    Produces all "YYYY-MM-DD" dates between start and end inclusive, ordered ascending.
    Used for building x-axis buckets in daily trend visualizations.

    Args:
        start: Start date string in format "YYYY-MM-DD".
        end: End date string in format "YYYY-MM-DD".

    Returns:
        List of date strings. Length equals the number of days in range.

    Raises:
        ValueError: If the input range is invalid.
    """
    start_parsed = parse_date(start)
    end_parsed = parse_date(end)

    if end_parsed < start_parsed:
        raise ValueError(
            f"End date ({end}) must be >= start date ({start})."
        )

    buckets: list[str] = []
    current = start_parsed
    while current <= end_parsed:
        buckets.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    return buckets


def today_cst(now: datetime | None = None) -> str:
    """Get today's date in CST as "YYYY-MM-DD".

    Args:
        now: Optional UTC datetime to use instead of real time. For test injection.

    Returns:
        Today's date string in UTC+8 timezone.
    """
    if now is None:
        now_dt = datetime.now(timezone.utc)
    else:
        now_dt = now

    return now_dt.astimezone(CST).strftime("%Y-%m-%d")
