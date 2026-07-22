# b2text/bili_api.py
"""B站 API 客户端（httpx，统一 cookie 参数传递）。"""
from __future__ import annotations

import json
from typing import Any

import httpx

_USER_AGENT = "Mozilla/5.0"
_API_TIMEOUT = 20.0


def _api_get(url: str, *, cookie: str) -> dict[str, Any]:
    """API GET 请求，返回 dict。失败返回 {}。"""
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
    if data.get("code") != 0 or "data" not in data:
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


def get_audio_url(aid: int, cid: int, *, cookie: str) -> str | None:
    """获取音频流直链。失败返回 None。"""
    data = _api_get(
        f"https://api.bilibili.com/x/player/playurl?avid={aid}&cid={cid}&qn=80&fnval=4048&fnver=0&fourk=1",
        cookie=cookie,
    )
    if data.get("code") != 0:
        return None
    audio_list = data.get("data", {}).get("dash", {}).get("audio", [])
    if not audio_list:
        return None
    return audio_list[0]["baseUrl"]
