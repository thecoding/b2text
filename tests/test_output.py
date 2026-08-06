"""tests/test_output.py — output_path_for_bvid 帮助函数。"""
from b2text.output import output_path_for_bvid


def test_returns_bvid_with_txt_extension():
    p = output_path_for_bvid("/tmp/out", "BV1abc")
    assert str(p) == "/tmp/out/BV1abc.txt"


def test_creates_path_object():
    from pathlib import Path
    p = output_path_for_bvid("/tmp/out", "BV1xyz")
    assert isinstance(p, Path)


def test_replaces_unsafe_chars_in_target_id():
    """bvid 不应该含非法字符，但保持清理规则：万一传入奇怪 target_id 也安全。"""
    p = output_path_for_bvid("/tmp/out", "BV1a/b?c")
    # / 与 ? 都被替换成 _
    assert "/" not in p.name
    assert "?" not in p.name
    assert p.name.endswith(".txt")


def test_truncates_long_target_id():
    long = "BV" + "x" * 200
    p = output_path_for_bvid("/tmp/out", long)
    assert len(p.stem) <= 80  # stem = file name without .txt


def test_accepts_pathlib_output_dir():
    from pathlib import Path
    p = output_path_for_bvid(Path("/tmp/out"), "BV1abc")
    assert str(p) == "/tmp/out/BV1abc.txt"