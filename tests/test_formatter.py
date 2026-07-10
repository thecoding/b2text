import pytest
from b2text.normalizer import Segment
from b2text.formatter import format_segments


def test_empty_list_returns_empty_string():
    assert format_segments([]) == ""


def test_single_segment():
    segs = [Segment(15.0, 18.0, "Speaker_1", "大家好")]
    assert format_segments(segs) == "[00:00:15] Speaker_1: 大家好"


def test_multiple_segments_one_per_line():
    segs = [
        Segment(15.0, 18.0, "Speaker_1", "大家好"),
        Segment(23.0, 28.0, "Speaker_2", "今天聊啥"),
    ]
    result = format_segments(segs)
    lines = result.split("\n")
    assert lines[0] == "[00:00:15] Speaker_1: 大家好"
    assert lines[1] == "[00:00:23] Speaker_2: 今天聊啥"


def test_long_video_uses_hours():
    segs = [Segment(3725.0, 3730.0, "Speaker_1", "一小时后")]
    assert format_segments(segs) == "[01:02:05] Speaker_1: 一小时后"


def test_uses_utf8_encoding_compatible_strings():
    segs = [Segment(0.0, 1.0, "Speaker_1", "你好世界")]
    result = format_segments(segs)
    assert "你好世界" in result