"""digest mcp：MCP stdio server（S3）——7 工具 + 进程内后台 job 管理。

服务化包装，核心管线零改动（pipeline/report 直接复用）。stdout 只有 MCP 协议帧：
- 本模块与管线的一切日志走 errors.log（stderr）；
- mcp SDK 的 stdio_server() 服务期间把 fd0 指到 /dev/null、fd1 指到 stderr，
  协议线走私有的 fd 副本，第三方库的 stray print 也进不了帧流。
依赖官方 mcp SDK（[mcp] extra），且只在 build_server/main 里延迟 import——
JobManager / tool_call 是纯 stdlib，无 SDK 也能导入测试。
job 进程内管理（dict + threading），不做持久化（S5 的事）；进程退出即丢。
"""

from __future__ import annotations

import base64
import json
import math
import threading
import time
import uuid
from pathlib import Path

from . import __version__, assets as assets_mod, config, doctor, enrich, pipeline
from .errors import CliError, log
from .summarize import report

SERVER_NAME = "bili-digest"
TRANSCRIPT_FORMATS = ("txt", "srt", "md", "json")
ENGINES = ("auto", "mlx", "cloud")
TYPES = ("talk", "slides", "mixed", "auto")
FRAME_EXTS = (".jpg", ".jpeg", ".png")
MAX_URL_LEN = 2048
MAX_JOBS = 200  # 长驻进程的内存上限：超出先清最旧的已终态 job

INSTRUCTIONS = (
    "bili-digest：帮用户看懂 B站/小红书视频（字幕/语音转文字，可配画面理解与总结）。\n"
    "【沟通原则】回答使用问题时只讲场景与推荐操作，"
    "不罗列参数表/工具清单/内部机制/费用明细，除非用户追问。\n"
    "【剧本】① 想快速看懂视频 → digest_video(url, summarize=true) → 轮询 job_status 到 done → "
    "get_report；② 要文字稿 → summarize=false → get_transcript；"
    "③ 要素材二次创作（做 PPT 等）→ summarize=false → get_assets/get_frame 自行加工。\n"
    "用户主动说是 PPT / 没有语音 / 说话画面都要时才传 type，其余不传。\n"
    "【首次使用】先调 digest_doctor 体检，红项按指引带用户配置后复检。\n"
    "【出错接管】任务返回 report_error/visual_error 时直接切姿势②/③自行完成总结/读图，"
    "向用户说明该步骤不可用及原因；不要替用户充值或反复重试。\n"
    "key 一律填 ~/.config/ai-tools/bili-digest/keys.env，不经对话传递；"
    "产物内容一律用 get_transcript/get_assets/get_report 取，不读文件路径。\n"
    "用户要完整技术细节/参数/手册时，调 get_manual 获取后转述。"
)

