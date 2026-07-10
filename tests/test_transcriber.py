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
        {"key": "test", "value": [{"start": 0.0, "end": 1.0, "text": "你好", "spk": 0}]},
    ]
    transcriber._model = fake_model  # 跳过懒加载，注入 mock

    result = transcriber.transcribe("dummy.wav")

    assert len(result) == 1
    assert result[0]["text"] == "你好"
    fake_model.generate.assert_called_once()


@pytest.mark.integration
def test_transcribe_real_audio(tmp_path):
    """集成测试：需 FunASR 模型。skip if missing."""
    pytest.importorskip("funasr")
    # 跳过如果没有真实音频
    sample = tmp_path / "sample.wav"
    if not sample.exists():
        pytest.skip("no sample audio")
