"""Cookie 读取（文件优先，B2TEXT_COOKIE 环境变量兑底）。

读取优先级（spec §4）：
  1. ~/.config/b2text/cookie（XDG 风格）
  2. B2TEXT_COOKIE 环境变量
  3. 都没有 → MissingCookieError
"""
import os

from b2text.paths import cookie_file


class MissingCookieError(RuntimeError):
    """未找到 cookie。请创建 ~/.config/b2text/cookie 或设置 B2TEXT_COOKIE。"""


def resolve_cookie() -> str:
    """返回单个 cookie 字符串（首位为 SESSDATA=...）。失败抛 MissingCookieError。"""
    path = cookie_file()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text

    env_cookie = os.environ.get("B2TEXT_COOKIE")
    if env_cookie and env_cookie.strip():
        return env_cookie.strip()

    raise MissingCookieError(
        f"未找到 cookie。请在 {path} 写入 SESSDATA=...; bili_jct=...，"
        f"或设置环境变量 B2TEXT_COOKIE。"
    )