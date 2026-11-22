"""硅基流动 SenseVoice 云转写后端（stdlib urllib，multipart 自拼）。

实测口径（docs/research/04 §6.1，2026-11-20）：
- 端点必须 https://api.siliconflow.cn/v1/audio/transcriptions（中国站 key 对 .com 401）；
  默认 .cn，401 时自动重试一次 .com（兼容国际站 key）。
- 模型 FunAudioLLM/SenseVoiceSmall，免费；wav / m4a / mp3 均可用。
- 响应 {text, language, usage:{type:"duration", seconds}}——只回整段文本，无分段时间戳。
- 文档标称单文件 ≤50MB（实测 127MB 未拦截，但不依赖该行为）：超 50MB 先 ffmpeg 压 mp3。
- S2 支持到 1h 内音频；language 回显不可靠，不作语种判断依据。
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .. import config
from ..errors import CliError, log
from .base import Segment, Transcript, suspects_silence

MODEL = "FunAudioLLM/SenseVoiceSmall"
ENDPOINTS = ("https://api.siliconflow.cn", "https://api.siliconflow.com")
CONSOLE_URL = "https://cloud.siliconflow.cn"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB，超限先压 mp3
TIMEOUT_S = 300


def error_hint(status, endpoint):
    """D-009 错误分类：每个状态码给「发生了什么 + 去哪修」。"""
    if status == 401:
        return (f"key 无效或端点错（{endpoint}）：到 {CONSOLE_URL} →「API 密钥」核对；"
                "中国站 key 须用 .cn 端点。运行 digest doctor 查看诊断")
    if status == 402:
        return f"硅基流动余额不足：到 {CONSOLE_URL} 充值后重试"
    if status == 403:
        return f"无权限（可能未实名认证或未开通该模型）：到 {CONSOLE_URL} 完成实名认证"
    if status == 429:
        return "请求过于频繁被限流，稍等片刻重试"
    return None


def build_multipart(fields, filename, content, content_type):
    """stdlib 手拼 multipart/form-data（不引第三方依赖）。"""
    boundary = "----bili-digest-" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             f"Content-Type: {content_type}\r\n\r\n").encode()
    body += content
    body += f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def shrink_to_mp3(wav: Path, work_dir: Path) -> Path:
    """超过上传上限的 wav 先压成 64k mono mp3（16k mono wav ≈ 32KB/s，1h ≈ 115MB → ~28MB）。"""
    mp3 = work_dir / (wav.stem + ".mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
         "-ac", "1", "-b:a", "64k", str(mp3)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not mp3.exists() or mp3.stat().st_size <= 1024:
        detail = result.stderr.decode("utf-8", "ignore").strip()[-300:]
        raise CliError(f"压 mp3 失败（ffmpeg）: {detail}", 1)
    return mp3


def post_transcription(endpoint, key, path: Path):
    """POST /v1/audio/transcriptions，返回 (http_status, 响应 dict 或原始文本)。"""
    payload = path.read_bytes()
    fields = {"model": MODEL}
    content_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
    body, ctype = build_multipart(fields, path.name, payload, content_type)
    req = urllib.request.Request(
        endpoint + "/v1/audio/transcriptions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": ctype},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "ignore")
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw
    except (urllib.error.URLError, TimeoutError) as err:
        raise CliError(f"云转写网络不可用或超时: {err}", 4)


class CloudSfTranscriber:
    name = "siliconflow-sensevoice"

    def available(self) -> bool:
        return bool(config.get_key("SILICONFLOW_API_KEY"))

    def transcribe(self, wav: Path, lang=None) -> Transcript:
        key = config.get_key("SILICONFLOW_API_KEY")
        if not key:
            raise CliError("云引擎需配置 SILICONFLOW_API_KEY（~/.config/ai-tools/bili-digest/keys.env "
                           "或环境变量），运行 digest doctor 查看指引", 3)

        wav = Path(wav)
        upload = wav
        if wav.stat().st_size > MAX_UPLOAD_BYTES:
            log(f"音频 {wav.stat().st_size / 1e6:.0f}MB 超上传上限，先压 mp3")
            upload = shrink_to_mp3(wav, wav.parent)

        t0 = time.time()
        status, body = post_transcription(ENDPOINTS[0], key, upload)
        if status == 401:  # 中国站 key 之外的账号可能属于国际站，自动换 .com 重试一次
            log(f"{ENDPOINTS[0]} 返回 401，自动换 {ENDPOINTS[1]} 重试")
            status, body = post_transcription(ENDPOINTS[1], key, upload)

        if status != 200:
            hint = error_hint(status, ENDPOINTS[0])
            if hint is None:
                detail = body.get("message") if isinstance(body, dict) else str(body)
                hint = f"云转写失败（HTTP {status}）: {detail}"
            raise CliError(hint, 3 if status in (401, 402, 403) else 4)

        text = str(body.get("text") or "").strip()
        seconds = float(((body.get("usage") or {}).get("seconds")) or 0)
        if not text:
            raise CliError("云转写返回空文本", 3)
        log(f"云端计费时长 {seconds:.0f}s")
        segments = [Segment(start=0.0, end=seconds, text=text)]
        return Transcript(
            segments=segments, lang=lang or "auto", engine=self.name, source="asr",
            elapsed_s=time.time() - t0, suspected_silent=suspects_silence(segments),
        )
