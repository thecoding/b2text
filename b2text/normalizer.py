from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Segment:
    """一段带说话人标签的转写结果。"""
    start: float       # 起始时间（秒）
    end: float         # 结束时间（秒）
    speaker: str       # "Speaker_1" / "Speaker_2" 等
    text: str          # 转写文字


# FunASR merged pipeline 的 sentence_info 返回 start/end 为毫秒；
# 旧的 narration pipeline（或非合并模式）可能返回秒。
# 用一个明确阈值判断：如果段内 max(start, end) > 10000，按毫秒处理。
_MS_THRESHOLD = 10_000


def normalize_funasr_output(raw: Iterable[dict]) -> list[Segment]:
    """把 FunASR AutoModel 的原始输出规整为 Segment 列表。

    - 跳过 text 为空或 spk 缺失的段
    - 按首次出现顺序把 spk 整数索引映射为 Speaker_N 标签
    - 自动把毫秒单位的 start/end 转为秒
    - 按 start 时间排序
    """
    speaker_map: dict[int, str] = {}
    next_idx = 1

    segments: list[Segment] = []
    for item in raw:
        # FunASR merged pipeline 用 "sentence"；narration pipeline 用 "text"
        text = (item.get("sentence") or item.get("text") or "").strip()
        spk = item.get("spk")
        if not text or spk is None:
            continue
        if spk not in speaker_map:
            speaker_map[spk] = f"Speaker_{next_idx}"
            next_idx += 1
        start_v = float(item["start"])
        end_v = float(item["end"])
        # 同一段内 start/end 单位必须一致。用二者最大值判断。
        if max(start_v, end_v) > _MS_THRESHOLD:
            start_v /= 1000.0
            end_v /= 1000.0
        segments.append(
            Segment(
                start=start_v,
                end=end_v,
                speaker=speaker_map[spk],
                text=text,
            )
        )

    segments.sort(key=lambda s: s.start)
    return segments
