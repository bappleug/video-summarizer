"""digest doctor：环境与 key 体检（D-009）。

输出按能力分组（S7.2：能力语言在上、厂商 key 在下）的「项目 × 状态 × 下一步动作」表格（stdout）；单项失败不中断，最后汇总；
有红项（fail）退出码 1。key 实测 ping 均为零成本接口（2026-11-20 实测口径）：
- 硅基流动 GET /v1/models（原 /v1/user/info 已下线返回 410，models 只读零成本）
- 智谱一条极短 chat（glm-5.3-flash；原定免费的 glm-4-flash 对 Coding Plan key 报「套餐到期」，不可用）
- DeepSeek GET /user/balance（查余额）
B站登录态只读本地凭据文件判断（不打 API，不消耗风控预算）。
"""

import json
import time
import urllib.error
import urllib.request

from . import config
from .errors import CliError
from .platforms import bilibili
from .transcribe import MlxTranscriber

OK, WARN, FAIL = "ok", "warn", "fail"

SF_PING_URL = "https://api.siliconflow.cn/v1/models"
SF_CONSOLE = "https://cloud.siliconflow.cn"
ZHIPU_CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_CONSOLE = "https://bigmodel.cn"
DS_BALANCE_URL = "https://api.deepseek.com/user/balance"
DS_CONSOLE = "https://platform.deepseek.com"

# S7.2 能力分组：能力语言在上、厂商 key 在下（检查仍是对 key/依赖的真实探测）
CAP_SUMMARY_PROVIDER_ENV = "SUMMARIZE_PROVIDER"


def http_json(method, url, key=None, payload=None, timeout=30):
    """doctor 专用轻量 JSON 请求（供测试 mock），返回 (status, dict 或文本)。"""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
        raise CliError(f"网络不可用或超时: {err}", 4)


def ping_siliconflow(key):
    status, body = http_json("GET", SF_PING_URL, key=key)
    if status == 200:
        models = (body.get("data") or []) if isinstance(body, dict) else []
        return OK, f"有效（{len(models)} 个模型可见）" if models else "有效"
    if status == 401:
        return FAIL, "key 无效（或端点错：中国站密钥须配 .cn 端点）"
    if status == 402:
        return FAIL, "余额不足"
    return FAIL, f"异常（HTTP {status}）"


def ping_zhipu(key):
    payload = {
        "model": "glm-5.3-flash",  # 极短调用成本 <¥0.001（glm-4-flash 对 Coding Plan key 报套餐到期）
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
    }
    status, body = http_json("POST", ZHIPU_CHAT_URL, key=key, payload=payload)
    if status == 200:
        return OK, "有效（glm-5.3-flash 连通）"
    if status == 401:
        return FAIL, "key 无效"
    if status == 402:
        return FAIL, "余额不足（欠费）"
    if status == 429:
        detail = _error_message(body)
        return FAIL, f"被限流：{detail}" if detail else "被限流"
    return FAIL, f"异常（HTTP {status}）"


def _error_message(body):
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or "")[:120]
        return str(body.get("message") or "")[:120]
    return ""


def ping_deepseek(key):
    status, body = http_json("GET", DS_BALANCE_URL, key=key)
    if status == 200:
        infos = body.get("balance_infos") or []
        cents = next((i.get("total_balance") for i in infos if i.get("currency") == "CNY"), None)
        return OK, f"有效（余额 {cents} 元）" if cents is not None else "有效"
    if status == 401:
        return FAIL, "key 无效"
    if status == 402:
        return FAIL, "余额不足"
    return FAIL, f"异常（HTTP {status}）"


PINGERS = {
    "SILICONFLOW_API_KEY": ping_siliconflow,
    "ZHIPU_API_KEY": ping_zhipu,
    "DEEPSEEK_API_KEY": ping_deepseek,
}


def check_key(name, purpose, console):
    key = config.get_key(name)
    if not key:
        return WARN, "未配置", f"按需申请：{console} → API 密钥，写入 ~/.config/ai-tools/bili-digest/keys.env"
    try:
        state, detail = PINGERS[name](key)
    except CliError as err:
        return FAIL, f"实测失败：{err}", "检查网络或代理后重跑 digest doctor"
    if state == OK:
        return OK, detail, "无需处理"
    hint = {
        "key 无效": f"到 {console} 重新生成 key 并更新 keys.env",
        "余额不足": f"到 {console} 充值（或换免费档模型）",
    }
    action = next((v for k, v in hint.items() if k in detail), f"到 {console} 排查账号状态")
    return FAIL, detail, action


def check_local():
    rows = []
    mlx = MlxTranscriber()
    if mlx.available():
        rows.append(("本地转写", "mlx-whisper", OK, "可用（auto 默认走本地）", "无需处理"))
    else:
        rows.append(("本地转写", "mlx-whisper", WARN,
                     "不可用（需 Apple Silicon + bili-digest[mlx]）",
                     "可装 mlx extra，或用 --engine cloud（免费 SenseVoice）"))
    import shutil
    if shutil.which("ffmpeg"):
        rows.append(("基础依赖", "ffmpeg", OK, "已安装", "无需处理"))
    else:
        rows.append(("基础依赖", "ffmpeg", FAIL, "未安装（ASR 音频前置必需）", "brew install ffmpeg"))
    rows.append(check_bili_login())
    rows.append(check_xhs_cookie())
    return rows