# 完整技术手册（get_manual 按需返回；费用口径 2026-11-22 硅基流动实测标称）：
# 视觉 ≈¥0.003/条（Qwen3-VL-8B 读图）、总结 ≈¥0.005/篇（DeepSeek-V4.1-Flash）、云转写 ¥0。
MANUAL = (
    "# bili-digest 技术手册（get_manual 按需提供；宿主只在用户要技术细节时转述）\n\n"
    "## 参数速查（digest_video）\n"
    "- url（必填）：B站链接/BV号/av号，或小红书分享链接（?p=N 自动识别分P）\n"
    "- type：talk=纯音频｜slides=画面为主（ppt 取帧+跳 ASR）｜mixed=双通道禁门控｜"
    "auto=默认；与 visual 互斥\n"
    "- visual：false/auto/ppt 三态（type 的底层等价物，推荐用 type）\n"
    "- summarize：true=工具出 LLM 报告；false=素材进宿主上下文\n"
    "- engine：auto|mlx|cloud（本地 mlx 优先，云端 SenseVoice 免费兜底）\n"
    "- page：分P 序号；format：txt|srt|md|json（get_transcript 产物格式）\n"
    "- output_dir：产物目录（绝对或相对 runs 根，目录自建）\n"
    "- reuse_cache：true（默认）=同一视频二次运行复用缓存、跳过下载；"
    "false=强制重下并刷新缓存。缓存在 Agent 工作目录/.bili-digest-cache/videos/，"
    "删整个 .bili-digest-cache 即可手动清理\n\n"
    "## 工具清单（9 个）\n"
    "digest_video（提交任务）→ job_status（轮询，done 带 artifacts 摘要）→ "
    "get_transcript / get_report / get_assets（素材清单）/ get_frame（帧图 base64，多模态复用）；"
    "cancel（协作取消）；digest_doctor（体检+配置指引）；get_manual（本手册）\n\n"
    "## 两种姿势（按宿主积分余量选）\n"
    "A. summarize=true：工具出报告（返回内容少，费用走用户 key）；"
    "B. summarize=false + get_assets/get_transcript/get_frame：素材进宿主上下文自行加工"
    "（费用走宿主）。\n\n"
    "## 费用与模型（默认 siliconflow 一把 key）\n"
    "- 视觉（读图）：Qwen/Qwen3-VL-8B-Instruct，≈¥0.003/条视频\n"
    "- 总结：deepseek-ai/DeepSeek-V4.1-Flash，≈¥0.005/篇\n"
    "- 云转写（SenseVoice）：¥0（免费档）；本地 mlx：¥0\n"
    "- 可选备用：智谱（glm-5.3-flash）/DeepSeek 官方，手动切换用 "
    "SUMMARIZE_PROVIDER / VISION_PROVIDER / VISION_MODEL（keys.env 或环境变量），不自动兜底\n\n"
    "## 内部机制\n"
    "字幕优先（污染校验四规则，不过则降级 ASR）；视觉走敏感边界检测取帧"
    "（pHash 聚簇取每页最全状态 + 静态簇优先抽稀）；auto 模式带口播门控"
    "（低多样性/口播型自动降级不读图；type=slides/mixed 跳过）；"
    "供应商内网络错误退避重试一次，跨供应商失败落 report_error 由宿主接管。"
)


class ToolInputError(ValueError):
    """非法的工具入参/用法（携带修法提示）。MCP 层转 isError:true 结构化错误，服务不崩。"""


# ---------------------------------------------------------------- 参数防御

