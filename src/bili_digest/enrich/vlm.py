"""智谱 GLM 读图（OpenAI 兼容 content 数组 + base64 image_url，stdlib urllib）。

S6.1 增画面价值门控探测 probe_content_type：单次调用带 ≤3 帧问「内容演示型 / 人物口播型」，
口播型由 pipeline 停止读图（~¥0.002/次）；探测失败返回 None 按内容型继续（不误伤）。

模型名与价格查证（2026-11-21，docs.bigmodel.cn 官方模型/价格页 + 真实 API 实测）：
- glm-5.3-flash：原生多模态（图像输入实测 200 通过），¥0.8/M 入 + ¥2.8/M 出——
  （S7.3 起默认供应商 siliconflow/Qwen3-VL-8B，约 ¥0.003/条视频；本段为历史查证记录）；
- glm-4.6v-flash / glm-4.1v-thinking-flash：官方标免费，但实测本项目的
  GLM Coding Plan key 请求被拒（HTTP 429 + code 1309「套餐已到期」）——免费档
  挂在套餐额度体系下，Coding Plan key 不可用；可用环境变量 BILI_DIGEST_VLM_MODEL
  换任意 VLM（如 glm-4.6v-flash，个人普通 key 可用且免费）；
- glm-4.6v（付费档）：<32K 输入 ¥1/M、输出 ¥3/M（32K-128K 档 ¥2/¥6）。
调用要点（官方 GLM-4.6V-Flash 文档）：image_url.url 直接放 base64 字符串，无需
data URI 前缀；thinking {"type": "disabled"} 关闭思考（帧描述任务不需要，实测 5.3-flash 接受）。
错误分类文案与 doctor / summarize.llm 体系一致（401/402/403/429/网络 → 带修法指引）。
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request

from .. import config
from ..errors import CliError, log

# ---------------------------------------------------------------- 读图供应商（S7.3 收敛）
# VISION_PROVIDER（keys.env/环境变量，默认 siliconflow）+ VISION_MODEL 覆盖（S7.2 保留）。
# 模型覆盖优先级：环境变量 BILI_DIGEST_VLM_MODEL > keys.env VISION_MODEL > 供应商默认。
# 价率未查证的供应商 price 为 None → 成本按 0 估算（tokens 照记），不编造数字。
VISION_PROVIDERS = {
    "siliconflow": {  # 默认（用户实测可用，2026-11-22）：一把 key 同时供读图/总结/云转写
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-VL-8B-Instruct",
        "key_env": "SILICONFLOW_API_KEY",
        "price_in": None, "price_out": None,
        "console": "https://cloud.siliconflow.cn",
    },
    "zhipu": {  # 备用（查证记录见模块 docstring）
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.3-flash",
        "key_env": "ZHIPU_API_KEY",
        "price_in": 0.8, "price_out": 2.8,  # 元/百万 tokens，查证 2026-11-20
        "console": "https://bigmodel.cn",
    },
    "deepseek": {  # 备用：官方端点暂无公开 VL 模型，选它必须配 VISION_MODEL
        "base_url": "https://api.deepseek.com",
        "model": None,
        "key_env": "DEEPSEEK_API_KEY",
        "price_in": None, "price_out": None,
        "console": "https://platform.deepseek.com",
    },
}
TIMEOUT = 120
RETRY_429_WAIT = 15.0  # 限流退避重试一次


def vision_provider() -> str:
    """当前读图供应商名：VISION_PROVIDER（默认 siliconflow）；非法值报用法错误。"""
    name = (config.get_key("VISION_PROVIDER") or "siliconflow").strip().lower()
    if name not in VISION_PROVIDERS:
        raise CliError(f"VISION_PROVIDER 非法: {name!r}（支持 {'|'.join(VISION_PROVIDERS)}）", 2)
    return name


def vision_conf_for(name):
    """按供应商名解析读图配置（供兜底链；deepseek 无默认模型时抛 CliError）。"""
    if name not in VISION_PROVIDERS:
        raise CliError(f"未知读图供应商: {name!r}", 2)
    conf = dict(VISION_PROVIDERS[name])
    model = (os.environ.get("BILI_DIGEST_VLM_MODEL")
             or config.get_key("VISION_MODEL") or conf["model"])
    if not model:
        raise CliError(f"VISION_PROVIDER={name} 需配 VISION_MODEL", 2)
    conf.update(provider=name, model=model)
    return conf


def vision_conf() -> dict:
    """当前读图配置（provider/model/base_url/key_env/price_*）。
    deepseek 无默认 VL 模型 → 必须配 VISION_MODEL，否则用法错误（不猜模型名）。"""
    name = vision_provider()
    conf = dict(VISION_PROVIDERS[name])
    model = (os.environ.get("BILI_DIGEST_VLM_MODEL")
             or config.get_key("VISION_MODEL") or conf["model"])
    if not model:
        raise CliError("VISION_PROVIDER=deepseek 需配 VISION_MODEL（官方端点暂无默认读图模型）", 2)
    conf.update(provider=name, model=model)
    return conf


def build_prompt(t, title=None, owner=None) -> str:
    """单帧读图 prompt：带视频标题/作者上下文，重点转录画面可见文字 + 帧类型分类
    （S6.4：frame_type 与描述同一次调用，零额外成本）。"""
    ctx = f"《{title}》" if title else ""
    if owner:
        ctx += f"（作者：{owner}）"
    return (
        f"你在看视频{ctx}的一帧关键画面，时间点 {t:.0f} 秒处。"
        "请仔细识别这一帧画面：完整转录画面上所有可见文字（标题、要点列表、标注、表格、公式），"
        "并简要描述图表/图像信息（如有）。只输出一个 JSON 对象，不要输出其他内容：\n"
        '{"t": <时间秒数>, "desc": "<画面内容描述：可见文字逐条转录+图表描述，300字内>", '
        '"has_text": <画面含明显文字为 true，否则 false>, '
        '"frame_type": "<帧类型，四选一：stage_talking=舞台人物口播或 webinar 人像为主的画面；'
        'desktop_content=桌面分享/代码编辑器/网页/文档等屏幕内容；diagram=幻灯片/图表/白板/图示；'
        'other=以上都不是>"}\n'
        "注意：文字要按画面原样转录，看不清的部分用「?」占位，不要猜测编造。"
    )


def estimate_cost(prompt_tokens, completion_tokens, conf=None) -> float:
    """按当前读图供应商费率估算（未查证价率的供应商按 0 估，不编造）。"""
    conf = conf or vision_conf()
    p_in, p_out = conf["price_in"] or 0.0, conf["price_out"] or 0.0
    return round(prompt_tokens / 1e6 * p_in + completion_tokens / 1e6 * p_out, 6)


def error_hint(status, body=None, conf=None) -> str | None:
    """状态码 + 业务错误码 → 修法指引（doctor 体系文案风格，不裸抛错误码）。"""
    conf = conf or vision_conf()
    console = conf["console"]
    code = ""
    if isinstance(body, dict):
        code = str((body.get("error") or {}).get("code") or "")
    if code == "1309" and conf["key_env"] == "ZHIPU_API_KEY":
        # 实测 2026-11-21：GLM Coding Plan key 请求免费档 VLM 被拒
        return ("GLM Coding Plan 套餐已到期/不适用该模型：续订（https://bigmodel.cn/claude-code）"
                "或换普通 API key，或设 VISION_MODEL 换默认读图模型")
    if status == 401:
        return (f"key 无效或端点错：到 {console} 核对 API 密钥（{conf['key_env']}）。"
                "运行 digest doctor 查看诊断")
    if status == 402:
        return f"余额不足：到 {console} 充值后重试"
    if status == 403:
        return f"无权限（可能未实名认证或模型未开通）：到 {console} 检查账号状态"
    if status == 429:
        return "请求过于频繁被限流，稍等片刻重试"
    return None


def post_chat(payload, timeout=TIMEOUT, conf=None):
    """POST /chat/completions（默认当前读图供应商，可传 conf 指定），返回
    (http_status, 响应 dict 或错误文本)。key 绝不打印。"""
    conf = conf or vision_conf()
    key = config.get_key(conf["key_env"])
    if not key:
        raise CliError(f"画面识别需配置 {conf['key_env']}（~/.config/ai-tools/bili-digest/keys.env "
                       "或环境变量），运行 digest doctor 查看指引", 3)
    req = urllib.request.Request(
        conf["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "ignore")
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw
    except (urllib.error.URLError, TimeoutError) as err:
        raise CliError(f"VLM 网络不可用或超时: {err}", 4)


FRAME_TYPES = ("stage_talking", "desktop_content", "diagram", "other")


def _parse_desc(content, t) -> dict:
    """模型输出 → {t, desc, has_text, frame_type}；非法 JSON 时把原文当 desc
    （has_text 保守 True）；frame_type 非法/缺失归 other（宁弃不猜，不误伤内容帧）。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
        desc = str(data.get("desc") or "").strip()
        if desc:
            ftype = str(data.get("frame_type") or "other").strip().lower()
            return {"t": t, "desc": desc, "has_text": bool(data.get("has_text", True)),
                    "frame_type": ftype if ftype in FRAME_TYPES else "other"}
    except (ValueError, AttributeError):
        pass
    if text:
        return {"t": t, "desc": text[:500], "has_text": True, "frame_type": "other"}
    raise CliError(f"VLM 返回空内容（t={t:.0f}s）", 4)


