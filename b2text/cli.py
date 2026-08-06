"""b2text 命令行入口。

子命令：
  serve start|stop|status|logs
  transcribe BV1xxx -o DIR
  transcribe --type up <uid> -o DIR --limit N
  status <task_id>
  list
  cancel <task_id>
  run <BV号|URL|本地mp4/wav> -o FILE [--device cpu] [--spk-num N] [--batch]
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
    cleanup_tasks,
)
from b2text.cookie_store import MissingCookieError, resolve_cookie
from b2text.paths import config_dir, data_dir, daemon_pid
from b2text import __version__


_BASE_URL = DEFAULT_BASE_URL


def _daemon_unreachable_message(e: BaseException) -> str:
    """统一处理 daemon 不可达的诊断信息。timeout 与连接被拒分别提示。"""
    if isinstance(e, httpx.TimeoutException):
        return (
            f"❌ 连接 daemon 超时（{type(e).__name__}: {e}）。\n"
            f"   这通常是 HTTP 代理劫持了 localhost 导致。绕开方法：\n"
            f"     export NO_PROXY=127.0.0.1,localhost\n"
            f"   然后重试。"
        )
    return "❌ daemon 未运行，先执行：b2text serve start"


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
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pid_path.unlink(missing_ok=True)
            print(f"✅ daemon 已停止（pid {pid}）")
            return 0
    print(f"⚠️  10s 内未退出，尝试 SIGKILL")
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
        except (DaemonNotRunning, httpx.RequestError) as e:
            print(_daemon_unreachable_message(e), flush=True)
            return 1
        except Exception as e:
            print(f"❌ 提交失败：{e}", flush=True)
            return 1
    else:
        try:
            tid = submit_up(
                _BASE_URL, args.id_or_uid, args.output,
                limit=args.limit, skip_existing=args.skip_existing,
            )
        except (DaemonNotRunning, httpx.RequestError) as e:
            print(_daemon_unreachable_message(e), flush=True)
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
    except (DaemonNotRunning, httpx.RequestError) as e:
        print(_daemon_unreachable_message(e))
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
        data = list_tasks(_BASE_URL, status=args.status,
                          uncompleted=args.uncompleted)
    except (DaemonNotRunning, httpx.RequestError) as e:
        print(_daemon_unreachable_message(e))
        return 1
    except Exception as e:
        print(f"❌ 查询失败：{e}")
        return 1
    rows = data.get("tasks", [])
    if not rows:
        print("（无任务）")
        return 0
    for row in rows:
        # progress = {step, msg} 或 None（queued 还没跑过 / 没有日志）
        prog = row.get("progress")
        if prog:
            step, msg = prog["step"], prog["msg"]
            if msg == "start":
                step_str = f"→ {step}"            # 正在跑
            elif msg == "ok":
                step_str = f"✓ {step}"            # 这步完成
            elif msg == "fail":
                step_str = f"✗ {step}"            # 这步失败
            else:
                step_str = f"  {step}"            # job_start / job_done 等
        else:
            step_str = "  -"
        print(f"[{row['status']:>9}] {row['id'][:8]}.. {step_str:<20} {row['type']}/{row['target_id']}")
    return 0


def _cancel(args) -> int:
    try:
        cancel_task(_BASE_URL, args.task_id)
        print(f"✅ 已请求取消 {args.task_id}")
    except (DaemonNotRunning, httpx.RequestError) as e:
        print(_daemon_unreachable_message(e))
        return 1
    except Exception as e:
        print(f"❌ 取消失败：{e}")
        return 1
    return 0


def _parse_duration(s: str) -> float:
    """把 '30d' / '24h' / '15m' 解析成秒数。"""
    if not s:
        raise ValueError("duration cannot be empty")
    unit = s[-1].lower()
    if unit not in "dhms":
        raise ValueError(f"duration must end with d/h/m/s, got {s!r}")
    try:
        n = float(s[:-1])
    except ValueError:
        raise ValueError(f"invalid duration: {s!r}")
    mult = {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return n * mult


def _clean(args) -> int:
    """删除任务。必须至少指定 --status / --older-than / --all 之一。"""
    if not args.status and not args.older_than and not args.all:
        print("❌ 必须指定至少一个过滤条件：--status / --older-than / --all")
        return 2

    older_than_seconds = None
    if args.older_than:
        try:
            older_than_seconds = _parse_duration(args.older_than)
        except ValueError as e:
            print(f"❌ {e}")
            return 2

    # --all 二次确认
    if args.all and not args.yes:
        from b2text.queue import JobQueue
        from b2text.paths import jobs_db
        try:
            q = JobQueue(jobs_db())
            total = q.count()
            q.close()
        except Exception:
            total = "?"
        print(f"⚠️  将删除全部 {total} 条任务。继续？[y/N] ", end="", flush=True)
        ans = input().strip().lower()
        if ans != "y":
            print("已取消。")
            return 0

    try:
        deleted = cleanup_tasks(
            _BASE_URL,
            status=args.status,
            older_than_seconds=older_than_seconds,
            all=args.all,
            cascade=not args.no_cascade,
        )
    except (DaemonNotRunning, httpx.RequestError) as e:
        print(_daemon_unreachable_message(e))
        return 1
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            print(f"❌ {e.response.json().get('detail', 'bad request')}")
            return 2
        print(f"❌ 清理失败：{e}")
        return 1
    except Exception as e:
        print(f"❌ 清理失败：{e}")
        return 1

    print(f"✅ 已清理 {deleted} 条任务")
    return 0


_LOCAL_SUFFIXES = (".mp4", ".wav", ".m4s")


def _is_local_path(s: str) -> bool:
    """判断是否是本地文件路径（不是 BV 号或 URL）。"""
    return Path(s).exists() or s.lower().endswith(_LOCAL_SUFFIXES)


def _safe_filename(title: str) -> str:
    """清理文件名非法字符。"""
    return re.sub(r'[<>:"/\\|?*]', "_", title)[:60]


def _transcribe_one(
    input_arg: str,
    output: Path,
    transcriber,
    cookie: str,
    *,
    overwrite: bool,
    keep_audio: bool,
    explicit: tuple[int | None, int | None] | None = None,
) -> bool:
    """处理单个视频/本地文件，返回成功与否。

    explicit=(aid, cid)：批量模式时由 extract_series_videos 提供，避免每集
    都重新拉一次 video info（也避免误用第一分页的 cid）。
    """
    from b2text import bili_api
    from b2text.audio import download_audio_stream, ensure_wav
    from b2text.formatter import format_segments
    from b2text.normalizer import normalize_funasr_output
    from b2text.utils import extract_bvid

    if output.exists() and not overwrite:
        print(f"⏭️  跳过已存在：{output}")
        return True

    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        if _is_local_path(input_arg):
            source = Path(input_arg)
            print(f"📂 使用本地文件：{source}")
            wav_path = ensure_wav(source, tmpdir)
        else:
            bvid = extract_bvid(input_arg)
            if not bvid:
                print(f"❌ 无法识别输入：{input_arg}")
                return False
            print(f"🔍 查询视频信息：{bvid}")
            try:
                info = bili_api.get_video_info(bvid, cookie=cookie)
            except bili_api.BiliAPIError as e:
                print(f"❌ 获取视频信息失败：{e}")
                return False
            if not info:
                print(f"❌ 获取视频信息失败：{bvid}")
                return False
            print(f"📺 {info['title']}")
            if explicit and explicit[1] is not None:
                aid, cid = explicit
            else:
                aid, cid = info["aid"], info["pages"][0]["cid"]
            try:
                url = bili_api.get_audio_url(aid, cid, cookie=cookie)
            except bili_api.BiliAPIError as e:
                print(f"❌ 获取音频链接失败：{e}")
                return False
            if not url:
                print("❌ 获取音频链接失败")
                return False
            m4s_path = tmpdir / "audio.m4s"
            try:
                download_audio_stream(url, m4s_path, cookie=cookie)
            except RuntimeError as e:
                print(f"❌ {e}")
                return False
            wav_path = ensure_wav(m4s_path, tmpdir)

        print("🎙️  开始转写…")
        try:
            raw = transcriber.transcribe(wav_path)
        except Exception as e:
            print(f"❌ 转写失败：{e}")
            return False
        segments = normalize_funasr_output(raw)
        text = format_segments(segments)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        if keep_audio:
            kept_wav = output.parent / f"{output.stem}.wav"
            shutil.copy2(wav_path, kept_wav)
            print(f"💾 已保留音频：{kept_wav}")

    print(f"✅ 已写入 {output}（{len(segments)} 段）")
    return True


def _run(args) -> int:
    """本地直跑（不通过 daemon）。"""
    from b2text.transcriber import FunASRTranscriber
    from b2text.audio import check_ffmpeg

    if not check_ffmpeg():
        print("❌ 未找到 ffmpeg。请先安装：brew install ffmpeg")
        return 3

    # 本地文件不需要 cookie；只有走 B 站 API 才要求。
    if not args.batch and _is_local_path(args.id_or_uid):
        cookie = ""
    else:
        try:
            cookie = resolve_cookie()
        except MissingCookieError as e:
            print(f"❌ {e}")
            return 4

    transcriber = FunASRTranscriber(device=args.device, spk_num=args.spk_num)
    output = Path(args.output)

    if args.batch:
        from b2text import bili_api
        from b2text.utils import extract_bvid
        bvid = extract_bvid(args.id_or_uid)
        if not bvid:
            print("❌ --batch 模式需要 BV 号或 URL")
            return 1
        try:
            info = bili_api.get_video_info(bvid, cookie=cookie)
        except bili_api.BiliAPIError as e:
            print(f"❌ 获取视频信息失败：{e}")
            return 1
        if not info:
            print(f"❌ 获取视频信息失败：{bvid}")
            return 1
        episodes = bili_api.extract_series_videos(info.get("ugc_season"))
        if not episodes:
            print(f"❌ {info['title']} 不是合集（无 ugc_season），无需 --batch")
            return 1
        output.mkdir(parents=True, exist_ok=True)
        ok = True
        for i, ep in enumerate(episodes, 1):
            print(f"\n[{i}/{len(episodes)}] {ep['title']}")
            out_file = output / f"{i:03d}_{_safe_filename(ep['title'])}.txt"
            if not _transcribe_one(
                ep["bvid"], out_file, transcriber, cookie,
                overwrite=not args.no_overwrite,
                keep_audio=args.keep_audio,
                explicit=(ep.get("aid"), ep.get("cid")),
            ):
                ok = False
        return 0 if ok else 1

    return 0 if _transcribe_one(
        args.id_or_uid, output, transcriber, cookie,
        overwrite=not args.no_overwrite,
        keep_audio=args.keep_audio,
    ) else 1


def JobStatus_str():
    from b2text.queue import JobStatus
    return list(JobStatus)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="b2text")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    pt.add_argument("--skip-existing", action="store_true",
                    help="UP 任务跳过 output_dir 下已存在的 .txt，避免重复转写")
    pt.set_defaults(func=_transcribe)

    pst = sub.add_parser("status")
    pst.add_argument("task_id")
    pst.set_defaults(func=_status)

    pl = sub.add_parser("list")
    pl.add_argument("--status", choices=JobStatus_str(), default=None)
    pl.add_argument("--uncompleted", action="store_true",
                    help="只列出未完成的任务（queued + running）")
    pl.set_defaults(func=_list)

    pc = sub.add_parser("cancel")
    pc.add_argument("task_id")
    pc.set_defaults(func=_cancel)

    pcl = sub.add_parser("clean")
    pcl.add_argument("--status", choices=JobStatus_str(), default=None,
                     help="只删除指定状态的任务")
    pcl.add_argument("--older-than", default=None,
                     help="删除 N 天前/小时前完成的，如 30d / 24h / 15m")
    pcl.add_argument("--all", action="store_true",
                     help="删除全部任务（需 --yes 或交互确认）")
    pcl.add_argument("--yes", action="store_true",
                     help="跳过 --all 的二次确认")
    pcl.add_argument("--no-cascade", action="store_true",
                     help="不级联删除 up 任务的 bv 子任务")
    pcl.set_defaults(func=_clean)

    pr = sub.add_parser("run")
    pr.add_argument("id_or_uid")
    pr.add_argument("-o", "--output", required=True)
    pr.add_argument("--device", default="mps", choices=["mps", "cpu"])
    pr.add_argument("--spk-num", type=int, default=None,
                    help="已知说话人数量（不指定则自动检测）")
    pr.add_argument("--no-overwrite", action="store_true",
                    help="不覆盖已存在的输出文件")
    pr.add_argument("--keep-audio", action="store_true",
                    help="在输出目录保留 wav 文件，便于复现或调试")
    pr.add_argument("--batch", action="store_true",
                    help="批量模式：处理 ugc_season 合集所有视频，输出到 -o 目录")
    pr.set_defaults(func=_run)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
