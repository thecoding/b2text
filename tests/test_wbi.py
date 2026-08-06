"""tests/test_wbi.py — B站 wbi 签名（mixin_key + w_rid）。"""
import hashlib
from b2text.wbi import mixin_key, sign_params, _url_encode


def _fake_keys():
    """B站 真实 img_key / sub_key 各 32 字符（UUID 无连字符）。"""
    return "a" * 32, "b" * 32


def test_url_encode_strips_filter_chars():
    """B站 编码要过滤掉 ! ' ( ) * 的 percent-encoded 形式。"""
    assert _url_encode("hello!world") == "helloworld"      # %21 去掉
    assert _url_encode("a'b") == "ab"                       # %27 去掉
    assert _url_encode("(x*y)") == "xy"                     # %28 %2A %29 都去掉
    assert _url_encode("normal") == "normal"
    assert _url_encode("a/b") == "a%2Fb"                    # / 保留编码


def test_mixin_key_is_64_chars():
    img, sub = _fake_keys()
    raw = img + sub
    assert len(raw) == 64
    out = mixin_key(img, sub)
    assert len(out) == 32  # mixin_table has 32 entries
    # 取的是 raw 的固定位置
    for i, ch in zip(
        (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
         27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13),
        out,
    ):
        assert ch == raw[i]


def test_mixin_key_uses_both_keys():
    img, sub = _fake_keys()
    img2 = "B" * 32
    assert mixin_key(img, sub) != mixin_key(img2, sub)
    sub2 = "X" * 32
    assert mixin_key(img, sub) != mixin_key(img, sub2)


def test_sign_params_adds_wts_and_w_rid():
    img, sub = _fake_keys()
    signed = sign_params({"mid": "12345", "ps": "1", "pn": "1"}, img_key=img, sub_key=sub)
    assert "wts" in signed
    assert "w_rid" in signed
    assert signed["mid"] == "12345"
    assert signed["ps"] == "1"
    assert signed["pn"] == "1"


def test_sign_params_w_rid_is_md5_of_canonical_query():
    img, sub = _fake_keys()
    mixin = mixin_key(img, sub)
    params = {"mid": "486325909", "ps": "1", "pn": "1"}
    signed = sign_params(params, img_key=img, sub_key=sub)
    # 手算期望的 w_rid：排序后 url-encode + wts + mixin_key → MD5
    sorted_items = sorted(params.items())
    parts = [f"{_url_encode(k)}={_url_encode(v)}" for k, v in sorted_items]
    parts.append(f"wts={signed['wts']}")
    canonical = "&".join(parts) + mixin
    expected = hashlib.md5(canonical.encode("utf-8")).hexdigest()
    assert signed["w_rid"] == expected


def test_sign_params_sorts_keys_alphabetically():
    """乱序传入也会按字母序拼，保证 w_rid 稳定。"""
    img, sub = _fake_keys()
    a = sign_params({"b": "2", "a": "1", "c": "3"}, img_key=img, sub_key=sub)
    b = sign_params({"c": "3", "a": "1", "b": "2"}, img_key=img, sub_key=sub)
    assert a["w_rid"] == b["w_rid"]


def test_sign_params_does_not_mutate_input():
    img, sub = _fake_keys()
    params = {"a": "1", "b": "2"}
    snapshot = dict(params)
    sign_params(params, img_key=img, sub_key=sub)
    assert params == snapshot


def test_wts_is_unix_timestamp_int():
    import time as _time
    img, sub = _fake_keys()
    before = int(_time.time())
    signed = sign_params({"x": "1"}, img_key=img, sub_key=sub)
    after = int(_time.time())
    wts = int(signed["wts"])
    assert before <= wts <= after