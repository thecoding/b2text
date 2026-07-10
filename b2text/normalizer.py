from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Segment:
    """一段带说话人标签的转写结果。"""
    start: float       # 起始时间（秒）
    end: float         # 结束时间（秒）
    speaker: str       # "Speaker_1" / "Speaker_2" 等
    text: str          # 转写文字


def normalize_funasr_output(raw: Iterable[dict]) -> list[Segment]:
    """把 FunASR AutoModel 的原始输出规整为 Segment 列表。

    - 跳过 text 为空或 spk 缺失的段
    - 按首次出现顺序把 spk 整数索引映射为 Speaker_N 标签
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
        segments.append(
            Segment(
                start=float(item["start"]),
                end=float(item["end"]),
                speaker=speaker_map[spk],
                text=text,
            )
        )

    segments.sort(key=lambda s: s.start)
    return segments
