# b2text/transcriber.py
"""FunASR ASR 封装（懒加载）。"""
from pathlib import Path
from typing import Any

from b2text.audio import chunk_wav, get_wav_duration_seconds

# FunASR 在长音频上会偶发 segfault（实测 45 分钟 WAV 必崩）。
# 阈值以下的音频直接转写；超过则切成等长片段分别转写后合并。
_CHUNK_THRESHOLD_SECONDS = 600   # 10 分钟
_CHUNK_SIZE_SECONDS = 300        # 每片 5 分钟


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

    def _run_once(self, wav_path: Path) -> list[dict[str, Any]]:
        """单次 FunASR 调用，返回 raw sentence_info list（时间戳为原始单位）。"""
        self._load_model()
        result = self._model.generate(input=str(wav_path))
        if not result:
            return []
        return result[0].get("sentence_info") or result[0].get("value") or []  # type: ignore[return-value]  # noqa: E501

    @staticmethod
    def _to_seconds(value: float) -> float:
        """把毫秒转秒（实际单位判定在 _convert_segments 里按段内 max 决定）。"""
        return value / 1000.0

    def _convert_segments(self, segments: list[dict[str, Any]], offset: float) -> list[dict[str, Any]]:
        """把一段 raw segments 转为秒单位并加 offset。

        单位判定：FunASR merged pipeline 在同一段内 start/end 单位一致。
        若 max(start, end) > 10000，按毫秒处理；否则视为秒。
        """
        out = []
        for seg in segments:
            start_v = float(seg["start"])
            end_v = float(seg["end"])
            if max(start_v, end_v) > 10_000:
                start_v = self._to_seconds(start_v)
                end_v = self._to_seconds(end_v)
            out.append({**seg, "start": start_v + offset, "end": end_v + offset})
        return out

    def transcribe(self, wav_path: str | Path) -> list[dict[str, Any]]:
        """对 WAV 文件做 ASR + VAD + 说话人日志，返回原始 segment 列表。

        时间戳统一为秒（FunASR merged pipeline 默认返回毫秒，这里转换为秒）。
        长音频（>10 分钟）会被切成 5 分钟片段分别转写，合并时按偏移量修正时间戳。
        每个 segment 形如: {"start": float, "end": float, "sentence"/"text": str, "spk": int}
        """
        wav_path = Path(wav_path)
        try:
            duration = get_wav_duration_seconds(wav_path)
        except RuntimeError:
            duration = 0

        if duration <= _CHUNK_THRESHOLD_SECONDS:
            return self._convert_segments(self._run_once(wav_path), offset=0.0)

        # 长音频：切片分别转写
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            chunks = chunk_wav(wav_path, Path(tmpdir), _CHUNK_SIZE_SECONDS)
            merged: list[dict[str, Any]] = []
            for chunk_path, offset in chunks:
                merged.extend(self._convert_segments(self._run_once(chunk_path), offset))
            return merged
