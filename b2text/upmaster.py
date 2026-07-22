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

    B 站单页上限 50 条；limit > 50 时自动翻页（pn=1, 2, ...）直到拿够或某页为空。
    每页 API 调用都会走 _BILI_BUCKET 限速（1 req/s）。

    参数：
        uid: UP 主 mid
        limit: 用户要的视频数（1+；翻页到 limit 或 B 站返回空为止）
        cookie: 完整 cookie 字符串

    返回：bvid 字符串列表（已按 B 站返回顺序，去重）。
    抛出 UpmasterAPIError 当 B 站返回 code != 0，或 HTTP/JSON 调用失败时。
    API 成功但 vlist 本身为空（UP 主真的没视频）则返回 []。
    """
    if limit < 1:
        return []
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://space.bilibili.com/",
    }
    bvids: list[str] = []
    seen: set[str] = set()
    pn = 1
    while len(bvids) < limit:
        ps = min(limit - len(bvids), 50)
        _BILI_BUCKET.acquire()
        url = f"{_SPACE_API}?mid={uid}&ps={ps}&pn={pn}&order=pubdate"
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
        if not vlist:
            break  # B 站返回空 → UP 主没更多视频了
        added_this_page = 0
        for v in vlist:
            bvid = v.get("bvid")
            if bvid and bvid not in seen:
                seen.add(bvid)
                bvids.append(bvid)
                added_this_page += 1
                if len(bvids) >= limit:
                    break
        # 这一页没有新增 bvid（全是重复）→ B 站翻到头也凑不够，停止
        if added_this_page == 0:
            break
        pn += 1
    return bvids[:limit]
