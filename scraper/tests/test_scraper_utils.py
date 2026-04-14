"""Tests for scraper utility functions that don't need a browser."""
import sys
from pathlib import Path

# Add scraper dir to path so we can import linkedin_scraper functions
sys.path.insert(0, str(Path(__file__).parent.parent))

from linkedin_scraper import parse_relative_time
from datetime import datetime


def test_parse_just_now():
    result = parse_relative_time("Just now")
    assert result is not None
    # Should be close to now
    parsed = datetime.fromisoformat(result)
    assert (datetime.now() - parsed).total_seconds() < 5


def test_parse_minutes():
    result = parse_relative_time("30m")
    assert result is not None
    parsed = datetime.fromisoformat(result)
    diff_minutes = (datetime.now() - parsed).total_seconds() / 60
    assert 29 < diff_minutes < 31


def test_parse_hours():
    result = parse_relative_time("2h")
    assert result is not None
    parsed = datetime.fromisoformat(result)
    diff_hours = (datetime.now() - parsed).total_seconds() / 3600
    assert 1.9 < diff_hours < 2.1


def test_parse_too_old_returns_none():
    # MAX_SCROLL_HOURS defaults to 24, so 2 days = too old
    result = parse_relative_time("2d")
    assert result is None


def test_parse_dutch_nu():
    result = parse_relative_time("nu")
    assert result is not None


def test_parse_hours_text():
    result = parse_relative_time("3 hours ago")
    assert result is not None
