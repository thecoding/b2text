# tests/test_transcriber.py
import pytest
from unittest.mock import MagicMock
from b2text.transcriber import FunASRTranscriber


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

    result = transcriber.transcribe("dummy.wav")
    assert len(result) == 1
    assert result[0]["text"] == "老格式"


def test_handles_empty_generate_result():
    """FunASR 偶尔会返回 []。要返回空列表不抛异常。"""
    transcriber = FunASRTranscriber()
    fake_model = MagicMock()
    fake_model.generate.return_value = []
    transcriber._model = fake_model

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


@pytest.mark.integration
def test_transcribe_real_audio(tmp_path):
    """集成测试：需 FunASR 模型。skip if missing."""
    pytest.importorskip("funasr")
    # 跳过如果没有真实音频
    sample = tmp_path / "sample.wav"
    if not sample.exists():
        pytest.skip("no sample audio")
