"""cli 单测：参数解析 / 退出码映射 / 未登录状态 / --summarize 编排（不触网）。"""

import json

import pytest

from bili_digest import cli
from bili_digest.errors import CliError
from bili_digest.platforms import bilibili


def test_bad_format_is_usage_error():
    with pytest.raises(SystemExit) as err:
        cli.build_parser().parse_args(["digest", "BV1CV411s7jd", "--format", "pdf"])
    assert err.value.code == 2


def test_missing_target_is_usage_error():
    with pytest.raises(SystemExit) as err:
        cli.build_parser().parse_args(["digest"])
    assert err.value.code == 2


def test_default_arguments():
    args = cli.build_parser().parse_args(["digest", "BV1CV411s7jd"])
    assert args.page is None
    assert args.format == "txt"
    assert args.engine == "auto"
    assert args.lang is None  # S7：默认自动检测
    assert args.summarize is False
    assert args.provider is None  # S7.2：默认走 SUMMARIZE_PROVIDER/zhipu


def test_engine_cloud_accepted():
    args = cli.build_parser().parse_args(["digest", "BV1CV411s7jd", "--engine", "cloud"])
    assert args.engine == "cloud"
    with pytest.raises(SystemExit) as err:
        cli.build_parser().parse_args(["digest", "BV", "--engine", "sensevoice"])
    assert err.value.code == 2


def test_report_and_doctor_registered():
    args = cli.build_parser().parse_args(["report", "runs/BV1x/20260101-000000"])
    assert args.run_dir == "runs/BV1x/20260101-000000"
    assert cli.build_parser().parse_args(["doctor"]).func is cli.cmd_doctor


def test_main_maps_cli_error_code(monkeypatch):
    def boom(*a, **kw):
        raise CliError("被风控拦截", 4)

    monkeypatch.setattr(cli.pipeline, "run", boom)
    assert cli.main(["digest", "BV1CV411s7jd"]) == 4