def describe_frame(frame, title=None, owner=None) -> tuple[dict, dict]:
    """单帧 → (描述 dict, 单次 usage)。frame 需带 t 与 path（frames.KeyFrame）。"""
    conf = vision_conf()  # S7.5：单供应商（撤销自动兜底，改宿主接管协议）；手动切换走 VISION_PROVIDER
    b64 = base64.b64encode(frame.path.read_bytes()).decode("ascii")
    payload = {
        "model": conf["model"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": b64}},
                {"type": "text", "text": build_prompt(frame.t, title, owner)},
            ],
        }],
        "temperature": 0.1,
        "stream": False,
        **({"thinking": {"type": "disabled"}} if conf["key_env"] == "ZHIPU_API_KEY" else {}),
    }
    for attempt in (1, 2):  # 真限流（429 且非 1309 套餐问题）退避重试一次（单供应商内保留）
        status, body = post_chat(payload, conf=conf)
        code = str((body.get("error") or {}).get("code") or "") \
            if isinstance(body, dict) else ""
        if status == 429 and code != "1309" and attempt == 1:
            log(f"VLM 限流，{RETRY_429_WAIT:.0f}s 后重试（t={frame.t:.0f}s）")
            time.sleep(RETRY_429_WAIT)
            continue
        break
    if status != 200:
        hint = error_hint(status, body, conf)
        if hint is None:
            detail = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)
            hint = f"VLM 调用失败（HTTP {status}）: {detail}"
        raise CliError(hint, 3 if status in (401, 402, 403) else 4)
    try:
        content = body["choices"][0]["message"]["content"]
        raw_usage = body.get("usage") or {}
    except (KeyError, IndexError, TypeError):
        raise CliError(f"VLM 响应结构异常: {str(body)[:200]}", 4) from None
    usage = {
        "prompt_tokens": int(raw_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(raw_usage.get("completion_tokens") or 0),
        "provider": conf["provider"], "model": conf["model"],
    }
    return _parse_desc(content, frame.t), usage


def describe_frames(frames, title=None, owner=None, pause=0.5) -> tuple[list[dict], dict]:
    """逐帧读图 → (与 frames 对齐的描述列表, 汇总 usage)。usage 记入 meta.json 的 vision 字段。"""
    t0 = time.time()
    descs, prompt_total, completion_total = [], 0, 0
    last = None
    for i, frame in enumerate(frames):
        desc, usage = describe_frame(frame, title=title, owner=owner)
        last = usage
        descs.append(desc)
        prompt_total += usage["prompt_tokens"]
        completion_total += usage["completion_tokens"]
        log(f"画面识别 {i + 1}/{len(frames)} · t={frame.t:.0f}s · "
            f"{'含文字' if desc['has_text'] else '无文字'}")
        if i < len(frames) - 1 and pause > 0:
            time.sleep(pause)  # 轻微限速，免费档更稳
    total_usage = {
        "provider": (last or {}).get("provider", vision_conf()["provider"]),
        "model": (last or {}).get("model", vision_conf()["model"]),
        "frames": len(frames),
        "prompt_tokens": prompt_total,
        "completion_tokens": completion_total,
        "total_tokens": prompt_total + completion_total,
        "cost_yuan": estimate_cost(prompt_total, completion_total),
        "elapsed_s": round(time.time() - t0, 2),
    }
    return descs, total_usage


# ---------------------------------------------------------------- 画面价值门控探测（S6.1）

def build_probe_prompt(title=None) -> str:
    """门控探测 prompt：2-3 帧判定内容类型（多样性正常但内容存疑时触发，auto 模式）。"""
    ctx = f"《{title}》" if title else ""
    return (
        f"你在看视频{ctx}的几个关键帧。请判断画面内容的主要类型：\n"
        "A. 内容演示型——幻灯片/文字/图表/表格/操作演示等以传递内容为主的画面\n"
        "B. 人物口播型——人脸/人物说话为主（访谈、vlog 口播、出镜讲解），画面信息量低\n"
        "只输出一个 JSON 对象，不要输出其他内容：\n"
        '{"type": "demo" 或 "talking_head"}'
    )


def probe_content_type(frames, title=None, owner=None) -> tuple[str | None, dict]:
    """门控探测（单次调用，≤3 帧同图组）：→ ("demo"|"talking_head"|None, usage)。
    None = 判定不明确（调用方按内容型继续读图，不因探测失败误伤）。"""
    b64s = [base64.b64encode(f.path.read_bytes()).decode("ascii") for f in frames]
    content = [{"type": "image_url", "image_url": {"url": b}} for b in b64s]
    content.append({"type": "text", "text": build_probe_prompt(title)})
    conf = vision_conf()
    status, body = post_chat({
        "model": conf["model"],
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "stream": False,
        **({"thinking": {"type": "disabled"}} if conf["key_env"] == "ZHIPU_API_KEY" else {}),
    })
    if status != 200:  # 探测失败不炸管线：返回 None，调用方按内容型继续
        log(f"画面价值探测失败（HTTP {status}），按内容型继续读图")
        return None, {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        text = body["choices"][0]["message"]["content"] or ""
        raw_usage = body.get("usage") or {}
    except (KeyError, IndexError, TypeError):
        return None, {"prompt_tokens": 0, "completion_tokens": 0}
    start, end = (text.find("{"), text.rfind("}") + 1)
    verdict = None
    if start >= 0 and end > start:
        try:
            value = str(json.loads(text[start:end]).get("type") or "").strip().lower()
        except ValueError:
            value = ""
        if value in ("demo", "talking_head"):
            verdict = value
        elif "口播" in value or "人物" in value:
            verdict = "talking_head"
        elif "演示" in value or "内容" in value:
            verdict = "demo"
    usage = {
        "prompt_tokens": int(raw_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(raw_usage.get("completion_tokens") or 0),
    }
    log(f"画面价值探测: {verdict or '判定不明确'}（{len(frames)} 帧，"
        f"¥{estimate_cost(usage['prompt_tokens'], usage['completion_tokens']):.4f}）")
    return verdict, usage

