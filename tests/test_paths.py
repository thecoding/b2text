import os
from pathlib import Path

import pytest

from b2text.paths import (
    config_dir,
    data_dir,
    cookie_file,
    jobs_db,
    jobs_log,
    daemon_pid,
)


def test_config_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    d = config_dir()
    assert d == tmp_path / "xdg_config" / "b2text"


def test_data_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    d = data_dir()
    assert d == tmp_path / "xdg_data" / "b2text"


def test_falls_back_to_dot_config_and_dot_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".config" / "b2text"
    assert data_dir() == tmp_path / ".local" / "share" / "b2text"


def test_cookie_file_is_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cookie_file() == tmp_path / "b2text" / "cookie"


def test_jobs_db_and_log_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert jobs_db() == tmp_path / "b2text" / "jobs.db"
    assert jobs_log() == tmp_path / "b2text" / "jobs.log"
    assert daemon_pid() == tmp_path / "b2text" / "daemon.pid"
