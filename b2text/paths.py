"""XDG 风格路径解析，daemon 数据均放这里。"""
import os
from pathlib import Path

_APP = "b2text"


def config_dir() -> Path:
    """返回 config 目录（cookie、pidfile 等）。"""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "~"), ".config"
    )
    return Path(os.path.expanduser(base)) / _APP


def data_dir() -> Path:
    """返回 data 目录（jobs.db、jobs.log、daemon log）。"""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.environ.get("HOME", "~"), ".local", "share"
    )
    return Path(os.path.expanduser(base)) / _APP


def cookie_file() -> Path:
    return config_dir() / "cookie"


def jobs_db() -> Path:
    return data_dir() / "jobs.db"


def jobs_log() -> Path:
    return data_dir() / "jobs.log"


def daemon_pid() -> Path:
    return config_dir() / "daemon.pid"
