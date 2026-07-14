import os
import pytest
from b2text.cookie_store import resolve_cookie, MissingCookieError


def test_resolves_cookie_from_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("SESSDATA=abc; bili_jct=xyz")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=abc; bili_jct=xyz"


def test_env_used_when_file_missing(monkeypatch, tmp_path):
    """文件不存在时退回到环境变量。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("B2TEXT_COOKIE", "SESSDATA=env_one")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=env_one"


def test_file_wins_when_both_present(monkeypatch, tmp_path):
    """文件存在时优先于环境变量（spec 约定的优先级）。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("SESSDATA=file_one")
    monkeypatch.setenv("B2TEXT_COOKIE", "SESSDATA=env_one")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=file_one"


def test_file_only_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("B2TEXT_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("SESSDATA=file_only")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=file_only"


def test_missing_cookie_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("B2TEXT_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(MissingCookieError, match="cookie"):
        resolve_cookie()


def test_empty_file_and_unset_env_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("B2TEXT_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("   \n")  # 只有空白
    with pytest.raises(MissingCookieError):
        resolve_cookie()


def test_trims_whitespace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("  SESSDATA=trimmed\n")
    assert resolve_cookie() == "SESSDATA=trimmed"