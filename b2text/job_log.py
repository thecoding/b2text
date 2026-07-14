"""结构化 JSON Lines 日志：每行一个 dict，必含 ts/level/job_id/step/msg。

- 失败时 extra 必填 exc_type / exc_message / stacktrace
- 支持 StepLogger 上下文管理器（自动捕获异常）
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Self


_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


class JobLog:
    """单一 job 的结构化日志写入器。线程不安全（每 job 实例化一次即可）。"""

    def __init__(self, log_path: Path, *, job_id: str):
        self.log_path = log_path
        self.job_id = job_id
        # 不创建父目录 — daemon 启动时统一建好
        # 暂存 set() 的字段，在下一次 step_start() 时合并进 extra
        self._pending: dict[str, Any] = {}

    def set(self, **kwargs: Any) -> "JobLog":
        """暂存字段，供下一次 step_start（或 step_ok/step_fail）合并到 extra。"""
        self._pending.update(kwargs)
        return self

    def _drain_pending(self) -> dict[str, Any]:
        merged = dict(self._pending)
        self._pending.clear()
        return merged

    def _emit(self, level: str, step: str, msg: str, extra: dict[str, Any] | None) -> None:
        ts = datetime.now(timezone.utc).strftime(_TS_FORMAT)[:-3] + "Z"
        record = {
            "ts": ts,
            "level": level,
            "job_id": self.job_id,
            "step": step,
            "msg": msg,
            "extra": extra or {},
        }
        line = json.dumps(record, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 实时同步到 stderr（daemon 模式不依赖 stdout）
        print(line, file=sys.stderr, flush=True)

    def info(self, msg: str, *, step: str = "_", extra: dict[str, Any] | None = None) -> None:
        self._emit("INFO", step, msg, extra)

    def warn(self, msg: str, *, step: str = "_", extra: dict[str, Any] | None = None) -> None:
        self._emit("WARNING", step, msg, extra)

    def error(self, msg: str, *, step: str = "_", extra: dict[str, Any] | None = None) -> None:
        self._emit("ERROR", step, msg, extra)

    def step_start(self, step: str, **extra: Any) -> None:
        merged = self._drain_pending()
        merged.update(extra)
        self.info("start", step=step, extra=merged)

    def step_ok(self, step: str, **extra: Any) -> None:
        self.info("ok", step=step, extra=extra)

    def step_fail(
        self,
        step: str,
        *,
        exc_info: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(extra or {})
        if exc_info:
            et, ev, tb = sys.exc_info()
            if et is not None:
                merged["exc_type"] = et.__name__
                merged["exc_message"] = str(ev) if ev else ""
                merged["stacktrace"] = "".join(
                    traceback.format_exception(et, ev, tb)
                )
        self.error("fail", step=step, extra=merged)

    def step(self, name: str) -> "StepLogger":
        return StepLogger(self, name)


class StepLogger:
    """上下文管理器，自动在 exit 时根据是否异常决定 step_ok 或 step_fail。"""

    def __init__(self, log: JobLog, name: str):
        self._log = log
        self._name = name
        self._extra: dict[str, Any] = {}

    def set(self, **kwargs: Any) -> Self:
        self._extra.update(kwargs)
        return self

    def __enter__(self) -> Self:
        self._log.step_start(self._name, **self._extra)
        self._extra.clear()  # start 阶段记录 context；ok/fail 只记结果字段
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._log.step_ok(self._name, **self._extra)
        else:
            self._log.step_fail(self._name, exc_info=True, extra=self._extra)
