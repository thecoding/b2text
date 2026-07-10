# b2text/transcriber.py
"""FunASR ASR 封装（懒加载）。"""
from pathlib import Path
from typing import Any


class FunASRTranscriber:
    """Paraformer-large ASR + CAM++ 说话人日志 + FSMN-VAD 一体化封装。"""

    def __init__(
        self,
        asr_model: str = "paraformer-zh",
        vad_model: str = "fsmn-vad",
        spk_model: str = "cam++",
        device: str = "mps",
        spk_num: int | None = None,
    ):
        self.asr_model_name = asr_model
        self.vad_model_name = vad_model
        self.spk_model_name = spk_model
        self.device = device
        self.spk_num = spk_num
        self._model = None  # 懒加载

    def _load_model(self):
        """首次使用时加载 FunASR AutoModel。"""
        if self._model is not None:
            return
        from funasr import AutoModel  # 重型导入延迟到此处

        kwargs = dict(
            model=self.asr_model_name,
            vad_model=self.vad_model_name,
            spk_model=self.spk_model_name,
            device=self.device,
        )
        if self.spk_num is not None:
            kwargs["spk_num"] = self.spk_num
        self._model = AutoModel(**kwargs)

    def transcribe(self, wav_path: str | Path) -> list[dict[str, Any]]:
        """对 WAV 文件做 ASR + VAD + 说话人日志，返回原始 segment 列表。

        每个 segment 形如: {"start": float, "end": float, "sentence"/"text": str, "spk": int}
        """
        self._load_model()
        result = self._model.generate(input=str(wav_path))
        if not result:
            return []
        # FunASR merged pipeline 返回 sentence_info（每个元素 {start, end, sentence, spk, ...}）。
        # 旧版本或不同 pipeline 可能用 "value"，两个 key 都试一下。
        return result[0].get("sentence_info") or result[0].get("value") or []  # type: ignore[return-value]  # noqa: E501
