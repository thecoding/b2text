"""tests/test_cli.py — CLI 入口：parser 构建 + 核心辅助函数。"""
import sys
import pytest
from b2text.cli import build_parser, _normalize_bv, _b2text_module_args


class TestB2textModuleArgs:
    def test_returns_python_module_invocation(self):
        args = _b2text_module_args()
        assert "-m" in args
        assert "b2text.server" in args


class TestNormalizeBv:
    def test_extracts_bv_from_pure_id(self):
        assert _normalize_bv("BV1test123") == "BV1test123"

    def test_extracts_bv_from_full_url(self):
        url = "https://www.bilibili.com/video/BV1GJ411k7qE"
        assert _normalize_bv(url) == "BV1GJ411k7qE"

    def test_extracts_bv_from_short_url(self):
        url = "https://b23.tv/BV1xx"
        assert _normalize_bv(url) == "BV1xx"

    def test_case_insensitive_prefix(self):
        assert _normalize_bv("bv1test123") == "bv1test123"

    def test_returns_input_if_no_match(self):
        assert _normalize_bv("not-a-bv") == "not-a-bv"
        assert _normalize_bv("") == ""


class TestBuildParser:
    def test_serve_start(self):
        p = build_parser()
        args = p.parse_args(["serve", "start", "--port", "8888"])
        assert args.command == "serve"
        assert args.serve_cmd == "start"
        assert args.port == 8888
        assert callable(args.func)

    def test_serve_start_default_port(self):
        p = build_parser()
        args = p.parse_args(["serve", "start"])
        assert args.port == 8765

    def test_serve_stop(self):
        p = build_parser()
        args = p.parse_args(["serve", "stop"])
        assert args.serve_cmd == "stop"
        assert callable(args.func)

    def test_serve_status(self):
        p = build_parser()
        args = p.parse_args(["serve", "status"])
        assert args.serve_cmd == "status"

    def test_serve_logs_default(self):
        p = build_parser()
        args = p.parse_args(["serve", "logs"])
        assert args.n == 50

    def test_serve_logs_custom_n(self):
        p = build_parser()
        args = p.parse_args(["serve", "logs", "-n", "100"])
        assert args.n == 100

    def test_transcribe_bv(self):
        p = build_parser()
        args = p.parse_args(["transcribe", "BV1xxx", "-o", "/tmp/out"])
        assert args.command == "transcribe"
        assert args.id_or_uid == "BV1xxx"
        assert args.output == "/tmp/out"
        assert args.type == "bv"
        assert callable(args.func)

    def test_transcribe_up(self):
        p = build_parser()
        args = p.parse_args([
            "transcribe", "12345", "-o", "/tmp/out",
            "--type", "up", "--limit", "20",
        ])
        assert args.type == "up"
        assert args.id_or_uid == "12345"
        assert args.limit == 20
        assert args.skip_existing is False

    def test_transcribe_up_skip_existing(self):
        """--skip-existing 解析为 True 并透传给 submit_up。"""
        p = build_parser()
        args = p.parse_args([
            "transcribe", "12345", "-o", "/tmp/out",
            "--type", "up", "--limit", "20", "--skip-existing",
        ])
        assert args.skip_existing is True

    def test_transcribe_bv_force(self):
        """--force 解析为 True，用于跳过服务端重复检测。"""
        p = build_parser()
        args = p.parse_args([
            "transcribe", "BV1xxx", "-o", "/tmp/out", "--force",
        ])
        assert args.force is True
        assert args.type == "bv"

    def test_status(self):
        p = build_parser()
        args = p.parse_args(["status", "abc-123"])
        assert args.task_id == "abc-123"
        assert callable(args.func)

    def test_list_default(self):
        p = build_parser()
        args = p.parse_args(["list"])
        assert args.status is None

    def test_list_with_status_filter(self):
        p = build_parser()
        args = p.parse_args(["list", "--status", "done"])
        assert args.status == "done"

    def test_cancel(self):
        p = build_parser()
        args = p.parse_args(["cancel", "task-1"])
        assert args.task_id == "task-1"

    def test_run_default_device(self):
        p = build_parser()
        args = p.parse_args(["run", "BV1xxx", "-o", "/tmp/out.txt"])
        assert args.device == "mps"

    def test_run_custom_device(self):
        p = build_parser()
        args = p.parse_args(["run", "BV1xxx", "-o", "/tmp/out.txt", "--device", "cpu"])
        assert args.device == "cpu"

    def test_run_full_flags(self):
        """run 应支持 --spk-num / --no-overwrite / --keep-audio / --batch。"""
        p = build_parser()
        args = p.parse_args([
            "run", "BV1xxx", "-o", "/tmp/out", "--spk-num", "3",
            "--no-overwrite", "--keep-audio", "--batch",
        ])
        assert args.spk_num == 3
        assert args.no_overwrite is True
        assert args.keep_audio is True
        assert args.batch is True

    def test_run_defaults(self):
        p = build_parser()
        args = p.parse_args(["run", "BV1xxx", "-o", "/tmp/out.txt"])
        assert args.spk_num is None
        assert args.no_overwrite is False
        assert args.keep_audio is False
        assert args.batch is False

    def test_serve_requires_subcommand(self):
        """'b2text serve' 不带子命令应报错。"""
        p = build_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["serve"])

    def test_transcribe_requires_output(self):
        """'transcribe' 不传 -o/--output 应报错。"""
        p = build_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["transcribe", "BV1xxx"])


