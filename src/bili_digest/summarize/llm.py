"""OpenAI 兼容 chat client（stdlib urllib，零第三方依赖）。

provider 默认智谱 GLM-5.3-Flash、备 DeepSeek；key 来自环境变量或
~/.config/ai-tools/bili-digest/keys.env（config.get_key）。
返回 (正文文本, usage dict)；usage 含 tokens 与估算成本。

错误分类（D-009）：401/402/403/429/网络 全部抛 CliError 带修法指引，不裸抛状态码。
"""

import json
import time
import urllib.error
import urllib.request

from .. import config
from ..errors import CliError, log

RETRY_NETWORK_WAIT = 30.0  # S7：网络类错误（读超时/连接重置）退避一次再试

# 费率单位：元 / 百万 tokens。查证日期 2026-11-20（docs/research/04 §3 官方定价页）：
# - zhipu glm-5.3-flash：输入 0.8 / 输出 2.8（无时段浮动；Flash 档 reasoning tokens 计入输出）
# - deepseek deepseek-chat（deepseek-flash 的别名）：空闲档输入 1 / 输出 4；
#   高峰（工作日 9-12 / 14-18 点）翻倍为 2 / 8，这里按空闲档估算并注明
PROVIDERS = {
    "siliconflow": {  # S7.3 默认（用户实测时间戳纪律过验，2026-11-22）：一把 key 全搞定
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "key_env": "SILICONFLOW_API_KEY",
        "price_in": None, "price_out": None,  # 价率未查证 → 成本按 0 估算（不编造）
        "console": "https://cloud.siliconflow.cn",
        "topup": "https://cloud.siliconflow.cn",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.3-flash",
        "key_env": "ZHIPU_API_KEY",
        "price_in": 0.8,
        "price_out": 2.8,
        "console": "https://bigmodel.cn",
        "topup": "https://bigmodel.cn",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "price_in": 1.0,
        "price_out": 4.0,
        "console": "https://platform.deepseek.com",
        "topup": "https://platform.deepseek.com/top_up",
    },
}


def estimate_cost(provider, prompt_tokens, completion_tokens):
    """按费率常量估算成本（元）。DeepSeek 按空闲档，高峰约翻倍；
    价率未查证的供应商（S7.3 siliconflow）按 0 估算，不编造数字。"""
    prov = PROVIDERS[provider]
    cost = (prompt_tokens / 1e6 * (prov["price_in"] or 0.0)
            + completion_tokens / 1e6 * (prov["price_out"] or 0.0))
    return round(cost, 6)


def error_hint(status, provider):
    prov = PROVIDERS[provider]
    if status == 401:
        return (f"key 无效或端点错：到 {prov['console']} 核对 API 密钥"
                f"（{prov['key_env']}）。运行 digest doctor 查看诊断")
    if status == 402:
        return f"余额不足：到 {prov['topup']} 充值后重试"
    if status == 403:
        return f"无权限（可能未实名认证或模型未开通）：到 {prov['console']} 检查账号状态"
    if status == 429:
        return "请求过于频繁被限流，稍等片刻重试"
    return None


def post_chat(provider, messages, temperature=0.3, timeout=180):
    """POST /chat/completions，返回 (http_status, 响应 dict 或错误文本)。"""
    prov = PROVIDERS[provider]
    key = config.get_key(prov["key_env"])
    if not key:
        raise CliError(f"provider {provider} 需配置 {prov['key_env']}（~/.config/ai-tools/"
                       "bili-digest/keys.env 或环境变量），运行 digest doctor 查看指引", 3)
    payload = {
        "model": prov["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        prov["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    last_net_err = None
    for attempt in (1, 2):  # S7：网络类错误（读超时/连接重置）退避一次再试
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
            last_net_err = err
            if attempt == 1:
                log(f"LLM 网络错误（{err}），{RETRY_NETWORK_WAIT:.0f}s 后重试一次")
                time.sleep(RETRY_NETWORK_WAIT)
    raise CliError(f"LLM 网络不可用或超时: {last_net_err}", 4)


def chat(provider, messages, temperature=0.3, timeout=180):
    """一次 chat 调用（单供应商；S7.5 撤销自动兜底，改为宿主接管协议——失败落
    report_error 由调用方引导宿主 get_transcript 自行总结或充值后补跑），
    返回 (正文, usage)。usage 含 tokens、估算成本与模型信息。
    供应商内的网络类退避重试一次保留（post_chat 内实现）；
    手动切换供应商走 --provider / SUMMARIZE_PROVIDER。"""
    if provider not in PROVIDERS:
        raise CliError(f"未知 provider: {provider}（支持 {'|'.join(PROVIDERS)}）", 2)
    t0 = time.time()
    status, body = post_chat(provider, messages, temperature=temperature, timeout=timeout)
    if status != 200:
        hint = error_hint(status, provider)
        if hint is None:
            detail = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)
            hint = f"LLM 调用失败（HTTP {status}）: {detail}"
        raise CliError(hint, 3 if status in (401, 402, 403) else 4)
    try:
        content = body["choices"][0]["message"]["content"]
        raw_usage = body.get("usage") or {}
    except (KeyError, IndexError, TypeError):
        raise CliError(f"LLM 响应结构异常: {str(body)[:200]}", 4)
    prompt_tokens = int(raw_usage.get("prompt_tokens") or 0)
    completion_tokens = int(raw_usage.get("completion_tokens") or 0)
    usage = {
        "provider": provider,
        "model": body.get("model") or PROVIDERS[provider]["model"],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_yuan": estimate_cost(provider, prompt_tokens, completion_tokens),
        "elapsed_s": round(time.time() - t0, 2),
    }
    return content, usage
