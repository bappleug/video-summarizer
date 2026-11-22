"""mcp_server 单测：JobManager（mock pipeline，不打真 API）/ 参数防御 / 工具分发 /
子进程 JSON-RPC 全链路（initialize → tools/list → digest_video → get_transcript）与 stdout 纯净性。
"""

import base64
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from bili_digest import mcp_server, pipeline
from bili_digest.errors import CliError
from bili_digest.transcribe.base import Segment, Transcript

TOOL_NAMES = {"digest_video", "job_status", "get_transcript", "get_report", "get_assets",
               "get_frame", "cancel", "digest_doctor", "get_manual"}


def fake_result(text="第一句", bvid="BV1test"):
    tr = Transcript([Segment(0.0, 5.0, text)], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.1)
    return pipeline.Result(tr, {"bvid": bvid, "title": "T", "duration": 5})


def wait_status(manager, job_id, statuses, timeout=5.0):
    """轮询 job 到目标状态集合之一，返回最终 status dict。"""
    deadline = time.time() + timeout
    while True:
        payload = manager.status(job_id)
        if payload["status"] in statuses or time.time() > deadline:
            return payload
        time.sleep(0.01)


# ---------------------------------------------------------------- digest_video → transcript

def test_submit_done_and_formats(monkeypatch):
    monkeypatch.setattr(mcp_server.pipeline, "run", lambda *a, **kw: fake_result("你好世界"))
    manager = mcp_server.JobManager()
    submitted = manager.submit("BV1test")
    assert submitted["status"] in ("queued", "running", "done")  # 快照，mock 管线可能已跑完
    done = wait_status(manager, submitted["job_id"], {"done", "error"})
    assert done["status"] == "done"
    assert done["transcript"] == {"engine": "subtitle", "source": "subtitle", "segments": 1}
    jid = submitted["job_id"]
    assert manager.transcript_text(jid) == "你好世界"  # 默认 txt
    assert "-->" in manager.transcript_text(jid, "srt")
    assert manager.transcript_text(jid, "md").startswith("# T")
    payload = json.loads(manager.transcript_text(jid, "json"))
    assert payload["segments"][0]["text"] == "你好世界"


def test_cli_error_passthrough_with_fix_hint(monkeypatch):
    def boom(*a, **kw):
        raise CliError("云引擎需配置 SILICONFLOW_API_KEY（~/.config/ai-tools/bili-digest/keys.env "
                       "或环境变量），运行 digest doctor 查看指引", 3)

    monkeypatch.setattr(mcp_server.pipeline, "run", boom)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test", engine="cloud")["job_id"]
    failed = wait_status(manager, jid, {"error"})
    assert failed["status"] == "error"
    assert "SILICONFLOW_API_KEY" in failed["error"]  # 修法指引原样透传


