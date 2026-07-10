# b2text/diarizer.py
"""FunASR CAM++ 说话人日志独立封装。

CLI 默认走 transcriber.py 的合并 pipeline（同时返回文字和说话人）。
本模块提供独立的"纯说话人分段"API，供未来"对外部字幕做说话人归属"等场景使用。
"""
from pathlib import Path
from typing import Any


class FunASRDiarizer:
    """CAM++ 说话人日志独立封装。"""

    def __init__(
        self,
        spk_model: str = "cam++",
        device: str = "mps",
    ):
        self.spk_model_name = spk_model
        self.device = device
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        from funasr import AutoModel

        self._model = AutoModel(
            model=self.spk_model_name,
            device=self.device,
        )

    def diarize(self, wav_path: str | Path) -> list[dict[str, Any]]:
        """对 WAV 文件做纯说话人日志，返回带 spk 的 segment 列表。

        每个 segment: {"start": float, "end": float, "spk": int, "text": str (可能为空)}
        """
        self._load_model()
        result = self._model.generate(input=str(wav_path))
        if not result:
            return []
        return result[0].get("value", [])
