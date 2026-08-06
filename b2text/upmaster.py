# b2text/upmaster.py
"""UP 主视频列表抓取 + 批量展开为 bvid 列表。"""
from __future__ import annotations

import httpx

from b2text.bili_api import _USER_AGENT
from b2text.ratelimit import _BILI_BUCKET
from b2text.wbi import sign_query

_SPACE_API = "https://api.bilibili.com/x/space/wbi/arc/search"


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
    """调用 B 站 space/wbi/arc/search，返回最多 limit 条 bvid。

    B 站有两个等价 endpoint：
      - /x/space/arc/search（旧）：对 ps>=20 直接 -799，且 IP+账号冷却期长
      - /x/space/wbi/arc/search（新）：要求 wbi 签名，ps=50 全开，长期稳定可用

    我们用新版 + wbi 签名。单页最多 50 条；limit > 50 时自动翻页（pn=1, 2, ...）直到拿够或某页为空。
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
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://space.bilibili.com/",
    }
    bvids: list[str] = []
    seen: set[str] = set()
    pn = 1
    while len(bvids) < limit:
        ps = min(limit - len(bvids), 50)
        _BILI_BUCKET.acquire()
        # wbi 签名：B站 从 2023 起对 space/arc/search 强制要求 wts + w_rid，
        # 不签名直接 -799。sign_query 内部会缓存 img/sub key。
        signed = sign_query(
            {"mid": str(uid), "ps": str(ps), "pn": str(pn), "order": "pubdate"},
            cookie=cookie, ua=_USER_AGENT,
        )
        qs = "&".join(f"{k}={v}" for k, v in signed.items())
        url = f"{_SPACE_API}?{qs}"
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