def test_unexpected_exception_is_contained(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("炸了")

    monkeypatch.setattr(mcp_server.pipeline, "run", boom)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test")["job_id"]
    failed = wait_status(manager, jid, {"error"})
    assert failed["status"] == "error"
    assert "RuntimeError" in failed["error"]


def test_transcript_not_ready_then_unknown_job(monkeypatch):
    gate = threading.Event()

    def slow(*a, **kw):
        gate.wait(5)
        return fake_result()

    monkeypatch.setattr(mcp_server.pipeline, "run", slow)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test")["job_id"]
    wait_status(manager, jid, {"running"})
    with pytest.raises(mcp_server.ToolInputError, match="running"):
        manager.transcript_text(jid)
    gate.set()
    wait_status(manager, jid, {"done"})
    with pytest.raises(mcp_server.ToolInputError, match="不存在"):
        manager.transcript_text("deadbeef")


# ---------------------------------------------------------------- summarize → report

def test_summarize_job_report(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(mcp_server.pipeline, "run", lambda *a, **kw: fake_result())
    monkeypatch.setattr(mcp_server.report, "summarize_transcript",
                        lambda t, m, provider="zhipu": ("# 报告正文",
                                                        {"model": "glm-5.3-flash",
                                                         "total_tokens": 7, "cost_yuan": 0.01}))
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test", summarize=True)["job_id"]
    done = wait_status(manager, jid, {"done", "error"})
    assert done["status"] == "done"
    assert manager.report_text(jid) == "# 报告正文"
    # 与 CLI --summarize 同构：run 目录三件套 + meta.json + report.md
    run_dir = Path(done["run_dir"])
    assert run_dir.is_relative_to(tmp_path / "runs" / "BV1test")
    assert (run_dir / "report.md").exists()
    assert (run_dir / "transcript.txt").exists()


def test_get_report_requires_summarize(monkeypatch):
    monkeypatch.setattr(mcp_server.pipeline, "run", lambda *a, **kw: fake_result())
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test")["job_id"]
    wait_status(manager, jid, {"done"})
    with pytest.raises(mcp_server.ToolInputError, match="summarize=true"):
        manager.report_text(jid)


# ---------------------------------------------------------------- cancel

def test_cancel_running_takes_effect_at_stage_boundary(monkeypatch):
    gate = threading.Event()

    def slow(*a, **kw):
        gate.wait(5)
        return fake_result()

    monkeypatch.setattr(mcp_server.pipeline, "run", slow)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test", summarize=False)["job_id"]
    wait_status(manager, jid, {"running"})
    resp = manager.cancel(jid)
    assert resp["cancelled"] is True  # 已请求，阶段边界生效
    gate.set()
    final = wait_status(manager, jid, {"cancelled", "done", "error"})
    assert final["status"] == "cancelled"
    with pytest.raises(mcp_server.ToolInputError, match="已取消"):
        manager.transcript_text(jid)


def test_cancel_before_pipeline_starts(monkeypatch):
    def must_not_run(*a, **kw):
        raise AssertionError("已取消的 job 不应触达 pipeline")

    monkeypatch.setattr(mcp_server.pipeline, "run", must_not_run)
    manager = mcp_server.JobManager()
    job = mcp_server.Job("BV1test", False, "auto", None, "txt")  # 不经 submit，手动驱动
    job.cancel_event.set()
    manager._execute(job)
    assert job.status == "cancelled"


def test_cancel_terminal_is_noop(monkeypatch):
    monkeypatch.setattr(mcp_server.pipeline, "run", lambda *a, **kw: fake_result())
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test")["job_id"]
    wait_status(manager, jid, {"done"})
    resp = manager.cancel(jid)
    assert resp == {"job_id": jid, "status": "done", "cancelled": False, "note": "job 已是终态，无需取消"}


def test_cancel_summarize_job_skips_report(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path / "runs"))
    gate = threading.Event()

    def slow(*a, **kw):
        gate.wait(5)
        return fake_result()

    def must_not_summarize(*a, **kw):
        raise AssertionError("取消后不应进入 summarize 阶段")

    monkeypatch.setattr(mcp_server.pipeline, "run", slow)
    monkeypatch.setattr(mcp_server.report, "summarize_transcript", must_not_summarize)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1test", summarize=True)["job_id"]
    wait_status(manager, jid, {"running"})
    manager.cancel(jid)
    gate.set()
    final = wait_status(manager, jid, {"cancelled"})
    assert final["status"] == "cancelled"


# ---------------------------------------------------------------- 并发（验收 #4）

def test_two_jobs_run_concurrently(monkeypatch):
    barrier = threading.Barrier(2)
    gate = threading.Event()

    def slow(target, **kw):
        barrier.wait(10)  # 两 job 必须同时身处 pipeline 内才能通过
        gate.wait(10)
        return fake_result(f"段-{target}")

    monkeypatch.setattr(mcp_server.pipeline, "run", slow)
    manager = mcp_server.JobManager()
    j1 = manager.submit("BV1aa")["job_id"]
    j2 = manager.submit("BV1bb")["job_id"]
    deadline = time.time() + 5
    while time.time() < deadline and not (manager.status(j1)["status"] == "running"
                                          and manager.status(j2)["status"] == "running"):
        time.sleep(0.01)
    assert manager.status(j1)["status"] == manager.status(j2)["status"] == "running"
    gate.set()
    assert wait_status(manager, j1, {"done"})["status"] == "done"
    assert wait_status(manager, j2, {"done"})["status"] == "done"
    assert manager.transcript_text(j1) == "段-BV1aa"
    assert manager.transcript_text(j2) == "段-BV1bb"  # 互不串扰


# ---------------------------------------------------------------- 参数防御

@pytest.mark.parametrize("args, patch", [
    ({"url": ""}, "url 必填"),
    ({"url": "x" * 2049}, "url 过长"),
    ({"url": ["BV1x"]}, "url 必填"),
    ({"url": "BV1x", "engine": "sensevoice"}, "engine"),
    ({"url": "BV1x", "format": "pdf"}, "format"),
    ({"url": "BV1x", "page": 0}, "page"),
    ({"url": "BV1x", "page": "第2P"}, "page"),
    ({"url": "BV1x", "summarize": "yes"}, "布尔"),
    ({"url": "BV1x", "visual": "yes"}, "auto"),
    ({"url": "BV1x", "visual": "story"}, "auto"),
])
def test_bad_args_rejected(args, patch):
    with pytest.raises(mcp_server.ToolInputError, match=patch):
        mcp_server.validate_digest_args(args)


@pytest.mark.parametrize("args, expect", [
    ({"url": " BV1x "}, {"url": "BV1x", "summarize": False, "engine": "auto", "page": None, "fmt": "txt", "visual": False, "vtype": "auto", "work_dir": None}),
    ({"url": "BV1x", "summarize": "true", "engine": "cloud", "page": "3", "format": "srt"},
     {"url": "BV1x", "summarize": True, "engine": "cloud", "page": 3, "fmt": "srt", "visual": False, "vtype": "auto", "work_dir": None}),
    ({"url": "BV1x", "summarize": 1}, {"url": "BV1x", "summarize": True, "engine": "auto", "page": None, "fmt": "txt", "visual": False, "vtype": "auto", "work_dir": None}),
    ({"url": "BV1x", "visual": "true"}, {"url": "BV1x", "summarize": False, "engine": "auto", "page": None, "fmt": "txt", "visual": "auto", "vtype": "auto", "work_dir": None}),
    ({"url": "BV1x", "visual": "PPT"}, {"url": "BV1x", "summarize": False, "engine": "auto", "page": None, "fmt": "txt", "visual": "ppt", "vtype": "auto", "work_dir": None}),
    ({"url": "BV1x", "visual": True}, {"url": "BV1x", "summarize": False, "engine": "auto", "page": None, "fmt": "txt", "visual": "auto", "vtype": "auto", "work_dir": None}),
    ({"url": "BV1x", "visual": 0}, {"url": "BV1x", "summarize": False, "engine": "auto", "page": None, "fmt": "txt", "visual": False, "vtype": "auto", "work_dir": None}),
])
def test_args_coerced(args, expect):
    assert mcp_server.validate_digest_args(args) == expect


# ---------------------------------------------------------------- 工具分发

def test_tool_dispatch_unknown_and_garbage_arguments():
    manager = mcp_server.JobManager()
    with pytest.raises(mcp_server.ToolInputError, match="未知工具"):
        mcp_server.tool_call(manager, "prewarm", {})
    with pytest.raises(mcp_server.ToolInputError, match="url 必填"):
        mcp_server.tool_call(manager, "digest_video", "不是对象")
    with pytest.raises(mcp_server.ToolInputError, match="job_id"):
        mcp_server.tool_call(manager, "job_status", {})


def test_tool_dispatch_end_to_end_mock(monkeypatch):
    monkeypatch.setattr(mcp_server.pipeline, "run", lambda *a, **kw: fake_result("分发链路"))
    manager = mcp_server.JobManager()
    submitted = mcp_server.tool_call(manager, "digest_video", {"url": "BV1test"})
    done = wait_status(manager, submitted["job_id"], {"done"})
    assert done["status"] == "done"
    text = mcp_server.tool_call(manager, "get_transcript", {"job_id": submitted["job_id"]})
    assert text == "分发链路"
    status = mcp_server.tool_call(manager, "job_status", {"job_id": submitted["job_id"]})
    assert status["status"] == "done"


def test_json_safe_nan():
    assert mcp_server._json_safe({"x": float("nan"), "y": [float("inf"), 1]}) == {"x": None, "y": [None, 1]}


def test_tool_schemas_frozen():
    names = [t["name"] for t in mcp_server.TOOLS]
    assert set(names) == TOOL_NAMES and len(names) == 9  # S7.6：+get_manual（手册按需取用）
    by_name = {t["name"]: t for t in mcp_server.TOOLS}
    assert by_name["digest_video"]["inputSchema"]["required"] == ["url"]
    for name in ("job_status", "get_transcript", "get_report", "get_assets", "cancel"):
        assert by_name[name]["inputSchema"]["required"] == ["job_id"]
    assert by_name["get_frame"]["inputSchema"]["required"] == ["job_id", "file"]
    assert by_name["digest_doctor"]["inputSchema"]["required"] == []
    assert "首次使用" in by_name["digest_doctor"]["description"] and "¥0.001" in \
        by_name["digest_doctor"]["description"]
    # S7.6：digest_video 描述场景句 + type 一行 + 指路 get_manual
    dv_desc = by_name["digest_video"]["description"]
    assert "summarize=true" in dv_desc and "type=slides" in dv_desc
    assert "get_manual" in dv_desc and "¥" not in dv_desc
    # S7.6：INSTRUCTIONS 常驻瘦身（≤800 字符，不含参数表/费用关键词）
    ins = mcp_server.INSTRUCTIONS
    assert len(ins) <= 800
    for banned in ("engine", "output_dir", "门控", "¥", "显示完整技术手册", "费用走宿主"):
        assert banned not in ins, f"常驻 INSTRUCTIONS 不应含 {banned!r}"
    assert "只讲场景与推荐操作，不罗列参数表/工具清单/内部机制/费用明细" in ins
    assert "get_report" in ins and "get_transcript" in ins and "get_assets/get_frame" in ins
    assert "调 get_manual 获取后转述" in ins
    # get_manual：手册常量含修正后费用与 9 工具
    manual = mcp_server.MANUAL
    assert "¥0.003" in manual and "¥0.005" in manual and "云转写（SenseVoice）：¥0" in manual
    assert manual.count("get_manual") >= 1 and "9 个" in manual
    gm = by_name["get_manual"]
    assert gm["inputSchema"]["required"] == []
    assert "仅在用户明确要技术细节" in gm["description"]


# ---------------------------------------------------------------- 协议全链路（验收 #1 #3）

DRIVER = textwrap.dedent("""
    import sys
    from bili_digest import mcp_server, pipeline
    from bili_digest.transcribe.base import Segment, Transcript

    def fake_run(target, page=None, engine="auto", lang="zh", visual=False, work_dir=None, **kw):
        tr = Transcript([Segment(0.0, 2.0, "你好，世界")], lang="zh", engine="subtitle",
                        source="subtitle", elapsed_s=0.05)
        return pipeline.Result(tr, {"bvid": "BV1e2e666", "title": "端到端", "duration": 2})

    pipeline.run = fake_run  # mock 管线，不打真 API
    sys.exit(mcp_server.main())
""")


class TestStdioProtocol:
    def run_server(self):
        return subprocess.Popen([sys.executable, "-c", DRIVER],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def test_full_chain_stdout_pure(self):
        proc = self.run_server()
        stdout_lines = []
        try:
            def send(msg):
                proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                proc.stdin.flush()

            def read_response(want_id):
                deadline = time.time() + 15
                while time.time() < deadline:
                    line = proc.stdout.readline()
                    if not line:
                        raise AssertionError("server 提前退出")
                    stdout_lines.append(line)
                    resp = json.loads(line)  # stdout 逐行必须是合法 JSON（纯净性）
                    if resp.get("id") == want_id:
                        return resp
                raise AssertionError(f"等响应 {want_id} 超时")

            # initialize 握手
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "pytest", "version": "0"}}})
            init = read_response(1)["result"]
            assert init["serverInfo"]["name"] == "bili-digest"
            assert init["capabilities"]["tools"]["listChanged"] is False
            assert init["instructions"]
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})

            # tools/list：5 工具与 schema
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = read_response(2)["result"]["tools"]
            assert {t["name"] for t in tools} == TOOL_NAMES
            dv = next(t for t in tools if t["name"] == "digest_video")
            assert dv["inputSchema"]["required"] == ["url"]
            assert set(dv["inputSchema"]["properties"]) == {"url", "summarize", "visual", "type", "engine", "page", "format", "output_dir"}

            # digest_video → job_status → get_transcript（mock pipeline 立即 done）
            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "digest_video", "arguments": {"url": "BV1e2e666"}}})
            payload = json.loads(read_response(3)["result"]["content"][0]["text"])
            jid = payload["job_id"]
            for i in range(4, 40):
                send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                      "params": {"name": "job_status", "arguments": {"job_id": jid}}})
                status = json.loads(read_response(i)["result"]["content"][0]["text"])
                if status["status"] == "done":
                    break
                assert status["status"] in ("queued", "running"), status
            else:
                raise AssertionError("job 未在轮询窗口内完成")
            assert status["transcript"]["engine"] == "subtitle"

            send({"jsonrpc": "2.0", "id": 50, "method": "tools/call",
                  "params": {"name": "get_transcript", "arguments": {"job_id": jid}}})
            text = read_response(50)["result"]["content"][0]["text"]
            assert "你好，世界" in text

            # 非法参数 → isError 结构化错误，服务不崩
            send({"jsonrpc": "2.0", "id": 51, "method": "tools/call",
                  "params": {"name": "digest_video", "arguments": {"url": ""}}})
            bad = read_response(51)["result"]
            assert bad["isError"] is True and "url 必填" in bad["content"][0]["text"]

            send({"jsonrpc": "2.0", "id": 52, "method": "ping"})
            assert read_response(52)["result"] == {}

            proc.stdin.close()
            assert proc.wait(timeout=10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
        # stdout 纯净性：整个会话的每一行都可 JSON parse（上面逐行已断言，这里再整体复核）
        for line in stdout_lines:
            json.loads(line)
        assert len(stdout_lines) >= 7  # initialize/list/call×5 条响应


def test_digest_mcp_cli_smoke_missing_sdk_message(monkeypatch):
    """cli 探测到无 mcp SDK 时给出安装指引而非 traceback。"""
    import builtins

    from bili_digest import cli

    real_import = builtins.__import__

    def no_mcp(name, *a, **kw):
        if name == "mcp":
            raise ImportError("No module named 'mcp'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_mcp)
    assert cli.main(["mcp"]) == 2


def test_digest_mcp_registered_in_parser():
    from bili_digest import cli

    args = cli.build_parser().parse_args(["mcp"])
    assert args.func is cli.cmd_mcp


# ---------------------------------------------------------------- visual（S6）

def visual_result(bvid="BV1vis"):
    tr = Transcript([Segment(0.0, 5.0, "画面里讲经纬网")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.1)
    meta = {"bvid": bvid, "title": "T", "duration": 9,
            "frames": [{"id": "f00", "t": 8.0, "file": "frames/f00.jpg",
                        "desc": "封面：经纬网", "has_text": True}],
            "vision": {"model": "glm-5.3-flash", "frames": 1, "total_tokens": 9, "cost_yuan": 0.0}}
    return pipeline.Result(tr, meta, run_dir=None)  # run_dir 由 _execute 落盘时新建


def test_visual_job_writes_enriched_run_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(mcp_server.pipeline, "run",
                        lambda *a, **kw: visual_result())
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1vis", visual=True)["job_id"]
    done = wait_status(manager, jid, {"done", "error"})
    assert done["status"] == "done"
    assert done["frames"] == 1  # status 暴露关键帧数
    run_dir = Path(done["run_dir"])
    assert (run_dir / "enriched_transcript.md").exists()
    assert "🖼" in (run_dir / "enriched_transcript.md").read_text(encoding="utf-8")
    assert (run_dir / "transcript.txt").exists()


def test_visual_job_passes_flag_to_pipeline(monkeypatch):
    seen = {}

    def fake_run(url, page=None, engine="auto", lang="zh", visual=False, work_dir=None, **kw):
        seen[visual] = url
        return fake_result()

    monkeypatch.setattr(mcp_server.pipeline, "run", fake_run)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1vis", visual=True)["job_id"]
    assert wait_status(manager, jid, {"done"})["status"] == "done"
    assert seen.get(True) == "BV1vis"  # 布尔 True 透传（真 pipeline.run 内归一为 "auto"）
    jid2 = manager.submit("BV1ppt", visual="ppt")["job_id"]
    assert wait_status(manager, jid2, {"done"})["status"] == "done"
    assert seen.get("ppt") == "BV1ppt"  # ppt 模式原样透传
    jid3 = mcp_server.tool_call(manager, "digest_video",
                                {"url": "BV1auto", "visual": True})["job_id"]
    assert wait_status(manager, jid3, {"done"})["status"] == "done"
    assert seen.get("auto") == "BV1auto"  # 工具层把 True 归一为 "auto"（S6.1 模式）


def test_visual_gate_exposes_frames_zero(monkeypatch, tmp_path):
    """门控命中的 job：frames=0 照常 done（诚实降级，不产帧不嵌帧）。"""
    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path / "runs"))
    tr = Transcript([Segment(0.0, 5.0, "口播内容")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.1)
    meta = {"bvid": "BV1talk", "title": "访谈", "duration": 600,
            "frames": [], "visual_gate": "low_diversity"}
    monkeypatch.setattr(mcp_server.pipeline, "run",
                        lambda *a, **kw: pipeline.Result(tr, meta, run_dir=None))
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1talk", visual="auto")["job_id"]
    done = wait_status(manager, jid, {"done", "error"})
    assert done["status"] == "done" and done["frames"] == 0  # status 暴露 0 帧


def test_visual_schema_supports_modes():
    """digest_video 的 visual schema：boolean | enum(auto,ppt)（S6.1 模式路由）。"""
    by_name = {t["name"]: t for t in mcp_server.TOOLS}
    visual = by_name["digest_video"]["inputSchema"]["properties"]["visual"]
    variants = visual["oneOf"]
    assert {"type": "boolean"} in variants
    assert {"type": "string", "enum": ["auto", "ppt"]} in variants
    assert "门控" in visual["description"]


# ---------------------------------------------------------------- S7：get_assets / get_frame

def _seed_visual_job(manager, monkeypatch, tmp_path):
    """提交一个 visual job（mock 管线，产物落 tmp_path），等 done 后返回 job_id。"""
    tr = Transcript([Segment(0.0, 5.0, "画面里讲经纬网")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.05)
    meta = {"bvid": "BV1ast", "title": "素材", "duration": 5,
            "frames": [{"id": "f00", "t": 1.0, "file": "frames/f00.jpg",
                        "desc": "经纬网图", "has_text": True, "type": "diagram"}]}

    def fake_run(url, page=None, engine="auto", lang="zh", visual=False,
                 work_dir=None, **kw):
        return pipeline.Result(tr, meta, run_dir=tmp_path)
    monkeypatch.setattr(mcp_server.pipeline, "run", fake_run)
    (tmp_path / "frames").mkdir(exist_ok=True)
    (tmp_path / "frames" / "f00.jpg").write_bytes(b"\xff\xd8JPEGDATA")
    jid = manager.submit("BV1ast", visual=True)["job_id"]
    assert wait_status(manager, jid, {"done"})["status"] == "done"
    return jid


def test_get_assets_and_frame_roundtrip(monkeypatch, tmp_path):
    manager = mcp_server.JobManager()
    jid = _seed_visual_job(manager, monkeypatch, tmp_path)
    payload = mcp_server.tool_call(manager, "get_assets", {"job_id": jid})
    assert payload["frames"][0]["file"] == "frames/f00.jpg"
    assert payload["frames"][0]["frame_type"] == "diagram"
    assert payload["transcript"]["source"] == "subtitle"
    frame = mcp_server.tool_call(manager, "get_frame", {"job_id": jid, "file": "frames/f00.jpg"})
    assert frame["mime"] == "image/jpeg"
    import base64
    assert base64.b64decode(frame["base64"]) == (tmp_path / "frames" / "f00.jpg").read_bytes()


def test_get_frame_rejects_traversal_and_bad_ext(monkeypatch, tmp_path):
    manager = mcp_server.JobManager()
    jid = _seed_visual_job(manager, monkeypatch, tmp_path)
    for bad in ("../meta.json", "frames/f00.gif", "no/such/f00.jpg", " "):
        with pytest.raises(mcp_server.ToolInputError):
            mcp_server.tool_call(manager, "get_frame", {"job_id": jid, "file": bad})


def test_type_param_routes_and_conflicts(monkeypatch, tmp_path):
    """MCP digest_video 的 type 参数：slides → ppt+skip_asr；与 visual 互斥。"""
    captured = {}
    tr = Transcript([Segment(0.0, 5.0, "t")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.05)

    def fake_run(url, page=None, engine="auto", lang="zh", visual=False,
                 work_dir=None, skip_asr=False, no_gate=False):
        captured.update(visual=visual, skip_asr=skip_asr, no_gate=no_gate)
        return pipeline.Result(tr, {"bvid": "BV1tp", "title": "T", "duration": 5})
    monkeypatch.setattr(mcp_server.pipeline, "run", fake_run)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1tp", vtype="slides")["job_id"]
    assert wait_status(manager, jid, {"done"})["status"] == "done"
    assert captured == {"visual": "ppt", "skip_asr": True, "no_gate": False}
    with pytest.raises(mcp_server.ToolInputError, match="互斥"):
        mcp_server.validate_digest_args({"url": "BV1x", "type": "mixed", "visual": "auto"})
    assert mcp_server.validate_digest_args({"url": "BV1x", "type": "MIXED"})["vtype"] == "mixed"


class TestStdioAssetsChain:
    """S7 验收：get_assets / get_frame 子进程 JSON-RPC 全链路（stdout 纯净）。"""

    DRIVER_ASSETS = textwrap.dedent("""
        import base64, json, sys
        from bili_digest import mcp_server, pipeline
        from bili_digest.transcribe.base import Segment, Transcript
        from pathlib import Path

        run_dir = Path(sys.argv[1])
        (run_dir / "frames").mkdir(parents=True, exist_ok=True)
        (run_dir / "frames" / "f00.jpg").write_bytes(b"\\xff\\xd8JPEGDATA")

        tr = Transcript([Segment(0.0, 5.0, "画面文字")], lang="zh", engine="subtitle",
                        source="subtitle", elapsed_s=0.05)
        meta = {"bvid": "BV1s7", "title": "素材链路", "duration": 5,
                "frames": [{"id": "f00", "t": 1.0, "file": "frames/f00.jpg",
                            "desc": "白板图", "has_text": True, "type": "diagram"}]}

        def fake_run(url, page=None, engine="auto", lang="zh", visual=False,
                     work_dir=None, **kw):
            return pipeline.Result(tr, meta, run_dir=run_dir)
        pipeline.run = fake_run
        sys.exit(mcp_server.main())
    """)

    def test_assets_chain_over_stdio(self, tmp_path):
        proc = subprocess.Popen(
            [sys.executable, "-c", self.DRIVER_ASSETS, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            def send(msg):
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()

            def read_response(want_id):
                deadline = time.time() + 15
                while time.time() < deadline:
                    line = proc.stdout.readline()
                    assert line, "server 提前退出"
                    resp = json.loads(line)  # stdout 纯净性：逐行合法 JSON
                    if resp.get("id") == want_id:
                        return resp
                raise AssertionError("超时")

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "pytest", "version": "0"}}})
            read_response(1)
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "digest_video",
                             "arguments": {"url": "BV1s7", "visual": True}}})
            jid = json.loads(read_response(2)["result"]["content"][0]["text"])["job_id"]
            for i in range(3, 30):
                send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                      "params": {"name": "job_status", "arguments": {"job_id": jid}}})
                status = json.loads(read_response(i)["result"]["content"][0]["text"])
                if status["status"] == "done":
                    break
            else:
                raise AssertionError("job 未完成")
            send({"jsonrpc": "2.0", "id": 40, "method": "tools/call",
                  "params": {"name": "get_assets", "arguments": {"job_id": jid}}})
            assets = json.loads(read_response(40)["result"]["content"][0]["text"])
            assert assets["frames"][0]["frame_type"] == "diagram"
            assert (tmp_path / "assets.json").exists()
            send({"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                  "params": {"name": "get_frame",
                             "arguments": {"job_id": jid, "file": "frames/f00.jpg"}}})
            frame = json.loads(read_response(41)["result"]["content"][0]["text"])
            assert frame["mime"] == "image/jpeg"
            assert base64.b64decode(frame["base64"]) == (tmp_path / "frames" / "f00.jpg").read_bytes()
            # 越界 file → isError 结构化错误不崩
            send({"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                  "params": {"name": "get_frame",
                             "arguments": {"job_id": jid, "file": "../assets.json"}}})
            bad = read_response(42)["result"]
            assert bad["isError"] is True
            proc.stdin.close()
            assert proc.wait(timeout=10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()


# ---------------------------------------------------------------- S7.1：digest_doctor

def test_doctor_payload_structured(monkeypatch):
    """S7.2：5 元组（capability 在上）→ 结构化检查项 + capability 字段（旧字段保留）+
    汇总退出态（ping 全 mock）。"""
    monkeypatch.setattr(mcp_server.doctor, "run", lambda: [
        ("读图能力", "智谱 key", "ok", "实测通过（glm-5.3-flash）", "无需处理"),
        ("基础依赖", "ffmpeg", "fail", "未安装", "brew install ffmpeg"),
        ("小红书 Cookie", "XHS_COOKIE", "warn", "未配置（匿名可用）",
         "安装仓库内置的 cookie 导出插件：git clone https://github.com/bappleug/video-summarizer"
         " → Chrome 开发者模式加载已解压 → 选 cookie-exporter/ 目录 → 一键「导出到 keys.env」"
         "（首次需跑 cookie-exporter/native/install-host.sh 并重启 Chrome）"),
    ])
    payload = mcp_server.doctor_payload()
    caps = [c["capability"] for c in payload["checks"]]
    assert caps == ["读图能力", "基础依赖", "小红书 Cookie"]
    first = payload["checks"][0]
    assert first["name"] == "智谱 key" and first["state"] == "ok"  # 旧字段向后兼容
    xhs = payload["checks"][2]
    assert xhs["state"] == "warn"
    assert "video-summarizer" in xhs["action"] and "install-host.sh" in xhs["action"]
    assert payload["summary"]["exit_code"] == 1 and payload["summary"]["fails"] == 1
    assert payload["summary"]["warns"] == 1


def test_doctor_tool_dispatch(monkeypatch):
    monkeypatch.setattr(mcp_server.doctor, "run", lambda: [
        ("读图能力", "智谱 key", "warn", "未配置", "按需申请")])
    manager = mcp_server.JobManager()
    payload = mcp_server.tool_call(manager, "digest_doctor", {})
    assert payload["summary"]["checks"] == 1 and payload["checks"][0]["capability"] == "读图能力"


DRIVER_DOCTOR = textwrap.dedent("""
    import sys
    from bili_digest import mcp_server
    mcp_server.doctor.run = lambda: [
        ("读图能力", "智谱 key", "ok", "实测通过", "无需处理"),
        ("B站登录态", "凭据文件", "warn", "未登录", "执行 digest login 扫码登录"),
    ]
    mcp_server.config.get_key = lambda name: None
    sys.exit(mcp_server.main())
""")


class TestStdioDoctorChain:
    """digest_doctor 子进程 JSON-RPC 全链路（检查函数 mock，零真实 ping）。"""

    def test_doctor_over_stdio(self):
        proc = subprocess.Popen([sys.executable, "-c", DRIVER_DOCTOR],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8")
        lines = []
        try:
            def send(msg):
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()

            def read_response(want_id):
                deadline = time.time() + 15
                while time.time() < deadline:
                    line = proc.stdout.readline()
                    assert line, "server 提前退出"
                    lines.append(line)
                    resp = json.loads(line)  # stdout 纯净性
                    if resp.get("id") == want_id:
                        return resp
                raise AssertionError("超时")

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "pytest", "version": "0"}}})
            read_response(1)
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = read_response(2)["result"]["tools"]
            assert len(tools) == 9
            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "digest_doctor", "arguments": {}}})
            result = read_response(3)["result"]
            payload = json.loads(result["content"][0]["text"])
            by_cap = {c["capability"]: c["state"] for c in payload["checks"]}
            assert by_cap["读图能力"] == "ok" and by_cap["B站登录态"] == "warn"
            assert payload["summary"]["exit_code"] == 0 and payload["summary"]["warns"] == 1
            proc.stdin.close()
            assert proc.wait(timeout=10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
        for line in lines:
            json.loads(line)


# ---------------------------------------------------------------- S7.4：部分成功 / artifacts / output_dir

def test_partial_success_report_error(monkeypatch, tmp_path):
    """summarize 余额错误 → job 仍 done；meta.report_error 结构化；get_report 结构化错误。"""
    tr = Transcript([Segment(0.0, 5.0, "正文")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.05)
    meta = {"bvid": "BV1ps", "title": "部分成功", "duration": 5, "frames": []}
    monkeypatch.setattr(mcp_server.pipeline, "run",
                        lambda *a, **kw: pipeline.Result(tr, meta, run_dir=tmp_path))

    def boom(tr, meta, provider="siliconflow"):
        raise CliError("余额不足：到 https://cloud.siliconflow.cn 充值后重试", 3)
    monkeypatch.setattr(mcp_server.report, "summarize_transcript", boom)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1ps", summarize=True)["job_id"]
    status = wait_status(manager, jid, {"done"})
    assert status["status"] == "done"  # transcript 就绪，不因报告失败拖垮
    re_ = (json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
           .get("report_error"))
    assert re_["error"].startswith("余额不足") and re_["provider"]
    # S7.5 宿主接管协议：两条出路写进 host_hint（自行总结 / 充值后补跑）
    assert "get_transcript" in re_["host_hint"] and "get_assets/get_frame" in re_["host_hint"]
    assert "digest report" in re_["host_hint"]
    assert (tmp_path / "assets.json").exists() and not (tmp_path / "report.md").exists()
    with pytest.raises(mcp_server.ToolInputError, match="报告生成失败") as exc_info:
        manager.report_text(jid)
    msg = str(exc_info.value)
    assert "①" in msg and "②" in msg and "自行总结" in msg  # 两条出路
    # INSTRUCTIONS 出错接管规则（S7.6 瘦身版措辞）
    ins = mcp_server.INSTRUCTIONS
    assert "report_error/visual_error" in ins and "自行完成总结/读图" in ins
    assert "不要替用户充值或反复重试" in ins


def test_output_dir_param_and_artifacts_summary(monkeypatch, tmp_path):
    """output_dir（绝对/相对 runs 根）目录自建并作为产物目录；done 响应带 artifacts 摘要。"""
    from bili_digest import config as cfg
    monkeypatch.setattr(cfg, "runs_dir", lambda: tmp_path)
    abs_dir = tmp_path / "abs" / "run"
    args = mcp_server.validate_digest_args({"url": "BV1od", "output_dir": str(abs_dir)})
    assert args["work_dir"] == str(abs_dir) and abs_dir.is_dir()
    rel = mcp_server.validate_digest_args({"url": "BV1od", "output_dir": "rel/run"})
    assert rel["work_dir"] == str(tmp_path / "rel" / "run")
    with pytest.raises(mcp_server.ToolInputError):
        mcp_server.validate_digest_args({"url": "BV1od", "output_dir": " "})

    tr = Transcript([Segment(0.0, 5.0, "x")], lang="zh", engine="subtitle",
                    source="subtitle", elapsed_s=0.05)
    seen = {}

    def fake_run(url, page=None, engine="auto", lang="zh", visual=False,
                 work_dir=None, **kw):
        seen["work_dir"] = work_dir
        return pipeline.Result(tr, {"bvid": "BV1od", "title": "T", "duration": 5,
                                    "frames": []}, run_dir=Path(work_dir))
    monkeypatch.setattr(mcp_server.pipeline, "run", fake_run)
    manager = mcp_server.JobManager()
    jid = manager.submit("BV1od", visual=True, work_dir=args["work_dir"])["job_id"]
    status = wait_status(manager, jid, {"done"})
    assert seen["work_dir"] == str(abs_dir)
    arts = status["artifacts"]
    assert arts["transcript"]["hint"] == "内容用 get_transcript(job_id) 获取"
    assert arts["report"]["status"] == "not_requested"
    assert "get_assets(job_id)" in arts["assets"]["hint"]
    assert "仅供人工备查" in status["run_dir_note"]
    assert "产物内容一律用 get_transcript/get_assets/get_report 取，不读文件路径" \
        in mcp_server.INSTRUCTIONS
    assert (abs_dir / "assets.json").exists()  # 产物落在指定目录


def test_get_manual_dispatch():
    """S7.6：get_manual 无参数返回手册全文（费用口径修正后）。"""
    manager = mcp_server.JobManager()
    manual = mcp_server.tool_call(manager, "get_manual", {})
    assert manual is mcp_server.MANUAL
    assert "¥0.003/条" in manual and "¥0.005/篇" in manual
    assert "¥0.05" not in manual  # 旧口径清除
