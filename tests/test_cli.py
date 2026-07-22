"""tests/test_cli.py — CLI 入口：parser 构建 + 核心辅助函数。"""
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
