"""digest 命令行入口。

内容输出走 stdout，进度和告警走 stderr（与 bili-subtitle 风格一致）。
退出码: 0 成功 / 2 用法错误 / 3 未登录或无字幕且 ASR 失败 / 4 网络或风控 / 1 其它。
"""

import argparse
import json
import sys
import time

from . import assets, config, doctor, enrich, pipeline
from .errors import CliError, log
from .platforms import bilibili
from .summarize import report


def out(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


# ---------------------------------------------------------------- 命令

def cmd_digest(args):
    if args.type is not None and args.visual is not None:
        raise CliError("--type 与 --visual 互斥（推荐 --type：talk|slides|mixed|auto）", 2)
    profile = pipeline.type_profile(args.type)
    visual = profile["visual"] if args.type is not None else args.visual
    want_report = args.summarize and not args.no_report
    result = pipeline.run(args.target, page=args.page, engine=args.engine, lang=args.lang,
                          visual=visual, skip_asr=profile["skip_asr"],
                          no_gate=profile["no_gate"],
                          reuse_cache=not args.no_reuse_cache)
    log(f"{result.transcript.engine}/{result.transcript.source} · "
        f"{len(result.transcript.segments)} 段 · {result.transcript.elapsed_s:.1f}s")
    if not (visual or want_report):
        out(pipeline.render(result.transcript, result.meta, args.format))
        return
    if visual:
        # pipeline 已建 run 目录（frames/ 与 download/ 已落盘），这里补 transcript 产物
        run_dir = result.run_dir
        enriched = enrich.fusion.render_enriched_md(
            result.transcript, result.meta.get("frames") or [], result.meta)
        report.save_run(run_dir, {
            "transcript.txt": pipeline.render_txt(result.transcript),
            "transcript.md": pipeline.render_md(result.transcript, result.meta),
            "transcript.json": pipeline.render_json(result.transcript, result.meta),
            "enriched_transcript.md": enriched,
        }, result.meta)
        assets.write_assets(run_dir, result.transcript, result.meta)
        log(f"transcript 已落盘 {run_dir}（含富化时间轴与 frames/ 与素材包）")
        if not want_report:
            if args.no_report:
                log("已按 --no-report 跳过总结报告（素材包已生成）")
            out(enriched)
            return
    else:
        run_dir = report.new_run_dir(result.meta.get("bvid") or result.meta.get("note_id"))
        report.save_run(run_dir, {
            "transcript.txt": pipeline.render_txt(result.transcript),
            "transcript.md": pipeline.render_md(result.transcript, result.meta),
            "transcript.json": pipeline.render_json(result.transcript, result.meta),
        }, result.meta)
        assets.write_assets(run_dir, result.transcript, result.meta)
        log(f"transcript 已落盘 {run_dir}（含素材包）")
        if not want_report:
            if args.no_report:
                log("已按 --no-report 跳过总结报告（素材包已生成）")
            out(pipeline.render(result.transcript, result.meta, args.format))
            return
    provider = args.provider or config.summarize_provider() or "siliconflow"
    try:
        report_md, usage = report.summarize_transcript(
            result.transcript, result.meta, provider=provider)
    except CliError as err:
        # S7.4 部分成功：transcript 是主产物，报告失败不拖垮退出码——meta 记结构化
        # report_error，事后可用 `digest report <run目录>` 单独补报告。
        result.meta["report_error"] = {"provider": provider, "error": str(err)}
        (run_dir / "meta.json").write_text(
            json.dumps(result.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"总结失败（{err}）——transcript 照常交付；"
            f"处理后可用 digest report {run_dir} 单独补报告")
        out(enriched if visual else pipeline.render(result.transcript, result.meta, args.format))
        return
    report.save_report(run_dir, report_md, usage)
    log(f"报告 {usage['model']} · {usage['total_tokens']} tokens · ¥{usage['cost_yuan']:.4f}")
    log(f"报告已写入 {run_dir / 'report.md'}")
    out(report_md)


def cmd_report(args):
    text, meta = report.load_run(args.run_dir)
    provider = args.provider or config.summarize_provider() or "siliconflow"
    report_md, usage = report.summarize_lines(text, meta, provider=provider)
    report.save_report(args.run_dir, report_md, usage)
    log(f"报告 {usage['model']} · {usage['total_tokens']} tokens · ¥{usage['cost_yuan']:.4f}")
    log(f"报告已写入 {args.run_dir}/report.md")
    out(report_md)


def cmd_doctor(args):
    return doctor.main()


def cmd_login(args):
    cred = bilibili.qr_login(args.timeout, args.open, args.url, out=out)
    bilibili.save_cred(cred)
    log(f"凭据已保存: {bilibili.CRED_PATH}")
    try:
        nav = bilibili.api_json(
            f"{bilibili.API}/x/web-interface/nav",
            cookie=bilibili.cookie_header(cred["cookies"]),
        )
        if nav.get("code") == 0:
            log(f"登录账号: {nav['data'].get('uname')} (mid {nav['data'].get('mid')})")
    except CliError:
        pass
    out("ok")


def cmd_status(args):
    state = bilibili.login_state()
    cred = state["cred"] or {}
    info = {
        "logged_in": state["logged_in"],
        "uname": state["uname"],
        "mid": state["mid"],
        "login_at": cred.get("login_at"),
        "has_refresh_token": bool(cred.get("refresh_token")),
        "cred_path": bilibili.CRED_PATH,
        "source": state["source"],
    }
    if args.json:
        out(json.dumps(info, ensure_ascii=False, indent=2))
        return
    if not info["logged_in"]:
        head = "未登录" if info["source"] is None else "登录态已失效"
        out(f"{head}：{bilibili.login_hint(info['source'])}")
        return
    age = f"，凭据已用 {(int(time.time()) - int(cred.get('login_at', 0))) // 86400} 天" if cred else ""
    out(f"已登录: {info['uname']} (mid {info['mid']}){age}")
    out(f"凭据来源: {'扫码凭据（credentials.json）' if cred else '插件 Cookie（BILI_COOKIE）'}")
    out("自动续期: 不可用（B站已改机制），登录态失效后需重新登录")


def cmd_mcp(args):
    """stdio MCP server：stdout 只有协议帧（一切日志走 stderr），供豆包等客户端接入。"""
    try:
        import mcp  # noqa: F401 — 只探测 [mcp] extra 是否安装
    except ImportError:
        log("缺少 mcp SDK：UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple "
            "uv pip install --python .venv/bin/python -e '.[mcp]'（详见 README「三步上手 → ② 接入你的 MCP 客户端」）")
        return 2
    from . import mcp_server
    return mcp_server.main()


# ---------------------------------------------------------------- CLI

def build_parser():
    parser = argparse.ArgumentParser(
        prog="digest", description="B站/小红书视频 transcript 提取（字幕优先，无字幕回退本地 ASR）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("digest", help="URL/BV号 → transcript")
    p.add_argument("target", help="链接 / BV 号 / av 号 / 小红书分享链接（URL 带 ?p=N 可自动识别分P）")
    p.add_argument("--page", type=int, help="第几个分P（默认取 URL 的 p 参数，否则 1）")
    p.add_argument("--format", choices=("txt", "srt", "md", "json"), default="txt",
                   help="产物格式（默认 txt）")
    p.add_argument("--engine", choices=("auto", "mlx", "cloud"), default="auto",
                   help="ASR 引擎（默认 auto：mlx 可用走本地，否则云端 SenseVoice）")
    p.add_argument("--lang", default=None,
                   help="ASR 语言（默认自动检测：mlx-whisper 自带检测；显式指定如 zh/en）")
    p.add_argument("--summarize", action="store_true",
                   help="同时生成 LLM 总结报告（落盘 runs/<bvid>/<run-id>/，含 usage 成本）")
    p.add_argument("--visual", nargs="?", const="auto", default=None,
                   choices=("auto", "ppt"), metavar="{auto,ppt}",
                   help="不加 --visual = 纯音频模式（不下载视频、零读图费用）。加 = 画面增强："
                        "关键帧提取（敏感边界检测 + 聚簇取末，页面取最全状态；超上限抽稀时"
                        "静态页优先、动态段每 30s 一代表，帧数上限按时长伸缩至 60）+ VLM 读图"
                        "+ 富化时间轴/报告；--visual 或 --visual=auto = 自动混合（含画面价值"
                        "门控，口播类自动降级不读图）；--visual=ppt = PPT/讲义模式（跳过门控）。"
                        "读图硅基流动 Qwen3-VL-8B，约 ¥0.003/条。与 --type 互斥（推荐 --type）")
    p.add_argument("--type", choices=("talk", "slides", "mixed", "auto"), default=None,
                   help="内容形态（用户词汇，推荐用它替代 --visual）：talk=内容在说话"
                        "（访谈/口播/旁白：纯音频不下载视频）；slides=内容在画面"
                        "（PPT/图文卡/无声演示：ppt 取帧 + 跳过 ASR，transcript 标 "
                        "visual-only；要保险用 mixed）；mixed=两边都要（auto 取帧 + "
                        "禁用口播门控，双通道保证）；auto=系统默认。与 --visual 互斥")
    p.add_argument("--no-report", action="store_true",
                   help="素材场景：跳过 LLM 总结报告（report.md 不生成；assets 素材包仍产出）")
    p.add_argument("--no-reuse-cache", action="store_true",
                   help="禁用视频轨缓存复用：强制重新下载并刷新缓存（默认复用；"
                        "缓存位置 <当前工作目录>/.bili-digest-cache/videos，"
                        "可用 BILI_DIGEST_CACHE_DIR 覆盖）")
    p.add_argument("--provider", choices=("zhipu", "deepseek", "siliconflow"), default=None,
                   help="总结 LLM provider（默认 siliconflow/DeepSeek-V4.1-Flash；keys.env 可用 "
                        "SUMMARIZE_PROVIDER=zhipu|deepseek|siliconflow 设持久化默认，"
                        "本参数显式给出时优先）")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("report", help="对已有 run 目录的 transcript 补生成总结报告")
    p.add_argument("run_dir", help="run 目录路径（含 transcript.json / transcript.txt）")
    p.add_argument("--provider", choices=("zhipu", "deepseek", "siliconflow"), default=None,
                   help="总结 LLM provider（默认 siliconflow/DeepSeek-V4.1-Flash；keys.env 可用 "
                        "SUMMARIZE_PROVIDER=zhipu|deepseek|siliconflow 设持久化默认，"
                        "本参数显式给出时优先）")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("doctor", help="环境与 key 体检（能力 × 状态 × 下一步动作）")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("login", help="扫码登录（凭据与 bili-subtitle 共享）")
    p.add_argument("--timeout", type=int, default=180, help="等待扫码秒数（默认 180）")
    p.add_argument("--open", action="store_true", help="在浏览器打开确认页，可直接点「确认」登录")
    p.add_argument("--url", action="store_true", help="只输出二维码链接，不画二维码")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", help="查看登录态")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("mcp", help="MCP stdio server（9 工具 + 后台 job，需 [mcp] extra）")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    # 顶层形态 `digest <url|BV号> [options]`；login/status/report/doctor/mcp 是子命令
    if argv and argv[0] in ("login", "status", "digest", "report", "doctor", "mcp"):
        args = build_parser().parse_args(argv)
    else:
        args = build_parser().parse_args(["digest"] + argv)
    try:
        code = args.func(args)
    except CliError as err:
        log(str(err))
        return err.code
    except KeyboardInterrupt:
        log("已取消")
        return 130
    return code if isinstance(code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
