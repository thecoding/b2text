# b2text/bili_api.py
"""B站 API 客户端（httpx，统一 cookie 参数传递）。"""
from __future__ import annotations

from typing import Any

import httpx

from b2text.ratelimit import _BILI_BUCKET

# 完整 Chrome UA：B站 对短 UA / "Mozilla/5.0" 单独限速（视为 bot）。
# 用真浏览器指纹能绕过大部分反爬前置检查；其他 endpoint（nav/view）也能更稳。
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_API_TIMEOUT = 20.0


class BiliAPIError(RuntimeError):
    """B 站 API 返回非零 code 时抛出，携带 code/message 便于诊断与重试。"""

    def __init__(self, code: int, message: str, *, url: str):
        super().__init__(f"B站 API 错误：code={code}, message={message!r}")
        self.code = code
        self.message = message
        self.url = url


def _api_get(url: str, *, cookie: str) -> dict[str, Any]:
    """API GET 请求，返回 dict。失败返回 {}。"""
    _BILI_BUCKET.acquire()
    try:
        with httpx.Client(timeout=_API_TIMEOUT) as client:
            r = client.get(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Cookie": cookie,
                },
            )
            return r.json()
    except Exception:
        return {}


def get_video_info(bvid: str, *, cookie: str) -> dict[str, Any] | None:
    """获取视频信息（aid, title, pages, ugc_season）。失败返回 None。"""
    data = _api_get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        cookie=cookie,
    )
    if not data:
        return None
    if data.get("code") != 0:
        raise BiliAPIError(
            code=data.get("code", -1),
            message=data.get("message") or "(no message)",
            url=f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        )
    if "data" not in data:
        return None
    info = data["data"]
    return {
        "bvid": bvid,
        "aid": info["aid"],
        "title": info["title"],
        "owner": info["owner"]["name"],
        "pages": [
            {"cid": p["cid"], "title": p["part"], "page": p["page"]}
            for p in info["pages"]
        ],
        "videos": info.get("videos", 1),
        "ugc_season": info.get("ugc_season"),
    }


def get_audio_urls(aid: int, cid: int, *, cookie: str) -> list[str]:
    """获取音频流直链候选（baseUrl + backupUrl，去重）。失败返回 []。"""
    url = (
        f"https://api.bilibili.com/x/player/playurl?avid={aid}&cid={cid}"
        f"&qn=80&fnval=4048&fnver=0&fourk=1"
    )
    data = _api_get(
        url,
        cookie=cookie,
    )
    if not data:
        return None
    if data.get("code") != 0:
        raise BiliAPIError(
            code=data.get("code", -1),
            message=data.get("message") or "(no message)",
            url=url,
        )
    audio_list = data.get("data", {}).get("dash", {}).get("audio", [])
    if not audio_list:
        return []
    entry = audio_list[0]
    candidates: list[str] = [entry.get("baseUrl") or ""]
    for key in ("backupUrl", "backup_url"):
        backups = entry.get(key)
        if isinstance(backups, list):
            candidates.extend(b for b in backups if isinstance(b, str))
    seen: set[str] = set()
    urls: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            urls.append(c)
    return urls


def get_audio_url(aid: int, cid: int, *, cookie: str) -> str | None:
    """获取音频流直链（第一个候选）。失败返回 None。"""
    urls = get_audio_urls(aid, cid, cookie=cookie)
    return urls[0] if urls else None


def extract_series_videos(ugc_season: dict[str, Any] | None) -> list[dict[str, Any]]:
    """从 ugc_season 提取所有视频（含 aid/bvid/title/cid）。

    返回空列表当 ugc_season 缺失或没有可用的分集。
    """
    if not ugc_season:
        return []
    videos: list[dict[str, Any]] = []
    for section in ugc_season.get("sections", []):
        for ep in section.get("episodes", []):
            bvid = ep.get("bvid")
            if not bvid:
                continue
            videos.append({
                "bvid": bvid,
                "title": ep.get("title", ""),
                "cid": ep.get("cid"),
                "aid": ep.get("aid"),
            })
    return videos
