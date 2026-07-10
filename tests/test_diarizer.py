# tests/test_diarizer.py
from unittest.mock import MagicMock
from b2text.diarizer import FunASRDiarizer


def test_lazy_loads_model_on_first_call():
    diarizer = FunASRDiarizer()
    assert diarizer._model is None

    fake_model = MagicMock()
    fake_model.generate.return_value = [
        {"key": "test", "value": [{"start": 0.0, "end": 1.0, "text": "", "spk": 0}]},
    ]
    diarizer._model = fake_model

    result = diarizer.diarize("dummy.wav")

    assert len(result) == 1
    assert result[0]["spk"] == 0
    fake_model.generate.assert_called_once()
