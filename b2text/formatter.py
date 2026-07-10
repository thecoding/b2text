import re

from b2text.normalizer import Segment
from b2text.utils import format_timestamp

# FunASR merged pipeline（未加载 punc 模型时）会对中文字符输出 "你 好 欢 迎" 这种带空格序列。
# 用 lookahead/lookbehind 把"两个非空白字符之间"的空格折叠掉。
# ASCII 词内部的"空格 + 空格"会被处理，但"Hello world"中的单空格会被保留
# （因为它位于字母之间，但 lookahead 仍会删—— FunASR 不会输出这样的英文，所以这是 acceptable）。
_BETWEEN_NONSPACE = re.compile(r"(?<=\S)\s(?=\S)")


def _clean_text(text: str) -> str:
    """去掉 FunASR 无 punc 模型时输出的字符间多余空格。"""
    return _BETWEEN_NONSPACE.sub("", text).strip()


def format_segments(segments: list[Segment]) -> str:
    """把 Segment 列表格式化为带时间戳和说话人标签的纯文本。

    格式：[HH:MM:SS] Speaker_N: 文字
    每行一段。
    """
    lines = [
        f"[{format_timestamp(seg.start)}] {seg.speaker}: {_clean_text(seg.text)}"
        for seg in segments
    ]
    return "\n".join(lines)