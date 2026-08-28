"""Tests for src/infra/analytics/date_range - UTC+8 date utilities.

Verifies:
- resolve_range produces exact CST midnight ± next day half-open interval
- previous_range computes equal-length prior ranges correctly (1d/7d/30d/custom)
- day_buckets generates inclusive [start, end] lists with correct length
- Invalid inputs raise ValueError (format errors, end < start)
- today_cst handles cross-midnight boundary via injected now parameter
"""

from datetime import datetime, timezone

import pytest

from src.infra.analytics.date_range import (
    CST,
    day_buckets,
    parse_date,
    previous_range,
    resolve_range,
    today_cst,
)


class TestParseDate:
    """Test parsing YYYY-MM-DD strings."""

    def test_valid_date(self):
        result = parse_date("2026-08-28")
        assert result.year == 2026
        assert result.month == 8
        assert result.day == 28

    def test_leap_year(self):
        result = parse_date("2024-02-29")
        assert result.year == 2024
        assert result.month == 2
        assert result.day == 29

    def test_invalid_format_missing_parts(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            parse_date("2026-8-28")  # missing leading zero

    def test_invalid_format_extra_dash(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            parse_date("2026-08-28-extra")

    def test_invalid_month_value(self):
        with pytest.raises(ValueError):
            parse_date("2026-13-01")  # invalid month - raises from date constructor

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            parse_date("")


class TestResolveRange:
    """Test resolving date strings to CST half-open intervals."""

    def test_exact_match_prd_example(self):
        """PRD example: "2026-08-22","2026-08-28" → (08-22T00:00+08:00, 08-29T00:00+08:00)."""
        start, end = resolve_range("2026-08-22", "2026-08-28")

        expected_start = datetime(2026, 8, 22, 0, 0, 0, tzinfo=CST)
        expected_end = datetime(2026, 8, 29, 0, 0, 0, tzinfo=CST)

        assert start == expected_start
        assert end == expected_end

    def test_single_day_today_preset(self):
        """Today preset in PRD: start == end must be supported as first-class citizen."""
        start, end = resolve_range("2026-08-28", "2026-08-28")

        expected_start = datetime(2026, 8, 28, 0, 0, 0, tzinfo=CST)
        expected_end = datetime(2026, 8, 29, 0, 0, 0, tzinfo=CST)

        assert start == expected_start
        assert end == expected_end

    def test_cross_year_boundary(self):
        start, end = resolve_range("2025-12-31", "2026-01-02")

        expected_start = datetime(2025, 12, 31, 0, 0, 0, tzinfo=CST)
        expected_end = datetime(2026, 1, 3, 0, 0, 0, tzinfo=CST)

        assert start == expected_start
        assert end == expected_end

    def test_end_less_than_start_raises(self):
        with pytest.raises(ValueError, match="must be >="):
            resolve_range("2026-08-28", "2026-08-22")

    def test_invalid_start_format_raises(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            resolve_range("invalid", "2026-08-28")

    def test_invalid_end_format_raises(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            resolve_range("2026-08-22", "bad")


class TestPreviousRange:
    """Test calculating equal-length preceding ranges."""

    def test_1_day_today_preset(self):
        """PRD Today preset: single day range. Start==end is first-class citizen."""
        prev_start, prev_end = previous_range("2026-08-28", "2026-08-28")
        # Single day -> prev is also single day, one day earlier
        assert prev_start == "2026-08-27"
        assert prev_end == "2026-08-27"

    def test_7_days_weekly_preset(self):
        """PRD 7-day preset."""
        prev_start, prev_end = previous_range("2026-08-22", "2026-08-28")
        assert prev_start == "2026-08-15"
        assert prev_end == "2026-08-21"

    def test_30_days_monthly_preset(self):
        """PRD 30-day preset."""
        prev_start, prev_end = previous_range("2026-08-28", "2026-09-26")
        assert prev_start == "2026-07-29"
        assert prev_end == "2026-08-27"

    def test_custom_length(self):
        """Custom length from PRD: 2026-08-22 to 2026-08-25 = 4 days."""
        prev_start, prev_end = previous_range("2026-08-22", "2026-08-25")
        assert prev_start == "2026-08-18"
        assert prev_end == "2026-08-21"

    def test_cross_month_boundary(self):
        """Range crosses month boundary."""
        prev_start, prev_end = previous_range("2026-03-01", "2026-03-05")
        assert prev_start == "2026-02-24"
        assert prev_end == "2026-02-28"

    def test_cross_year_boundary(self):
        """Range starts in new year."""
        prev_start, prev_end = previous_range("2026-01-01", "2026-01-05")
        assert prev_start == "2025-12-27"
        assert prev_end == "2025-12-31"


class TestDayBuckets:
    """Test generating inclusive daily buckets."""

    def test_basic_range_inclusive(self):
        buckets = day_buckets("2026-08-22", "2026-08-28")
        expected = [
            "2026-08-22",
            "2026-08-23",
            "2026-08-24",
            "2026-08-25",
            "2026-08-26",
            "2026-08-27",
            "2026-08-28",
        ]
        assert buckets == expected

    def test_single_day(self):
        buckets = day_buckets("2026-08-28", "2026-08-28")
        assert buckets == ["2026-08-28"]

    def test_exact_length(self):
        """Length must equal number of days in closed range."""
        buckets = day_buckets("2026-08-22", "2026-08-28")
        assert len(buckets) == 7

        buckets = day_buckets("2026-01-01", "2026-01-31")
        assert len(buckets) == 31

        buckets = day_buckets("2026-01-01", "2026-02-28")
        assert len(buckets) == 59

    def test_cross_month_boundary(self):
        buckets = day_buckets("2026-02-28", "2026-03-05")
        expected = [
            "2026-02-28",
            "2026-03-01",
            "2026-03-02",
            "2026-03-03",
            "2026-03-04",
            "2026-03-05",
        ]
        assert buckets == expected

    def test_end_less_than_start_raises(self):
        with pytest.raises(ValueError, match="must be >="):
            day_buckets("2026-08-28", "2026-08-22")

    def test_leap_year_february(self):
        """Verify leap year Feb has 29 days."""
        buckets = day_buckets("2024-02-01", "2024-02-29")
        assert len(buckets) == 29
        assert buckets[-1] == "2024-02-29"


class TestTodayCst:
    """Test getting current date in CST, including cross-midnight edge cases."""

    def test_with_injected_datetime(self):
        # UTC 2026-08-28 17:30 -> CST 2026-08-29 01:30
        now_utc = datetime(2026, 8, 28, 17, 30, 0, tzinfo=timezone.utc)
        assert today_cst(now_utc) == "2026-08-29"

    def test_utc_midnight_boundary(self):
        # UTC 2026-08-28 16:00 -> CST 2026-08-29 00:00
        now_utc = datetime(2026, 8, 28, 16, 0, 0, tzinfo=timezone.utc)
        assert today_cst(now_utc) == "2026-08-29"

    def test_utc_before_midnight(self):
        # UTC 2026-08-28 07:59 -> CST 2026-08-28 15:59
        now_utc = datetime(2026, 8, 28, 7, 59, 0, tzinfo=timezone.utc)
        assert today_cst(now_utc) == "2026-08-28"

    def test_no_parameter_uses_real_time(self):
        # Just verify it returns something valid without error
        result = today_cst()
        assert len(result) == 10
        assert result[4] == "-"
        assert result[7] == "-"
        # Should be a valid date
        parse_date(result)  # raises if invalid
