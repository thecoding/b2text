# tests/test_normalizer.py
import pytest
from b2text.normalizer import Segment, normalize_funasr_output


def test_empty_input():
    assert normalize_funasr_output([]) == []


def test_skips_segments_without_text():
    raw = [
        {"start": 0.0, "end": 1.0, "text": "你好", "spk": 0},
        {"start": 1.0, "end": 2.0, "text": "", "spk": 1},  # 无文字，跳过
    ]
    result = normalize_funasr_output(raw)
    assert len(result) == 1
    assert result[0].text == "你好"


def test_accepts_sentence_key_from_funasr_pipeline():
    """FunASR merged pipeline 用 'sentence' 而非 'text'。"""
    raw = [{"start": 0.0, "end": 2.0, "sentence": "你好世界", "spk": 0}]
    result = normalize_funasr_output(raw)
    assert len(result) == 1
    assert result[0].text == "你好世界"


def test_skips_segments_without_speaker():
    raw = [
        {"start": 0.0, "end": 1.0, "text": "你好"},
        {"start": 1.0, "end": 2.0, "text": "hello", "spk": 0},
    ]
    result = normalize_funasr_output(raw)
    assert len(result) == 1
    assert result[0].text == "hello"


def test_speaker_indices_are_renumbered_by_first_appearance():
    """spk=5 先出现 → Speaker_1；spk=2 后出现 → Speaker_2。"""
    raw = [
        {"start": 0.0, "end": 1.0, "text": "A 先说", "spk": 5},
        {"start": 1.0, "end": 2.0, "text": "B 后说", "spk": 2},
        {"start": 2.0, "end": 3.0, "text": "A 又说", "spk": 5},
    ]
    result = normalize_funasr_output(raw)
    assert result[0].speaker == "Speaker_1"
    assert result[1].speaker == "Speaker_2"
    assert result[2].speaker == "Speaker_1"


def test_keeps_original_timing():
    raw = [{"start": 15.5, "end": 18.7, "text": "你好", "spk": 0}]
    result = normalize_funasr_output(raw)
    assert result[0].start == 15.5
    assert result[0].end == 18.7


def test_output_is_sorted_by_start_time():
    raw = [
        {"start": 5.0, "end": 6.0, "text": "后", "spk": 0},
        {"start": 1.0, "end": 2.0, "text": "先", "spk": 0},
    ]
    result = normalize_funasr_output(raw)
    assert result[0].text == "先"
    assert result[1].text == "后"