def validate_digest_args(args: dict) -> dict:
    """digest_video 入参校验与归一（AI 客户端常传字符串数字/带空格枚举，宽松收敛、非法拒绝）。"""
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolInputError("url 必填：B站视频链接 / BV号 / av号，或小红书视频笔记分享链接")
    url = url.strip()
    if len(url) > MAX_URL_LEN:
        raise ToolInputError(f"url 过长（{len(url)} > {MAX_URL_LEN} 字符），疑似粘贴错误")

    summarize = args.get("summarize", False)
    if isinstance(summarize, str):
        text = summarize.strip().lower()
        if text not in ("true", "false", "1", "0"):
            raise ToolInputError(f"summarize 需要布尔值（true/false），收到: {summarize!r}")
        summarize = text in ("true", "1")
    elif isinstance(summarize, int) and not isinstance(summarize, bool):
        summarize = bool(summarize)
    elif not isinstance(summarize, bool):
        raise ToolInputError(f"summarize 需要布尔值，收到: {summarize!r}")

    visual = args.get("visual", False)
    if isinstance(visual, str):
        text = visual.strip().lower()
        if text in ("auto", "ppt"):  # S6.1 模式：auto 混合（门控）/ ppt 讲义（跳过门控）
            visual = text
        elif text in ("true", "1"):
            visual = "auto"
        elif text in ("false", "0", ""):
            visual = False
        else:
            raise ToolInputError(f"visual 需要 false | \"auto\" | \"ppt\"，收到: {visual!r}")
    elif isinstance(visual, int) and not isinstance(visual, bool):
        visual = "auto" if visual else False
    elif visual is True:
        visual = "auto"
    elif visual is not False:
        raise ToolInputError(f"visual 需要 false | \"auto\" | \"ppt\"，收到: {visual!r}")

    vtype = args.get("type") or "auto"  # S7 用户词汇（与 visual 互斥）
    if not isinstance(vtype, str) or vtype.strip().lower() not in TYPES:
        raise ToolInputError(f"type 只支持 {'|'.join(TYPES)}，收到: {vtype!r}")
    vtype = vtype.strip().lower()
    if vtype != "auto" and visual is not False:
        raise ToolInputError("type 与 visual 互斥（推荐 type：talk|slides|mixed|auto）")

    engine = args.get("engine") or "auto"
    if engine not in ENGINES:
        raise ToolInputError(f"engine 只支持 auto|mlx|cloud，收到: {engine!r}")

    page = args.get("page")
    if page is not None:
        if not isinstance(page, int) or isinstance(page, bool):
            try:
                page = int(str(page).strip())
            except (TypeError, ValueError):
                raise ToolInputError(f"page 需要整数（分P 序号，从 1 开始），收到: {args['page']!r}") from None
        if page < 1:
            raise ToolInputError("page 需要整数 ≥ 1（分P 序号从 1 开始）")

    fmt = args.get("format") or "txt"
    if fmt not in TRANSCRIPT_FORMATS:
        raise ToolInputError(f"format 只支持 txt|srt|md|json，收到: {fmt!r}")

    output_dir = args.get("output_dir")
    work_dir = None
    if output_dir is not None:
        if not isinstance(output_dir, str) or not output_dir.strip():
            raise ToolInputError(f"output_dir 需为非空字符串，收到: {output_dir!r}")
        from . import config as _config
        path = Path(output_dir.strip()).expanduser()
        if not path.is_absolute():
            path = _config.runs_dir() / path  # 相对路径锚定 runs 根
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolInputError(f"output_dir 无法创建（{exc}）: {output_dir}") from None
        work_dir = str(path)
    reuse_cache = args.get("reuse_cache", True)
    if not isinstance(reuse_cache, bool):
        raise ToolInputError(f"reuse_cache 需为布尔值，收到: {reuse_cache!r}")

    return {"url": url, "summarize": summarize, "engine": engine, "page": page,
            "fmt": fmt, "visual": visual, "vtype": vtype, "work_dir": work_dir,
            "reuse_cache": reuse_cache}


def _job_id(args: dict) -> str:
    jid = args.get("job_id")
    if not isinstance(jid, str) or not jid.strip():
        raise ToolInputError("缺少参数 job_id（digest_video 返回的任务 ID）")
    return jid.strip()


def _fmt(args: dict, key: str = "format") -> str | None:
    fmt = args.get(key)
    if fmt is None or fmt == "":
        return None
    if fmt not in TRANSCRIPT_FORMATS:
        raise ToolInputError(f"{key} 只支持 txt|srt|md|json，收到: {fmt!r}")
    return fmt


# ---------------------------------------------------------------- job 管理

class Job:
    """单个后台任务的可观测状态（读写都在 JobManager 锁内）。"""

    def __init__(self, url, summarize, engine, page, fmt, visual=False, vtype="auto",
                 work_dir=None, reuse_cache=True):
        self.job_id = uuid.uuid4().hex[:8]
        self.url = url
        self.summarize = summarize
        self.engine = engine
        self.page = page
        self.format = fmt
        self.visual = visual
        self.vtype = vtype  # S7 --type 用户词汇（auto 时 visual 生效）
        self.work_dir = work_dir  # S7.4 output_dir（绝对/相对 runs 根；目录自建）
        self.reuse_cache = reuse_cache  # 视频轨缓存复用（默认 True，reuse_cache=false 强制重下）
        self.status = "queued"  # queued|running|done|error|cancelled
        self.progress = "排队中"
        self.error = None
        self.cancel_event = threading.Event()
        self.transcript = None  # pipeline.Result.transcript
        self.meta = None
        self.report_md = None
        self.run_dir = None  # summarize 落盘目录（Path）
        self.created_at = time.time()


