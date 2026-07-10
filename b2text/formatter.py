from b2text.normalizer import Segment
from b2text.utils import format_timestamp


def format_segments(segments: list[Segment]) -> str:
    """把 Segment 列表格式化为带时间戳和说话人标签的纯文本。

    格式：[HH:MM:SS] Speaker_N: 文字
    每行一段。
    """
    lines = [
        f"[{format_timestamp(seg.start)}] {seg.speaker}: {seg.text}"
        for seg in segments
    ]
    return "\n".join(lines)