class TestCleanSubcommand:
    def test_clean_accepts_status(self):
        from b2text.cli import build_parser
        p = build_parser()
        ns = p.parse_args(["clean", "--status", "failed"])
        assert ns.status == "failed"
        assert ns.older_than is None
        assert ns.all is False

    def test_clean_accepts_older_than(self):
        from b2text.cli import build_parser
        p = build_parser()
        ns = p.parse_args(["clean", "--older-than", "30d"])
        assert ns.older_than == "30d"
        assert ns.all is False

    def test_clean_accepts_all_with_yes(self):
        from b2text.cli import build_parser
        p = build_parser()
        ns = p.parse_args(["clean", "--all", "--yes"])
        assert ns.all is True
        assert ns.yes is True


class TestParseDuration:
    def test_days(self):
        from b2text.cli import _parse_duration
        assert _parse_duration("7d") == 7 * 86400

    def test_hours(self):
        from b2text.cli import _parse_duration
        assert _parse_duration("24h") == 24 * 3600

    def test_minutes(self):
        from b2text.cli import _parse_duration
        assert _parse_duration("15m") == 15 * 60

    def test_seconds(self):
        from b2text.cli import _parse_duration
        assert _parse_duration("90s") == 90

    def test_rejects_unknown_unit(self):
        from b2text.cli import _parse_duration
        with pytest.raises(ValueError):
            _parse_duration("5y")

    def test_rejects_bad_format(self):
        from b2text.cli import _parse_duration
        with pytest.raises(ValueError):
            _parse_duration("d")


class TestCleanNoFilter:
    """不传任何过滤条件 → 拒绝并返回 2（避免误删）。"""

    def test_no_args_returns_2(self, monkeypatch):
        from b2text.cli import build_parser, _clean
        import io
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        ns = build_parser().parse_args(["clean"])
        rc = _clean(ns)
        assert rc == 2
        assert "必须指定" in buf.getvalue()


class TestRunLocalFile:
    """b2text run 处理本地文件：转写、no-overwrite 跳过、keep-audio 复制。"""

    def _setup(self, monkeypatch, tmp_path):
        import b2text.transcriber as tmod
        from b2text.cli import build_parser, _run

        wav = tmp_path / "input.wav"
        wav.write_bytes(b"RIFFxxxx")
        out = tmp_path / "out.txt"

        state = {"transcribe_calls": 0}

        class FakeTranscriber:
            def __init__(self, **kwargs):
                state["kwargs"] = kwargs

            def transcribe(self, path):
                state["transcribe_calls"] += 1
                return [{"start": 0, "end": 1.0, "sentence": "你好世界", "spk": 0}]

        monkeypatch.setattr(tmod, "FunASRTranscriber", FakeTranscriber)
        monkeypatch.setattr("b2text.audio.check_ffmpeg", lambda: True)
        return _run, build_parser(), wav, out, state

    def test_local_wav_writes_output_and_keeps_audio(self, monkeypatch, tmp_path):
        _run, parser, wav, out, state = self._setup(monkeypatch, tmp_path)
        ns = parser.parse_args([
            "run", str(wav), "-o", str(out),
            "--device", "cpu", "--spk-num", "2", "--keep-audio",
        ])
        assert _run(ns) == 0
        assert state["kwargs"] == {"device": "cpu", "spk_num": 2}
        text = out.read_text(encoding="utf-8")
        assert "[00:00:00] Speaker_1: 你好世界" in text
        kept = tmp_path / "out.wav"
        assert kept.exists()

    def test_local_wav_no_overwrite_skips(self, monkeypatch, tmp_path):
        _run, parser, wav, out, state = self._setup(monkeypatch, tmp_path)
        out.write_text("已存在", encoding="utf-8")
        ns = parser.parse_args([
            "run", str(wav), "-o", str(out), "--no-overwrite",
        ])
        assert _run(ns) == 0
        assert state["transcribe_calls"] == 0
        assert out.read_text(encoding="utf-8") == "已存在"


