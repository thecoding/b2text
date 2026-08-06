"""B站 wbi 签名 — `space/arc/search` 等接口 2023 起强制要求 wts + w_rid。

参考 https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md

签名流程：
  1. 调 /x/web-interface/nav 取 wbi_img.img_url / sub_url，提取文件名当 img_key / sub_key
  2. mixin_key = (img_key + sub_key) 按固定 32 位表挑出前 64 个字符
  3. 把要发的 query params 排序后 url-encode（去掉 !'()*），再拼上 wts
  4. w_rid = md5(排序后字符串 + mixin_key)
  5. 请求里附加 wts + w_rid
"""
from __future__ import annotations

import hashlib
import threading
import time
from urllib.parse import quote, urlencode

import httpx


# 取 raw = img_key + sub_key 的固定 32 个位置，拼成 64 字符 mixin_key
_MIXIN_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
)

# 编码时要去掉的字符（B站 自己规定的过滤）：实际是去掉这些字符的 percent-encoded 形式
_FILTER_ENCODED = ("%21", "%27", "%28", "%29", "%2A")

_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
_KEY_TTL_SECONDS = 12 * 3600  # B站 一天换一次 key，缓存 12 小时保险


class WbiKeys:
    """缓存 img_key / sub_key，自动过期刷新（线程安全）。"""

    def __init__(self) -> None:
        self._img_key: str = ""
        self._sub_key: str = ""
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    def get(self, *, cookie: str, ua: str) -> tuple[str, str]:
        """返回 (img_key, sub_key)，过期则重新拉。"""
        now = time.time()
        if self._img_key and now - self._fetched_at < _KEY_TTL_SECONDS:
            return self._img_key, self._sub_key
        # 加锁防止并发刷新；double-check 避免拿到锁后发现已被别的线程刷新过。
        with self._lock:
            now = time.time()
            if self._img_key and now - self._fetched_at < _KEY_TTL_SECONDS:
                return self._img_key, self._sub_key
            self._refresh(cookie=cookie, ua=ua)
            return self._img_key, self._sub_key

    def _refresh(self, *, cookie: str, ua: str) -> None:
        r = httpx.get(
            _NAV_URL,
            headers={"Cookie": cookie, "User-Agent": ua},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        wbi_img = data.get("wbi_img", {})
        img_url = wbi_img.get("img_url", "")
        sub_url = wbi_img.get("sub_url", "")
        if not img_url or not sub_url:
            raise RuntimeError(
                f"wbi keys 解析失败：nav 返回没有 wbi_img（cookie 可能失效）"
            )
        self._img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        self._sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        self._fetched_at = time.time()


def mixin_key(img_key: str, sub_key: str) -> str:
    """从 img_key + sub_key 拼出 64 字符 mixin_key。"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in _MIXIN_TABLE)


def _url_encode(value: str) -> str:
    """B站 签名用的编码：quote 后去掉 !'()* 的 percent-encoded 形式。"""
    encoded = quote(value, safe="")
    for tag in _FILTER_ENCODED:
        encoded = encoded.replace(tag, "")
    return encoded


def sign_params(params: dict[str, str], *, img_key: str, sub_key: str) -> dict[str, str]:
    """给 query params 加 wts + w_rid。

    入参 params 不应含 wts / w_rid（会被覆盖）。
    返回新 dict，原 dict 不改。
    """
    mixin = mixin_key(img_key, sub_key)
    sorted_items = sorted(params.items())
    encoded_parts = []
    for k, v in sorted_items:
        encoded_parts.append(f"{_url_encode(k)}={_url_encode(v)}")
    wts = str(int(time.time()))
    encoded_parts.append(f"wts={wts}")
    query = "&".join(encoded_parts)
    w_rid = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return {**dict(sorted_items), "wts": wts, "w_rid": w_rid}


# 单例：进程内共享一份 wbi keys
_wbi_keys = WbiKeys()


def sign_query(params: dict[str, str], *, cookie: str, ua: str) -> dict[str, str]:
    """便捷封装：自动取/缓存 keys，返回带 wts+w_rid 的新 params。"""
    img_key, sub_key = _wbi_keys.get(cookie=cookie, ua=ua)
    return sign_params(params, img_key=img_key, sub_key=sub_key)
