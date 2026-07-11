# tests/test_transcriber.py
import subprocess
import pytest
from unittest.mock import MagicMock, patch
from b2text.transcriber import FunASRTranscriber


def _fake_short_duration(*args, **kwargs):
    """mock ffprobe 返回 5 秒（短音频，不需要 chunking）。"""
    return subprocess.CompletedProcess(args, 0, b"5.0\n", b"")


def test_lazy_loads_model_on_first_call():
    """首次 transcribe 调用时才加载模型。"""
    transcriber = FunASRTranscriber()

    # 没调用过 transcribe，模型未加载
    assert transcriber._model is None

    # mock AutoModel
    fake_model = MagicMock()
    fake_model.generate.return_value = [
        {"key": "test", "sentence_info": [{"start": 0.0, "end": 1.0, "sentence": "你好", "spk": 0}]},
    ]
    transcriber._model = fake_model  # 跳过懒加载，注入 mock

    with patch("b2text.transcriber.get_wav_duration_seconds", return_value=5.0):
        result = transcriber.transcribe("dummy.wav")

    assert len(result) == 1
    assert result[0]["sentence"] == "你好"
    fake_model.generate.assert_called_once()


def test_falls_back_to_value_key_for_legacy_pipeline():
    """旧版 FunASR pipeline 用 'value' 而非 'sentence_info'。"""
    transcriber = FunASRTranscriber()
    fake_model = MagicMock()
    fake_model.generate.return_value = [
        {"key": "test", "value": [{"start": 0.0, "end": 1.0, "text": "老格式", "spk": 0}]},
    ]
    transcriber._model = fake_model

    with patch("b2text.transcriber.get_wav_duration_seconds", return_value=5.0):
        result = transcriber.transcribe("dummy.wav")
    assert len(result) == 1
    assert result[0]["text"] == "老格式"


def test_handles_empty_generate_result():
    """FunASR 偶尔会返回 []。要返回空列表不抛异常。"""
    transcriber = FunASRTranscriber()
    fake_model = MagicMock()
    fake_model.generate.return_value = []
    transcriber._model = fake_model

    with patch("b2text.transcriber.get_wav_duration_seconds", return_value=5.0):
        assert transcriber.transcribe("dummy.wav") == []


def test_uses_spk_num_when_provided():
    """spk_num 指定时传给 AutoModel。"""
    from funasr import AutoModel as _AutoModel
    import unittest.mock as mock

    with mock.patch("funasr.AutoModel") as fake_cls:
        transcriber = FunASRTranscriber(spk_num=3)
        transcriber._load_model()
        _, kwargs = fake_cls.call_args
        assert kwargs["spk_num"] == 3


def test_long_audio_is_chunked_and_offsets_applied():
    """超过阈值的音频被切成多片，时间戳加上对应偏移量。"""
    transcriber = FunASRTranscriber()
    fake_model = MagicMock()
    # 第一次调用返回 chunk 1 的结果，第二次返回 chunk 2 的
    fake_model.generate.side_effect = [
        [{"key": "c1", "sentence_info": [
            {"start": 0.0, "end": 5.0, "sentence": "第一片", "spk": 0},
        ]}],
        [{"key": "c2", "sentence_info": [
            {"start": 0.0, "end": 5.0, "sentence": "第二片", "spk": 0},
        ]}],
    ]
    transcriber._model = fake_model

    # Mock chunk_wav 返回 2 个 chunk，每个 offset = 0, 300
    fake_chunks = [
        (MagicMock(name="chunk1"), 0.0),
        (MagicMock(name="chunk2"), 300.0),
    ]

    with patch("b2text.transcriber.get_wav_duration_seconds", return_value=1500.0), \
         patch("b2text.transcriber.chunk_wav", return_value=fake_chunks):
        result = transcriber.transcribe("dummy.wav")

    assert len(result) == 2
    # 第一片：start=0, end=5
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 5.0
    assert result[0]["sentence"] == "第一片"
    # 第二片：start=300, end=305
    assert result[1]["start"] == 300.0
    assert result[1]["end"] == 305.0
    assert result[1]["sentence"] == "第二片"


def test_chunked_audio_converts_ms_to_seconds():
    """长音频的毫秒时间戳在 chunked 路径下也要转为秒。"""
    transcriber = FunASRTranscriber()
    fake_model = MagicMock()
    fake_model.generate.return_value = [
        {"sentence_info": [{"start": 5000, "end": 15000, "sentence": "毫秒值", "spk": 0}]},
    ]
    transcriber._model = fake_model

    fake_chunks = [(MagicMock(name="c"), 0.0)]

    with patch("b2text.transcriber.get_wav_duration_seconds", return_value=1500.0), \
         patch("b2text.transcriber.chunk_wav", return_value=fake_chunks):
        result = transcriber.transcribe("dummy.wav")

    assert result[0]["start"] == 5.0
    assert result[0]["end"] == 15.0


@pytest.mark.integration
def test_transcribe_real_audio(tmp_path):
    """集成测试：需 FunASR 模型。skip if missing."""
    pytest.importorskip("funasr")
    sample = tmp_path / "sample.wav"
    if not sample.exists():
        pytest.skip("no sample audio")
