from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    """一段带说话人标签的转写结果。"""
    start: float       # 起始时间（秒）
    end: float         # 结束时间（秒）
    speaker: str       # "Speaker_1" / "Speaker_2" 等
    text: str          # 转写文字