# b2text/upmaster.py
"""UP 主视频列表抓取 + 批量展开为 bvid 列表。"""
from __future__ import annotations

import httpx

from b2text.ratelimit import _BILI_BUCKET

_SPACE_API = "https://api.bilibili.com/x/space/arc/search"


class UpmasterAPIError(RuntimeError):
    """B 站 space/arc/search 返回非零 code 或 HTTP/JSON 解析失败时抛。透传 code/message。

    用 RuntimeError 作基类是为了让 worker._with_api_retry 能直接 catch 重试。
    """

    def __init__(self, code: int, message: str, *, uid: int):
        super().__init__(f"B站 API 错误：code={code}, message={message!r} (uid={uid})")
        self.code = code
        self.message = message
        self.uid = uid


def fetch_up_videos(uid: int, limit: int, *, cookie: str) -> list[str]:
    """调用 B 站 space/arc/search，返回最多 limit 条 bvid。

    参数：
        uid: UP 主 mid
        limit: 用户要的视频数（1-50，B 站单页最多 50）
        cookie: 完整 cookie 字符串

    返回：bvid 字符串列表（已按 B 站返回顺序）。
    抛出 UpmasterAPIError 当 B 站返回 code != 0，或 HTTP/JSON 调用失败时。
    API 成功但 vlist 本身为空（UP 主真的没视频）则返回 []。
    """
    if limit < 1:
        return []
    _BILI_BUCKET.acquire()
    ps = min(limit, 50)
    url = f"{_SPACE_API}?mid={uid}&ps={ps}&pn=1&order=pubdate"
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://space.bilibili.com/",
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(url, headers=headers)
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise UpmasterAPIError(
            code=-1, message=f"HTTP/JSON 错误：{type(e).__name__}: {e}", uid=uid,
        ) from e
    if data.get("code") != 0:
        raise UpmasterAPIError(
            code=data.get("code", -1),
            message=data.get("message") or "(no message)",
            uid=uid,
        )
    vlist = data.get("data", {}).get("list", {}).get("vlist", [])
    bvids = [v.get("bvid") for v in vlist if v.get("bvid")]
    return bvids[:limit]
