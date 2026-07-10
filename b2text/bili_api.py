# b2text/bili_api.py
"""B站 API 客户端（curl 风格，复用现有下载器的 cookie 和调用模式）。"""
import json
import subprocess
from typing import Any

COOKIE = (
    "buvid4=D21E6012-4A38-B23C-2BC5-7961BE48BEDE62503-024092215-3n5xeHPj8bn9aScYIf2pzg%3D%3D; "
    "SESSDATA=02e002c7%2C1778164764%2C4ad7b%2Ab1CjAFHRTtmUbXSwancqb8IOrEITiLH-OCPDF8YgnZZoJyUC4S2hy63a6JiY0UlRuu-lMSVnlzWVBxRUFOOUx3bmZQZEF1RnlnRGxhNlprLXZLejQwTmtvMWdjNm9mTldXd3M0ZHVCWGJVdzVmb2FuOGpkalc5WHhycnJQdDlKMFNzZ0U2TkZkN19RIIEC; "
    "bili_jct=169d89ed657d4564dd1e190a04ec1acd"
)

_USER_AGENT = "Mozilla/5.0"


def api_get(url: str, cookie: str = COOKIE) -> dict[str, Any]:
    """API GET 请求，返回 dict。失败返回 {}。"""
    cmd = [
        "curl", "-s", url,
        "-H", f"User-Agent: {_USER_AGENT}",
        "-H", f"Cookie: {cookie}",
        "--max-time", "20",
    ]
    result = subprocess.run(cmd, capture_output=True)
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


def get_video_info(bvid: str) -> dict[str, Any] | None:
    """获取视频信息（aid, title, pages, ugc_season）。失败返回 None。"""
    data = api_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
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


def get_audio_url(aid: int, cid: int) -> str | None:
    """获取音频流直链。失败返回 None。"""
    data = api_get(
        f"https://api.bilibili.com/x/player/playurl?avid={aid}&cid={cid}&qn=80&fnval=4048&fnver=0&fourk=1"
    )
    if data.get("code") != 0:
        return None
    audio_list = data.get("data", {}).get("dash", {}).get("audio", [])
    if not audio_list:
        return None
    return audio_list[0]["baseUrl"]


def extract_series_videos(ugc_season: dict | None) -> list[dict[str, Any]]:
    """从 ugc_season 提取所有视频（含 bvid, title, cid）。"""
    if not ugc_season:
        return []
    videos = []
    for section in ugc_season.get("sections", []):
        for ep in section.get("episodes", []):
            videos.append({
                "bvid": ep.get("bvid"),
                "title": ep.get("title", ""),
                "cid": ep.get("cid"),
            })
    return videos