def check_bili_login():
    try:
        cred = bilibili.load_cred()
    except CliError as err:
        return ("B站登录态", "凭据文件", FAIL, str(err), "重新执行 digest login")
    if not cred:
        return ("B站登录态", "凭据文件", WARN, "未登录（公开字幕探测受限，ASR 路径不受影响）",
                "执行 digest login 扫码登录")
    age = (int(time.time()) - int(cred.get("login_at", 0))) // 86400
    mid = cred.get("cookies", {}).get("DedeUserID")
    renew = "自动续期可用" if cred.get("refresh_token") else "缺 refresh_token（过期需重新登录）"
    state = OK if cred.get("refresh_token") else WARN
    return ("B站登录态", "凭据文件", state, f"已登录（mid {mid}，{age} 天，{renew}）",
            "无需处理" if cred.get("refresh_token") else "失效前重新 digest login")


def check_xhs_cookie():
    """小红书 Cookie（S7.2 入 doctor）：只查配置存在性，不打 API。"""
    if config.get_key("XHS_COOKIE"):
        return ("小红书 Cookie", "XHS_COOKIE", OK, "已配置（匿名优先，被拒才带 cookie 重试）",
                "无需处理")
    return ("小红书 Cookie", "XHS_COOKIE", WARN,
            "未配置（匿名可用；笔记 300031 多为 xsec_token/cookie 过期）",
            "用浏览器插件（如 EditThisCookie）导出小红书站点 Cookie 整行，写入 "
            "~/.config/ai-tools/bili-digest/keys.env 的 XHS_COOKIE=（不经对话传递）")


def _vision_model_label():
    try:
        from .enrich.vlm import vision_conf
        return vision_conf()["model"]
    except CliError:
        return "(VISION_PROVIDER 配置异常)"


def run():
    """体检 → [(capability, item, state, detail, action)]（S7.2 能力分组；
    S7.3 供应商收敛：读图/总结/云转写默认全走硅基流动——一把 key 全搞定）。"""
    rows = []
    sf_state, sf_detail, sf_action = check_key(
        "SILICONFLOW_API_KEY", "硅基流动 key", SF_CONSOLE)
    rows.append(("读图能力", f"硅基流动 key（读图模型 {_vision_model_label()}，"
                              "可用 VISION_MODEL 覆盖）",
                 sf_state, sf_detail, sf_action))
    rows.append(("总结能力", "硅基流动 key（读图+总结+云转写一把 key 全搞定；"
                              "备用需手动指定：SUMMARIZE_PROVIDER/--provider 切换）",
                 sf_state, sf_detail, sf_action))
    rows.append(("云端转写", "硅基流动 key（免费注册；仅 --engine cloud / 非 Apple Silicon 机器需要）",
                 sf_state, sf_detail, sf_action))
    # 备用 key：只在已配置时显示（存在即备用，不催注册、不 ping）
    for key_name, label in (("ZHIPU_API_KEY", "智谱 key"), ("DEEPSEEK_API_KEY", "DeepSeek key")):
        if config.get_key(key_name):
            rows.append(("可选备用", f"{label}（已配置；手动备用：--provider / SUMMARIZE_PROVIDER / VISION_PROVIDER 指定切换）",
                         OK, "已配置", "无需处理（可选备用）"))
    rows.extend(check_local())
    return rows


def render_table(rows):
    """按能力分组渲染（S7.2）：[能力] 标题行 + 组内 item × 状态 × 详情 × 动作表格。"""
    marks = {OK: "✔", WARN: "△", FAIL: "✘"}
    names = {"ok": "正常", "warn": "可优化", "fail": "异常"}
    lines = []
    for cap in dict.fromkeys(r[0] for r in rows):  # 保序去重
        lines.append(f"【{cap}】")
        sub = [r[1:] for r in rows if r[0] == cap]
        widths = [max(len(str(r[i])) for r in sub) for i in range(4)]
        header = ["项目", "状态", "详情", "下一步动作"]
        lines.append(" | ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
        lines.append("-|-".join("-" * w for w in widths))
        for name, state, status_text, action in sub:
            lines.append(" | ".join([
                marks[state] + " " + str(name).ljust(widths[0] - 2),
                names[state].ljust(widths[1]),
                str(status_text).ljust(widths[2]),
                str(action),
            ]))
    return "\n".join(lines)


def main():
    rows = run()
    print(render_table(rows))
    fails = [r for r in rows if r[2] == FAIL]
    print()
    if fails:
        print(f"体检结论：{len(fails)} 项异常，按「下一步动作」修复后重跑 digest doctor")
        return 1
    warns = [r for r in rows if r[1] == WARN]
    print(f"体检结论：无异常（{len(warns)} 项可按需优化）")
    return 0