class JobManager:
    """进程内 job 注册表：submit 起后台线程，cancel 在阶段边界协作生效。"""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ---- 提交与查询

    def submit(self, url, summarize=False, engine="auto", page=None, fmt="txt",
               visual=False, vtype="auto", work_dir=None, reuse_cache=True) -> dict:
        job = Job(url, summarize, engine, page, fmt, visual, vtype, work_dir, reuse_cache)
        with self._lock:
            if len(self._jobs) >= MAX_JOBS:
                self._prune_locked()
                if len(self._jobs) >= MAX_JOBS:
                    raise ToolInputError(f"job 数已达上限 {MAX_JOBS}（进程内不持久，重启即清）")
            self._jobs[job.job_id] = job
        threading.Thread(target=self._execute, args=(job,),
                         name=f"digest-job-{job.job_id}", daemon=True).start()
        return {"job_id": job.job_id, "status": job.status}

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ToolInputError(f"job 不存在: {job_id}（进程重启会丢历史 job，重新 digest_video 提交）")
        return job

    def status(self, job_id: str) -> dict:
        job = self.get(job_id)
        with self._lock:
            payload = {
                "job_id": job.job_id,
                "url": job.url,
                "status": job.status,
                "progress": job.progress,
            }
            if job.error is not None:
                payload["error"] = job.error
            if job.transcript is not None:
                payload["transcript"] = {
                    "engine": job.transcript.engine,
                    "source": job.transcript.source,
                    "segments": len(job.transcript.segments),
                }
            if job.meta is not None and job.meta.get("frames") is not None:
                payload["frames"] = len(job.meta["frames"])  # --visual 的关键帧数
            if job.run_dir is not None:
                payload["run_dir"] = str(job.run_dir)
                payload["run_dir_note"] = "路径仅供人工备查；产物内容一律通过 get_* 工具获取"
            if job.status == "done":
                payload["artifacts"] = {  # S7.4 产物可达性摘要
                    "transcript": {"formats": list(TRANSCRIPT_FORMATS),
                                   "hint": "内容用 get_transcript(job_id) 获取"},
                    "report": {"status": ("failed" if (job.meta or {}).get("report_error")
                                          else ("ok" if job.report_md else "not_requested")),
                               "hint": "报告用 get_report(job_id) 获取"},
                    "assets": {"hint": "帧清单用 get_assets(job_id)；单帧图 get_frame(job_id, file)"},
                }
        return payload

    def transcript_text(self, job_id: str, fmt=None) -> str:
        """done 后按需渲染；fmt 缺省回退 digest_video 提交时的 format。"""
        job = self.get(job_id)
        if job.status == "cancelled":
            raise ToolInputError("job 已取消，transcript 不可用；重新 digest_video 提交")
        if job.status != "done":
            raise ToolInputError(f"transcript 尚不可用（当前状态: {job.status}）——轮询 job_status 到 done 再取")
        return pipeline.render(job.transcript, job.meta, fmt or job.format)

    def report_text(self, job_id: str) -> str:
        job = self.get(job_id)
        if not job.summarize:
            raise ToolInputError("该 job 未开启 summarize，没有报告（digest_video 时传 summarize=true）")
        if job.status == "done" and (job.meta or {}).get("report_error"):
            err = job.meta["report_error"]
            raise ToolInputError(
                f"报告生成失败（provider {err.get('provider') or '?'}）：{err.get('error')}"
                "——两条出路：① 直接调 get_transcript 取全文由你自行总结"
                "（如需看图用 get_assets/get_frame）；② 引导用户按错误指引充值/处理后"
                f"用 CLI `digest report {job.run_dir}` 补跑")
        if job.status == "cancelled":
            raise ToolInputError("job 已取消，报告不可用；重新 digest_video 提交")
        if job.status != "done":
            raise ToolInputError(f"报告尚未生成（当前状态: {job.status}）——轮询 job_status 到 done 再取")
        return job.report_md

    # ---- 素材复用（S7）

    def assets_payload(self, job_id: str) -> dict:
        """done 后读 run 目录的 assets.json（帧清单+desc+transcript 概要）。"""
        job = self._done_or_bust(job_id)
        path = job.run_dir / "assets.json" if job.run_dir else None
        if not path or not path.exists():
            raise ToolInputError("该 job 没有素材包（visual/summarize 任务才会落 run 目录与素材）")
        return json.loads(path.read_text(encoding="utf-8"))

    def frame_b64(self, job_id: str, file: str) -> dict:
        """run 目录内指定帧图 → {file, mime, base64}（多模态客户端直接看图复用）。"""
        job = self._done_or_bust(job_id)
        if not isinstance(file, str) or not file.strip():
            raise ToolInputError("缺少参数 file（如 frames/f00.jpg，用 get_assets 查帧清单）")
        file = file.strip()
        if not job.run_dir:
            raise ToolInputError("该 job 没有 run 目录（无视觉产物）")
        path = (job.run_dir / file).resolve()
        if job.run_dir.resolve() not in path.parents or path.suffix.lower() not in FRAME_EXTS:
            raise ToolInputError(f"file 需为 run 目录内的图片（如 frames/f00.jpg），收到: {file!r}")
        if not path.exists():
            raise ToolInputError(f"文件不存在: {file}（用 get_assets 查帧清单）")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return {"file": file, "mime": mime,
                "base64": base64.b64encode(path.read_bytes()).decode("ascii")}

    def _done_or_bust(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status == "cancelled":
            raise ToolInputError("job 已取消，素材不可用；重新 digest_video 提交")
        if job.status != "done":
            raise ToolInputError(f"素材尚未就绪（当前状态: {job.status}）——轮询 job_status 到 done 再取")
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        with self._lock:
            if job.status in ("done", "error", "cancelled"):
                return {"job_id": job.job_id, "status": job.status,
                        "cancelled": False, "note": "job 已是终态，无需取消"}
            job.cancel_event.set()
            if job.status == "queued":
                self._settle_locked(job, "cancelled", "已取消")
                return {"job_id": job.job_id, "status": "cancelled", "cancelled": True}
        # running：协作取消点在阶段边界，本阶段（转写/总结）跑完才落定
        return {"job_id": job.job_id, "status": job.status, "cancelled": True,
                "note": "已请求取消，当前阶段结束后生效（轮询 job_status 确认）"}

    # ---- 后台执行（阶段边界查取消；CliError 含修法指引原样透传进 error 字段）

    def _execute(self, job: Job):
        try:
            if job.cancel_event.is_set():
                return self._settle(job, "cancelled", "已取消")
            profile = pipeline.type_profile(job.vtype)  # S7：type 语义糖衣
            visual = job.visual if job.vtype == "auto" else profile["visual"]
            with self._lock:
                job.status, job.progress = "running", "获取 transcript（字幕优先，无字幕回退 ASR）"
            result = pipeline.run(job.url, page=job.page, engine=job.engine, visual=visual,
                                  skip_asr=profile["skip_asr"], no_gate=profile["no_gate"],
                                  work_dir=job.work_dir, reuse_cache=job.reuse_cache)
            job.transcript, job.meta = result.transcript, result.meta
            if result.run_dir is not None:
                job.run_dir = result.run_dir  # --visual 的产物目录（frames/ 已落盘）
            if job.cancel_event.is_set():
                return self._settle(job, "cancelled", "已取消（transcript 已丢弃）")
            if not (job.summarize or visual):
                return self._settle(job, "done", "完成")
            stages = ["落盘 transcript"]
            if visual:
                stages.insert(0, "画面增强产物")
            if job.summarize:
                stages.append("生成 LLM 总结报告")
            with self._lock:
                job.progress = " → ".join(stages)
            run_dir = result.run_dir or (Path(job.work_dir) if job.work_dir else None) or \
                report.new_run_dir(job.meta.get("bvid") or job.meta.get("note_id"))
            files = {
                "transcript.txt": pipeline.render_txt(job.transcript),
                "transcript.md": pipeline.render_md(job.transcript, job.meta),
                "transcript.json": pipeline.render_json(job.transcript, job.meta),
            }
            if visual:
                files["enriched_transcript.md"] = enrich.fusion.render_enriched_md(
                    job.transcript, job.meta.get("frames") or [], job.meta)
            report.save_run(run_dir, files, job.meta)
            assets_mod.write_assets(run_dir, job.transcript, job.meta)  # S7 素材包
            if not job.summarize:
                job.run_dir = run_dir
                return self._settle(job, "done", f"完成（视觉增强产物 {run_dir}）")
            try:
                report_md, usage = report.summarize_transcript(job.transcript, job.meta)
            except CliError as err:
                # S7.4 部分成功：transcript 就绪则 job 仍 done，meta 记结构化 report_error
                job.meta["report_error"] = {
                    "provider": config.summarize_provider() or "siliconflow",
                    "error": str(err),
                    "host_hint": "宿主可直接 get_transcript 取全文自行总结"
                                 "（如需看图用 get_assets/get_frame）；"
                                 "或引导用户按错误指引充值/处理后用 CLI "
                                 f"`digest report {run_dir}` 补跑",
                }
                (run_dir / "meta.json").write_text(
                    json.dumps(job.meta, ensure_ascii=False, indent=2), encoding="utf-8")
                job.run_dir = run_dir
                return self._settle(job, "done", f"完成（transcript 就绪；报告失败：{err}）")
            if job.cancel_event.is_set():
                return self._settle(job, "cancelled", "已取消（报告已丢弃）")
            report.save_report(run_dir, report_md, usage)
            job.report_md, job.run_dir = report_md, run_dir
            log(f"job {job.job_id} 报告 {usage['model']} · {usage['total_tokens']} tokens · "
                f"¥{usage['cost_yuan']:.4f} · {run_dir}")
            self._settle(job, "done", "完成")
        except CliError as err:
            self._settle(job, "error", "失败", error=str(err))
        except Exception as exc:  # noqa: BLE001 — 后台线程异常不能带崩 server
            log(f"job {job.job_id} 异常: {type(exc).__name__}: {exc}")
            self._settle(job, "error", "失败", error=f"{type(exc).__name__}: {exc}")

    def _settle(self, job: Job, status: str, progress: str, error=None):
        with self._lock:
            self._settle_locked(job, status, progress, error)

    def _settle_locked(self, job: Job, status: str, progress: str, error=None):
        if job.status in ("done", "error", "cancelled"):
            return  # 终态不可逆（cancel 与线程收尾的竞态下不覆盖）
        job.status, job.progress, job.error = status, progress, error

    def _prune_locked(self):
        terminal = sorted((j for j in self._jobs.values()
                           if j.status in ("done", "error", "cancelled")),
                          key=lambda j: j.created_at)
        for j in terminal[:len(self._jobs) - MAX_JOBS + 1]:
            del self._jobs[j.job_id]


# ---------------------------------------------------------------- digest_doctor（S7.1）

def doctor_payload() -> dict:
    """doctor 体检 → 结构化结果（S7.2 能力分组：capability + name/state/detail/action，
    旧字段全保留，向后兼容）。指引文案含 key 注册入口 URL、keys.env 路径、
    XHS_COOKIE 用仓库内置 cookie-exporter 插件一键导出的方法。
    注意：check_key 对已配置 key 做真实 ping（约 ¥0.001/项）——首次使用/疑似凭据问题时调用。"""
    rows = doctor.run()  # [(capability, item, state, detail, action)]，XHS 行已含在内
    checks = [{"capability": cap, "name": n, "state": s, "detail": d, "action": a}
              for cap, n, s, d, a in rows]
    fails = [c for c in checks if c["state"] == "fail"]
    warns = [c for c in checks if c["state"] == "warn"]
    summary = {
        "exit_code": 1 if fails else 0,
        "fails": len(fails), "warns": len(warns), "checks": len(checks),
        "conclusion": (f"{len(fails)} 项异常，按各检查项「下一步动作」指导用户配置后复检"
                       if fails else f"无异常（{len(warns)} 项可按需优化）"),
    }
    return {"checks": checks, "summary": summary}


# ---------------------------------------------------------------- 工具分发（纯 stdlib）

def tool_call(manager: JobManager, name, arguments) -> dict | str:
    """按工具名分发；返回 dict（JSON 呈现）或 str（纯文本呈现）。

    非 dict 的 arguments（客户端塞了字符串/数组）按空参处理，让各工具的
    必填校验给出可读错误，而不是 KeyError 崩掉。
    """
    args = arguments if isinstance(arguments, dict) else {}
    if name == "digest_video":
        return manager.submit(**validate_digest_args(args))
    if name == "job_status":
        return manager.status(_job_id(args))
    if name == "get_transcript":
        return manager.transcript_text(_job_id(args), _fmt(args))
    if name == "get_report":
        return manager.report_text(_job_id(args))
    if name == "get_assets":
        return manager.assets_payload(_job_id(args))
    if name == "get_frame":
        return manager.frame_b64(_job_id(args), args.get("file"))
    if name == "cancel":
        return manager.cancel(_job_id(args))
    if name == "digest_doctor":
        return doctor_payload()
    if name == "get_manual":
        return MANUAL
    raise ToolInputError(
        f"未知工具: {name}（可用: digest_video|job_status|get_transcript|get_report|"
        "get_assets|get_frame|cancel|digest_doctor|get_manual）")


# ---------------------------------------------------------------- 工具 schema（冻结命名）

TOOLS: list[dict] = [
    {
        "name": "digest_video",
        "description": "提交 B站/小红书视频任务（后台执行，立即返回 job_id）。"
                       "要总结报告传 summarize=true（完成后 get_report 取）；"
                       "要文字稿或素材传 false（get_transcript / get_assets+get_frame 取）。"
                       "用户说是 PPT/无语音/画面为主时传 type=slides，双通道传 mixed。"
                       "完整参数见 get_manual。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "B站视频链接 / BV号 / av号（URL 带 ?p=N 自动识别分P），或小红书视频笔记分享链接"},
                "summarize": {"type": "boolean", "default": False,
                              "description": "是否同时生成 LLM 总结报告（get_report 可取）"},
                "visual": {"oneOf": [{"type": "boolean"}, {"type": "string", "enum": ["auto", "ppt"]}],
                           "default": False,
                           "description": "视觉增强（false=纯音频不下载视频；\"auto\"=自动混合，"
                                          "含画面价值门控（口播类自动降级不读图）；\"ppt\"=PPT/讲义模式，"
                                          "跳过门控）：关键帧提取 + VLM 读图"
                                          "（≈¥0.003/条），产物落 run 目录"
                                          "（frames/ + 富化时间轴 + 素材包）。与 type 互斥"},
                "type": {"type": "string", "enum": list(TYPES), "default": "auto",
                         "description": "内容形态（用户词汇，推荐用它替代 visual；互斥）："
                                        "talk=内容在说话（纯音频）；slides=内容在画面"
                                        "（ppt 取帧+跳过 ASR，transcript 标 visual-only）；"
                                        "mixed=两边都要（auto 取帧+禁用口播门控，双通道保证）；"
                                        "auto=系统默认（此时 visual 参数生效）"},
                "engine": {"type": "string", "enum": list(ENGINES), "default": "auto",
                           "description": "ASR 引擎（auto：本地 mlx 可用走本地，否则云端 SenseVoice）"},
                "page": {"type": "integer", "description": "第几个分P（默认取 URL 的 p 参数，否则 1）"},
                "format": {"type": "string", "enum": list(TRANSCRIPT_FORMATS), "default": "txt",
                           "description": "get_transcript 的默认产物格式"},
                "output_dir": {"type": "string",
                               "description": "产物目录（可选；绝对路径或相对 runs 根的路径，"
                                              "目录自动创建；缺省按视频 ID 自动命名）"},
                "reuse_cache": {"type": "boolean", "default": True,
                                "description": "复用视频轨缓存（默认 true）：同一视频二次运行"
                                               "跳过下载。缓存在 Agent 工作目录/.bili-digest-cache/videos/，"
                                               "可自行删除整个 .bili-digest-cache 清理。传 false 强制重下"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "job_status",
        "description": "查询任务状态与阶段：queued | running | done | error | cancelled；"
                       "error 时 error 字段带修法指引（如 digest login / 配 key）。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "任务 ID（digest_video 返回）"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_transcript",
        "description": "读取已完成任务的 transcript 文本。done 后可用；format 缺省取 digest_video 提交时的 format。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "任务 ID（digest_video 返回）"},
                "format": {"type": "string", "enum": list(TRANSCRIPT_FORMATS),
                           "description": "txt | srt | md | json（缺省取提交时的 format，最终回退 txt）"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "get_report",
        "description": "读取 summarize=true 任务的 Markdown 总结报告（仅此类任务有）。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "任务 ID（digest_video 返回）"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_assets",
        "description": "读取任务的素材包（assets.json）：帧清单（file/时间/frame_type/desc）+ "
                       "transcript 概要（files/segments/source/lang）。done 后可用；"
                       "配合 get_frame 可把帧图直接拉回多模态客户端复用。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "任务 ID（digest_video 返回）"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_frame",
        "description": "读取任务 run 目录内指定帧图的 base64（多模态客户端可直接看图复用）。"
                       "file 形如 frames/f00.jpg（用 get_assets 查帧清单）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "任务 ID（digest_video 返回）"},
                "file": {"type": "string", "description": "run 目录内的帧图相对路径（如 frames/f00.jpg）"},
            },
            "required": ["job_id", "file"],
        },
    },
    {
        "name": "cancel",
        "description": "取消任务：排队中立即取消；running 中在阶段边界（转写/总结之间）协作生效。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "任务 ID（digest_video 返回）"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_manual",
        "description": "取完整技术手册（参数全表/type 与 visual 语义/9 工具清单/两种姿势/"
                       "内部机制/费用明细）。仅在用户明确要技术细节、参数或手册时调用并转述；"
                       "日常使用按 INSTRUCTIONS 场景剧本即可，不需要本手册。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "digest_doctor",
        "description": "体检：环境与凭据是否就绪（首次使用/疑似凭据问题时调用；"
                       "对已配置 key 做真实 ping，约 ¥0.001/项）。返回各能力（读图/总结/云转写/"
                       "B站/小红书等）的状态与「下一步动作」——照着指引用户配置即可"
                       "（默认只需注册硅基流动一把 key：https://cloud.siliconflow.cn → API 密钥，"
                       "填 ~/.config/ai-tools/bili-digest/keys.env；小红书 Cookie 装仓库内置 "
                       "cookie-exporter 插件一键导出（github.com/bappleug/video-summarizer）。"
                       "key 不经对话传递）。复检通过再执行任务。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


# ---------------------------------------------------------------- MCP 协议层（延迟 import SDK）

def _json_safe(value):
    """递归把 NaN/Infinity 换成 None（裸 NaN 会让客户端 JSON.parse 直接炸）。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def build_server(manager: JobManager | None = None):
    """组装低层 MCP Server：5 工具 + 空 resources/prompts（兼容会无条件拉清单的严格客户端）。"""
    import mcp.types as types
    from mcp.server import Server

    manager = manager or JobManager()

    async def on_list_tools(ctx, params):
        tools = [types.Tool(name=t["name"], description=t["description"],
                            input_schema=t["inputSchema"]) for t in TOOLS]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params):
        try:
            payload = tool_call(manager, params.name, params.arguments)
        except ToolInputError as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True)
        if isinstance(payload, str):
            return types.CallToolResult(content=[types.TextContent(type="text", text=payload)])
        safe = _json_safe(payload)
        return types.CallToolResult(
            content=[types.TextContent(type="text",
                                       text=json.dumps(safe, ensure_ascii=False, allow_nan=False))],
            structured_content=safe)

    async def on_list_resources(ctx, params):
        return types.ListResourcesResult(resources=[])

    async def on_list_prompts(ctx, params):
        return types.ListPromptsResult(prompts=[])

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_list_prompts=on_list_prompts,
    )


def main() -> int:
    """stdio 模式入口（digest mcp）。stdout 只有协议帧，日志走 stderr。"""
    import logging
    import sys

    import anyio
    from mcp.server.stdio import stdio_server

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")  # SDK/anyio 告警只进 stderr

    async def _serve():
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            # 服务期间 fd0→/dev/null、fd1→stderr（SDK 接管），协议帧走私有 fd 副本
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        anyio.run(_serve)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