class TestLegacyShim:
    """bilibili_to_text.py 旧用法应自动补 run 子命令。"""

    def _main(self, monkeypatch, argv):
        import bilibili_to_text
        captured = {}
        real_main = bilibili_to_text.cli_main

        def fake_cli_main():
            captured["argv"] = list(sys.argv[1:])
            return 0

        monkeypatch.setattr(bilibili_to_text, "cli_main", fake_cli_main)
        try:
            rc = bilibili_to_text.main(argv)
        finally:
            monkeypatch.setattr(bilibili_to_text, "cli_main", real_main)
        return rc, captured

    def test_legacy_args_get_run_prepended(self, monkeypatch):
        rc, captured = self._main(monkeypatch, ["BV1xxx", "-o", "/tmp/x.txt"])
        assert rc == 0
        assert captured["argv"] == ["run", "BV1xxx", "-o", "/tmp/x.txt"]

    def test_known_subcommand_passes_through(self, monkeypatch):
        rc, captured = self._main(monkeypatch, ["serve", "status"])
        assert rc == 0
        assert captured["argv"] == ["serve", "status"]

    def test_empty_argv_passes_through(self, monkeypatch):
        rc, captured = self._main(monkeypatch, [])
        assert rc == 0
        assert captured["argv"] == []


class TestRunBatch:
    """b2text run --batch 展开 ugc_season，每集一个 txt。"""

    def test_batch_writes_one_file_per_episode(self, monkeypatch, tmp_path):
        from b2text import bili_api
        import b2text.transcriber as tmod
        from b2text.cli import build_parser, _run

        out_dir = tmp_path / "texts"
        calls = {"transcribe": 0, "download": 0}
        fake_info = {
            "title": "测试合集",
            "ugc_season": {"sections": [{"episodes": [
                {"bvid": "BV1a", "title": "第1集", "cid": 101, "aid": 1},
                {"bvid": "BV1b", "title": "第2集", "cid": 102, "aid": 2},
            ]}]},
        }

        class FakeTranscriber:
            def __init__(self, **kwargs):
                pass

            def transcribe(self, path):
                calls["transcribe"] += 1
                return [{"start": 0, "end": 1.0, "sentence": "转写内容", "spk": 0}]

        monkeypatch.setattr(tmod, "FunASRTranscriber", FakeTranscriber)
        monkeypatch.setattr("b2text.audio.check_ffmpeg", lambda: True)
        monkeypatch.setattr(
            bili_api, "get_video_info",
            lambda bvid, **kw: fake_info,
        )
        monkeypatch.setattr(
            bili_api, "get_audio_url",
            lambda aid, cid, **kw: f"https://example/{aid}/{cid}",
        )

        def fake_download(url, output, **kw):
            calls["download"] += 1
            output.write_bytes(b"m4s")
            return output

        monkeypatch.setattr("b2text.audio.download_audio_stream", fake_download)

        def fake_ensure_wav(source, output_dir):
            wav = output_dir / "audio.wav"
            wav.write_bytes(b"RIFF")
            return wav

        monkeypatch.setattr("b2text.audio.ensure_wav", fake_ensure_wav)

        ns = build_parser().parse_args([
            "run", "BV1collection", "-o", str(out_dir), "--batch",
        ])
        assert _run(ns) == 0
        files = sorted(p.name for p in out_dir.glob("*.txt"))
        assert files == ["001_第1集.txt", "002_第2集.txt"]
        assert calls["transcribe"] == 2
        assert calls["download"] == 2

    def test_batch_requires_cookie(self, monkeypatch, tmp_path):
        """batch 走 B 站 API，没有 cookie 应返回 4。"""
        from b2text import cli
        monkeypatch.setattr("b2text.audio.check_ffmpeg", lambda: True)
        monkeypatch.setattr(
            cli, "resolve_cookie",
            lambda: (_ for _ in ()).throw(cli.MissingCookieError("no cookie")),
        )
        ns = cli.build_parser().parse_args([
            "run", "BV1collection", "-o", str(tmp_path), "--batch",
        ])
        assert cli._run(ns) == 4


class TestRunBiliBusinessError:
    """B 站业务错误时，b2text run 应友好提示 code/message 而不是裸 traceback。"""

    def test_run_shows_business_error_message(self, monkeypatch, tmp_path):
        import io
        from b2text import cli
        from b2text.bili_api import BiliAPIError

        monkeypatch.setattr("b2text.audio.check_ffmpeg", lambda: True)
        monkeypatch.setattr(cli, "resolve_cookie", lambda: "SESSDATA=x")

        def boom(bvid, **kwargs):
            raise BiliAPIError(code=-352, message="请求被拦截", url="https://example.com/view")

        monkeypatch.setattr("b2text.bili_api.get_video_info", boom)

        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        ns = cli.build_parser().parse_args([
            "run", "BV1xxx", "-o", str(tmp_path / "out.txt"),
        ])
        assert cli._run(ns) == 1
        assert "-352" in buf.getvalue()
        assert "请求被拦截" in buf.getvalue()