def test_status_not_logged_in(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bilibili, "CRED_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: None)  # 未配插件 Cookie
    assert cli.main(["status"]) == 0
    out_text = capsys.readouterr().out
    assert "未登录" in out_text
    assert "digest login" in out_text and "BILI_COOKIE" in out_text  # 两条出路


SCAN_CRED = {"cookies": {"SESSDATA": "s"}, "refresh_token": "r", "login_at": 1700000000}


def _login_state(source, logged_in, cred=None):
    return {"source": source, "logged_in": logged_in, "uname": "测试用户" if logged_in else None,
            "mid": 42 if logged_in else None, "cred": cred}


def test_status_scan_cred_logged_in(monkeypatch, capsys):
    """扫码凭据有效：JSON 原 6 字段不变、只增 source；不再凭 refresh_token 报「自动续期可用」。"""
    monkeypatch.setattr(bilibili, "login_state",
                        lambda: _login_state("credentials.json", True, SCAN_CRED))
    assert cli.main(["status", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info == {"logged_in": True, "uname": "测试用户", "mid": 42, "login_at": 1700000000,
                    "has_refresh_token": True, "cred_path": bilibili.CRED_PATH,
                    "source": "credentials.json"}
    assert cli.main(["status"]) == 0
    out_text = capsys.readouterr().out
    assert "已登录: 测试用户 (mid 42)" in out_text and "凭据已用" in out_text
    assert "扫码凭据" in out_text
    assert "自动续期: 不可用" in out_text and "自动续期: 可用" not in out_text
    assert "登录态失效后需重新登录" in out_text


def test_status_scan_cred_expired(monkeypatch, capsys):
    monkeypatch.setattr(bilibili, "login_state",
                        lambda: _login_state("credentials.json", False, SCAN_CRED))
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: None)
    assert cli.main(["status"]) == 0
    out_text = capsys.readouterr().out
    assert "登录态已失效：重新执行 digest login" in out_text
    assert "credentials.json" not in out_text  # 没配插件 Cookie 就不必提改用


def test_status_scan_cred_expired_with_plugin_cookie(monkeypatch, capsys):
    """扫码凭据失效且已配 BILI_COOKIE：指路重新扫码，并说明删凭据文件即可改用插件 Cookie。"""
    monkeypatch.setattr(bilibili, "login_state",
                        lambda: _login_state("credentials.json", False, SCAN_CRED))
    monkeypatch.setattr(bilibili, "cookie_from_keys", lambda: "SESSDATA=plugin")
    assert cli.main(["status"]) == 0
    out_text = capsys.readouterr().out
    assert "登录态已失效" in out_text and "重新执行 digest login" in out_text
    assert f"删除 {bilibili.CRED_PATH}" in out_text and "优先生效" in out_text


def test_status_plugin_cookie_logged_in(monkeypatch, capsys):
    """仅插件 Cookie 且有效：如实报已登录，来源 BILI_COOKIE，无凭据天数。"""
    monkeypatch.setattr(bilibili, "login_state", lambda: _login_state("BILI_COOKIE", True))
    assert cli.main(["status"]) == 0
    out_text = capsys.readouterr().out
    assert "已登录: 测试用户 (mid 42)" in out_text and "插件 Cookie" in out_text
    assert "凭据已用" not in out_text and "自动续期: 不可用" in out_text
    assert cli.main(["status", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["logged_in"] is True and info["source"] == "BILI_COOKIE"
    assert info["has_refresh_token"] is False and info["login_at"] is None


def test_status_plugin_cookie_expired(monkeypatch, capsys):
    monkeypatch.setattr(bilibili, "login_state", lambda: _login_state("BILI_COOKIE", False))
    assert cli.main(["status"]) == 0
    out_text = capsys.readouterr().out
    assert "登录态已失效" in out_text and "Chrome 插件" in out_text and "digest login" in out_text


def test_status_network_error_keeps_exit_code(monkeypatch):
    """实测遇网络/风控：CliError 原样上抛（退出码 4），不谎报成未登录/失效。"""
    def boom():
        raise CliError("网络不可用: x", 4)

    monkeypatch.setattr(bilibili, "login_state", boom)
    assert cli.main(["status"]) == 4


def test_digest_missing_page(monkeypatch):
    def boom(*a, **kw):
        raise CliError("没有第 9 P（共 5 P）", 2)

    monkeypatch.setattr(cli.pipeline, "run", boom)
    assert cli.main(["digest", "BV1CV411s7jd", "--page", "9"]) == 2


def test_digest_summarize_writes_run_dir(monkeypatch, tmp_path, capsys):
    """--summarize：落盘 transcript 三件套 + meta.json + report.md，stdout 出报告。"""
    from bili_digest.transcribe.base import Segment, Transcript

    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path / "runs"))
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="mlx-whisper",
                    source="asr", elapsed_s=1.0)
    meta = {"bvid": "BV1test", "title": "T", "duration": 5}
    monkeypatch.setattr(cli.pipeline, "run",
                        lambda *a, **kw: cli.pipeline.Result(tr, meta))
    monkeypatch.setattr(cli.report, "summarize_transcript",
                        lambda t, m, provider="zhipu": ("# 报告正文",
                                                        {"model": "glm-5.3-flash",
                                                         "total_tokens": 42, "cost_yuan": 0.01}))
    assert cli.main(["digest", "BV1test", "--summarize"]) == 0
    captured = capsys.readouterr()
    assert "# 报告正文" in captured.out
    run_dirs = list((tmp_path / "runs" / "BV1test").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "transcript.txt").exists()
    assert (run_dir / "transcript.json").exists()
    assert (run_dir / "report.md").read_text(encoding="utf-8") == "# 报告正文"
    saved = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["summarize"]["cost_yuan"] == 0.01


# ---------------------------------------------------------------- --visual（S6 / S6.1 模式）

def test_visual_flag_default_off():
    """不加 --visual = 纯音频模式（None，不下载视频）。"""
    args = cli.build_parser().parse_args(["digest", "BV1CV411s7jd"])
    assert args.visual is None


def test_visual_mode_values():
    assert cli.build_parser().parse_args(["digest", "BV", "--visual"]).visual == "auto"
    assert cli.build_parser().parse_args(["digest", "BV", "--visual=auto"]).visual == "auto"
    assert cli.build_parser().parse_args(["digest", "BV", "--visual=ppt"]).visual == "ppt"
    assert cli.build_parser().parse_args(["digest", "BV", "--visual", "ppt"]).visual == "ppt"
    with pytest.raises(SystemExit) as err:  # 非法模式 → 用法错误
        cli.build_parser().parse_args(["digest", "BV", "--visual=story"])
    assert err.value.code == 2


def test_digest_visual_writes_enriched_run(monkeypatch, tmp_path, capsys):
    """--visual：pipeline 已建 run 目录 → CLI 补 transcript 三件套 + enriched_transcript.md，
    stdout 出富化时间轴。"""
    from bili_digest.transcribe.base import Segment, Transcript

    frames = [{"id": "f00", "t": 8.0, "file": "frames/f00.jpg", "desc": "封面文字", "has_text": True}]
    run_dir = tmp_path / "run"
    (run_dir / "frames").mkdir(parents=True)
    (run_dir / "frames" / "f00.jpg").write_bytes(b"\xff\xd8fake")
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    meta = {"bvid": "BV1test", "title": "T", "duration": 9, "frames": frames,
            "vision": {"model": "glm-5.3-flash", "frames": 1, "total_tokens": 9, "cost_yuan": 0.0}}
    monkeypatch.setattr(cli.pipeline, "run",
                        lambda *a, **kw: cli.pipeline.Result(tr, meta, run_dir=run_dir))
    assert cli.main(["digest", "BV1test", "--visual"]) == 0
    captured = capsys.readouterr()
    assert "🖼 （画面）封面文字" in captured.out  # stdout 是富化时间轴
    assert (run_dir / "enriched_transcript.md").exists()
    assert (run_dir / "transcript.txt").exists()
    saved = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["vision"]["model"] == "glm-5.3-flash" and saved["frames"][0]["id"] == "f00"


def test_digest_visual_summarize_reports_with_frames(monkeypatch, tmp_path, capsys):
    """--visual --summarize：报告模型收到含 frames 的 meta；report.md 落盘。"""
    from bili_digest.transcribe.base import Segment, Transcript

    frames = [{"id": "f00", "t": 8.0, "file": "frames/f00.jpg", "desc": "封面文字", "has_text": True}]
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    meta = {"bvid": "BV1test", "title": "T", "duration": 9, "frames": frames}
    monkeypatch.setattr(cli.pipeline, "run",
                        lambda *a, **kw: cli.pipeline.Result(tr, meta, run_dir=run_dir))
    seen = {}

    def fake_summarize(t, m, provider="zhipu"):
        seen["meta"] = m
        return "# 视觉报告\n![f00](frames/f00.jpg)", {"model": "glm-5.3-flash",
                                                       "total_tokens": 42, "cost_yuan": 0.01}

    monkeypatch.setattr(cli.report, "summarize_transcript", fake_summarize)
    assert cli.main(["digest", "BV1test", "--visual", "--summarize"]) == 0
    assert seen["meta"]["frames"] == frames  # frames 进入总结 meta
    assert (run_dir / "report.md").exists()
    assert (run_dir / "enriched_transcript.md").exists()
    assert "视觉报告" in capsys.readouterr().out


def test_digest_visual_ppt_mode_passed_to_pipeline(monkeypatch, tmp_path):
    """--visual=ppt 透传到 pipeline.run（模式路由）。"""
    from bili_digest.transcribe.base import Segment, Transcript

    seen = {}
    run_dir = tmp_path / "run"
    (run_dir / "frames").mkdir(parents=True)
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    meta = {"bvid": "BV1test", "title": "T", "duration": 5, "frames": []}

    def fake_run(target, page=None, engine="auto", lang="zh", visual=None, work_dir=None, **kw):
        seen["visual"] = visual
        return cli.pipeline.Result(tr, meta, run_dir=run_dir)

    monkeypatch.setattr(cli.pipeline, "run", fake_run)
    assert cli.main(["digest", "BV1test", "--visual=ppt"]) == 0
    assert seen["visual"] == "ppt"


# ---------------------------------------------------------------- S7：--type / --no-report

def _fake_result(tr, meta, run_dir):
    return cli.pipeline.Result(tr, meta, run_dir=run_dir)


def test_type_conflicts_with_visual_exit_2():
    """--type 与 --visual 同时出现 → 用法错误 exit 2（防歧义）。"""
    assert cli.main(["digest", "BV1x", "--type", "slides", "--visual"]) == 2


def test_type_routes(monkeypatch, tmp_path):
    from bili_digest.transcribe.base import Segment, Transcript
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    captured = []
    run_dir = tmp_path / "run"
    (run_dir / "frames").mkdir(parents=True)

    def fake_run(target, page=None, engine="auto", lang="zh", visual=None,
                 work_dir=None, skip_asr=False, no_gate=False, **kw):
        captured.append({"visual": visual, "skip_asr": skip_asr, "no_gate": no_gate})
        return _fake_result(tr, {"bvid": "BV1t", "title": "T", "duration": 5, "frames": []},
                            run_dir if visual else None)

    monkeypatch.setattr(cli.pipeline, "run", fake_run)
    monkeypatch.setattr(cli.report, "summarize_transcript",
                        lambda tr, meta, provider="zhipu": ("# R", {"model": "m", "total_tokens": 1, "cost_yuan": 0.0}))
    monkeypatch.setattr(cli.report, "save_report", lambda rd, md, usage: rd / "report.md")
    for vtype in ("talk", "slides", "mixed", "auto"):
        assert cli.main(["digest", "BV1t", "--type", vtype, "--summarize"]) == 0
    assert captured == [
        {"visual": None, "skip_asr": False, "no_gate": False},     # talk
        {"visual": "ppt", "skip_asr": True, "no_gate": False},     # slides
        {"visual": "auto", "skip_asr": False, "no_gate": True},    # mixed
        {"visual": None, "skip_asr": False, "no_gate": False},     # auto
    ]


def test_no_report_skips_summarize(monkeypatch, tmp_path):
    """--no-report：素材场景跳过 summarize（report.md 不生成），素材包仍产出。"""
    from bili_digest.transcribe.base import Segment, Transcript
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    run_dir = tmp_path / "run"
    (run_dir / "frames").mkdir(parents=True)

    def must_not_summarize(*a, **kw):
        raise AssertionError("--no-report 不应触发 summarize")
    monkeypatch.setattr(cli.pipeline, "run",
                        lambda *a, **kw: _fake_result(
                            tr, {"bvid": "BV1n", "title": "T", "duration": 5, "frames": []},
                            run_dir))
    monkeypatch.setattr(cli.report, "summarize_transcript", must_not_summarize)
    monkeypatch.setattr(cli.enrich.fusion, "render_enriched_md",
                        lambda tr, frames, meta: "# 富化")
    assert cli.main(["digest", "BV1n", "--visual", "--no-report"]) == 0
    assert (run_dir / "assets.json").exists() and (run_dir / "assets.md").exists()
    assert not (run_dir / "report.md").exists()


def test_provider_env_default_resolution(monkeypatch, tmp_path):
    """S7.2：--provider 缺省时走 SUMMARIZE_PROVIDER 持久化默认，显式给出时优先。"""
    from bili_digest.transcribe.base import Segment, Transcript
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    seen = {}

    def fake_summarize(t, m, provider="zhipu"):
        seen["provider"] = provider
        return "# R", {"model": "m", "total_tokens": 1, "cost_yuan": 0.0}
    monkeypatch.setattr(cli.report, "summarize_transcript", fake_summarize)
    monkeypatch.setattr(cli.report, "save_report", lambda rd, md, usage: rd / "report.md")
    monkeypatch.setattr(cli.pipeline, "run",
                        lambda *a, **kw: cli.pipeline.Result(
                            tr, {"bvid": "BV1p", "title": "T", "duration": 5, "frames": []},
                            None))
    monkeypatch.setattr(cli.config, "summarize_provider", lambda: "deepseek")
    assert cli.main(["digest", "BV1p", "--summarize"]) == 0
    assert seen["provider"] == "deepseek"  # 持久化默认生效
    assert cli.main(["digest", "BV1p", "--summarize", "--provider", "zhipu"]) == 0
    assert seen["provider"] == "zhipu"  # 显式参数优先


def test_partial_success_report_error_exit_zero(monkeypatch, tmp_path, capsys):
    """S7.4：transcript 成功 + 报告失败 → exit 0 + stderr 警告 + meta.report_error（主产物契约）。"""
    from bili_digest.transcribe.base import Segment, Transcript
    tr = Transcript([Segment(0.0, 5.0, "第一句")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=1.0)
    run_dir = tmp_path / "run"
    (run_dir / "frames").mkdir(parents=True)

    def boom(t, m, provider="siliconflow"):
        raise CliError("余额不足：到 https://cloud.siliconflow.cn 充值后重试", 3)
    monkeypatch.setattr(cli.pipeline, "run",
                        lambda *a, **kw: cli.pipeline.Result(
                            tr, {"bvid": "BV1ps", "title": "T", "duration": 5, "frames": []},
                            run_dir))
    monkeypatch.setattr(cli.report, "summarize_transcript", boom)
    monkeypatch.setattr(cli.enrich.fusion, "render_enriched_md",
                        lambda tr, frames, meta: "# 富化")
    assert cli.main(["digest", "BV1ps", "--visual", "--summarize"]) == 0  # 主产物契约：exit 0
    err_text = capsys.readouterr().err
    assert "总结失败" in err_text and "digest report" in err_text
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["report_error"]["error"].startswith("余额不足")
    assert not (run_dir / "report.md").exists()
