import pytest
from b2text.utils import extract_bvid, format_timestamp


class TestExtractBvid:
    def test_pure_bvid(self):
        assert extract_bvid("BV1abc123def") == "BV1abc123def"

    def test_bvid_lowercase(self):
        assert extract_bvid("bv1abc123def") == "BV1abc123def"

    def test_full_url(self):
        assert extract_bvid("https://www.bilibili.com/video/BV1abc123def") == "BV1abc123def"

    def test_short_url(self):
        assert extract_bvid("https://b23.tv/BV1abc123def") == "BV1abc123def"

    def test_invalid_input_returns_none(self):
        assert extract_bvid("not a bv id") is None

    def test_empty_string_returns_none(self):
        assert extract_bvid("") is None


class TestFormatTimestamp:
    def test_zero(self):
        assert format_timestamp(0.0) == "00:00:00"

    def test_seconds_only(self):
        assert format_timestamp(15.0) == "00:00:15"

    def test_minutes_and_seconds(self):
        assert format_timestamp(83.0) == "00:01:23"

    def test_hours_minutes_seconds(self):
        assert format_timestamp(3725.0) == "01:02:05"

    def test_truncates_subseconds(self):
        assert format_timestamp(15.789) == "00:00:15"