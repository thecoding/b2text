"""b2text 命令行入口。

子命令：
  serve start|stop|status|logs
  transcribe BV1xxx -o DIR
  transcribe --type up <uid> -o DIR --limit N
  status <task_id>
  list
  cancel <task_id>
  run <bvid> -o FILE    # 本地直跑，不走 daemon
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from b2text.client import (
    DEFAULT_BASE_URL, DaemonNotRunning,
    submit_bv, submit_up, get_task, list_tasks, cancel_task,
)
from b2text.cookie_store import MissingCookieError, resolve_cookie
from b2text.paths import config_dir, data_dir, daemon_pid


_BASE_URL = DEFAULT_BASE_URL


def _b2text_module_args() -> list[str]:
    return [sys.executable, "-m", "b2text.server"]


def _serve_start(args) -> int:
    pid_path = daemon_pid()
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            import os
            try:
                os.kill(existing_pid, 0)
                print(f"❌ daemon 已在运行（pid {existing_pid}）。先 `b2text serve stop`。")
                return 1
            except ProcessLookupError:
                pid_path.unlink()
        except ValueError:
            pid_path.unlink()

    try:
        resolve_cookie()
    except MissingCookieError as e:
        print(f"❌ {e}", flush=True)
        print(
            f"💡 提示：把 cookie 写入 {config_dir() / 'cookie'}（文件建议 chmod 600）\n"
            f"   内容：SESSDATA=xxx; bili_jct=xxx",
            flush=True,
        )
        return 4

    data_dir().mkdir(parents=True, exist_ok=True)
    log_path = data_dir() / "daemon.log"
    log_f = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        _b2text_module_args() + ["--port", str(args.port)],
        stdout=log_f, stderr=log_f,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid))
    print(f"✅ daemon 已启动（pid {proc.pid}，port {args.port}）")
    print(f"   日志：{log_path}")
    print(f"   pidfile：{pid_path}")
    return 0


def _serve_stop(args) -> int:
    pid_path = daemon_pid()
    if not pid_path.exists():
        print("❌ 没有 pidfile — daemon 未启动？")
        return 1
    pid = int(pid_path.read_text().strip())
    import os, signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"⚠️  进程 {pid} 不存在，清掉 pidfile")
        pid_path.unlink(missing_ok=True)
        return 0
    for _ in range(20):
        time.sleep(0.5)
        if not pid_path.exists():
            print(f"✅ daemon 已停止（pid {pid}）")
            return 0
    print(f"⚠️  30s 内未退出，尝试 SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    pid_path.unlink(missing_ok=True)
    return 0


def _serve_status(args) -> int:
    pid_path = daemon_pid()
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            import os
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            pid = None
            pid_path.unlink(missing_ok=True)
    if pid is None:
        print("daemon 未运行")
        return 0
    try:
        health = DaemonClient_shim().health()
        print(f"✅ daemon 正在运行（pid {pid}）")
        print(f"   ok={health.get('ok')}, model_loaded={health.get('model_loaded')}")
        print(f"   queue_len={health.get('queue_len')}, running={health.get('running')}")
    except (DaemonNotRunning, httpx.HTTPError):
        print(f"⚠️  pidfile 存在（pid {pid}）但端口不响应")
    return 0


def DaemonClient_shim():
    from b2text.client import DaemonClient
    return DaemonClient(_BASE_URL)


def _serve_logs(args) -> int:
    log = data_dir() / "daemon.log"
    n = args.n
    cmd = ["tail", "-n", str(n), "-F", str(log)]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


def _transcribe(args) -> int:
    if args.type == "bv":
        bvid = _normalize_bv(args.id_or_uid)
        try:
            tid = submit_bv(_BASE_URL, bvid, args.output)
        except (DaemonNotRunning, httpx.ConnectError):
            print("❌ daemon 未运行，先执行：b2text serve start", flush=True)
            return 1
        except Exception as e:
            print(f"❌ 提交失败：{e}", flush=True)
            return 1
    else:
        try:
            tid = submit_up(_BASE_URL, args.id_or_uid, args.output, limit=args.limit)
        except (DaemonNotRunning, httpx.ConnectError):
            print("❌ daemon 未运行，先执行：b2text serve start", flush=True)
            return 1
        except Exception as e:
            print(f"❌ 提交失败：{e}", flush=True)
            return 1
    print(f"✅ 任务已提交：{tid}")
    print(f"   查状态：b2text status {tid}")
    return 0


_BV_RE = re.compile(r"(BV[a-zA-Z0-9]+)")


def _normalize_bv(s: str) -> str:
    m = _BV_RE.search(s)
    return m.group(1) if m else s


def _status(args) -> int:
    try:
        job = get_task(_BASE_URL, args.task_id)
    except (DaemonNotRunning, httpx.ConnectError):
        print("❌ daemon 未运行，先执行：b2text serve start")
        return 1
    except Exception as e:
        print(f"❌ 查询失败：{e}")
        return 1
    print(f"任务 {job['id']}")
    print(f"  type: {job['type']}, target: {job['target_id']}")
    print(f"  status: {job['status']}")
    print(f"  output_dir: {job['output_dir']}")
    if job.get("result_path"):
        print(f"  result: {job['result_path']}")
    if job.get("error"):
        print(f"  error: {job['error']}")
    print(f"  created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['created_at']))}")
    if job.get("started_at"):
        print(f"  started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['started_at']))}")
    if job.get("finished_at"):
        print(f"  finished: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['finished_at']))}")
    return 0


def _list(args) -> int:
    try:
        data = list_tasks(_BASE_URL, status=args.status)
    except (DaemonNotRunning, httpx.ConnectError):
        print("❌ daemon 未运行，先执行：b2text serve start")
        return 1
    except Exception as e:
        print(f"❌ 查询失败：{e}")
        return 1
    rows = data.get("tasks", [])
    if not rows:
        print("（无任务）")
        return 0
    for row in rows:
        print(f"[{row['status']:>9}] {row['id'][:8]}.. {row['type']}/{row['target_id']}")
    return 0


def _cancel(args) -> int:
    try:
        cancel_task(_BASE_URL, args.task_id)
        print(f"✅ 已请求取消 {args.task_id}")
    except (DaemonNotRunning, httpx.ConnectError):
        print("❌ daemon 未运行，先执行：b2text serve start")
        return 1
    except Exception as e:
        print(f"❌ 取消失败：{e}")
        return 1
    return 0


def _run(args) -> int:
    """本地直跑（不通过 daemon）。"""
    from b2text.transcriber import FunASRTranscriber
    from b2text.normalizer import normalize_funasr_output
    from b2text.formatter import format_segments
    from b2text.audio import check_ffmpeg, download_audio_stream, ensure_wav
    from b2text import bili_api
    from b2text.utils import extract_bvid

    if not check_ffmpeg():
        print("❌ 未找到 ffmpeg。请先安装：brew install ffmpeg")
        return 3

    transcriber = FunASRTranscriber(device=args.device)
    output = Path(args.output)
    try:
        cookie = resolve_cookie()
    except MissingCookieError as e:
        print(f"❌ {e}")
        return 4

    bili_api.COOKIE = cookie

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        bvid = extract_bvid(args.id_or_uid)
        if not bvid:
            print(f"❌ 无法识别输入：{args.id_or_uid}")
            return 1
        info = bili_api.get_video_info(bvid)
        if not info:
            print(f"❌ 获取视频信息失败：{bvid}")
            return 1
        print(f"📺 {info['title']}")
        page = info["pages"][0]
        url = bili_api.get_audio_url(info["aid"], page["cid"])
        if not url:
            print("❌ 获取音频链接失败")
            return 1
        m4s_path = tmpdir / "audio.m4s"
        download_audio_stream(url, m4s_path, cookie=cookie)
        wav_path = ensure_wav(m4s_path, tmpdir)

        print("🎙️  开始转写…")
        raw = transcriber.transcribe(wav_path)
        segments = normalize_funasr_output(raw)
        text = format_segments(segments)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"✅ 已写入 {output}（{len(segments)} 段）")
    return 0


def JobStatus_str():
    from b2text.queue import JobStatus
    return list(JobStatus)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="b2text")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve")
    ssub = s.add_subparsers(dest="serve_cmd", required=True)
    p_start = ssub.add_parser("start")
    p_start.add_argument("--port", type=int, default=8765)
    p_start.set_defaults(func=_serve_start)
    p_stop = ssub.add_parser("stop")
    p_stop.set_defaults(func=_serve_stop)
    p_status = ssub.add_parser("status")
    p_status.set_defaults(func=_serve_status)
    p_logs = ssub.add_parser("logs")
    p_logs.add_argument("-n", type=int, default=50)
    p_logs.set_defaults(func=_serve_logs)

    pt = sub.add_parser("transcribe")
    pt.add_argument("id_or_uid")
    pt.add_argument("-o", "--output", required=True)
    pt.add_argument("--type", choices=["bv", "up"], default="bv")
    pt.add_argument("--limit", type=int, default=50)
    pt.set_defaults(func=_transcribe)

    pst = sub.add_parser("status")
    pst.add_argument("task_id")
    pst.set_defaults(func=_status)

    pl = sub.add_parser("list")
    pl.add_argument("--status", choices=JobStatus_str(), default=None)
    pl.set_defaults(func=_list)

    pc = sub.add_parser("cancel")
    pc.add_argument("task_id")
    pc.set_defaults(func=_cancel)

    pr = sub.add_parser("run")
    pr.add_argument("id_or_uid")
    pr.add_argument("-o", "--output", required=True)
    pr.add_argument("--device", default="mps", choices=["mps", "cpu"])
    pr.set_defaults(func=_run)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
