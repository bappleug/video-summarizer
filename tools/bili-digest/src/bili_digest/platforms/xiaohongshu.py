"""小红书平台模块：视频笔记取材（网页版 __INITIAL_STATE__ 解析，无官方 API）。

抽自 vendors/video-report-agent/src/video_report_agent/xiaohongshu.py（MIT，commit af73abd）。
VRA 原实现基于 yt-dlp（monkey-patch XiaoHongShuIE）+ httpx + pysubs2；本项目零第三方依赖，
改为 stdlib 等价实现，数据路径与登录策略保持一致：
- 匿名优先；HTTP 401/403/461 或跳转登录页 → 带 XHS_COOKIE 重试一次（config.get_key）
- 分享链接保留 xsec_token / xsec_source；xhslink 短链逐跳安全重定向（最多 5 跳）
- 内嵌字幕取自 video.mediaV2.video.subtitles（仅收 .srt 直链）
- 视频流取自 video.media.stream.<codec>[].masterUrl / backupUrls（h264 优先，含备选）
改造点：yt-dlp js_to_json → _js_to_json；pysubs2 → _parse_srt；httpx → urllib；
不使用 originVideoKey 原片直链（省一次探测请求，masterUrl 够用）。
"""

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import audio, config
from ..errors import CliError, log

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
WEB = "https://www.xiaohongshu.com"

# xhslink.cn 是短链主域，.com 兼容（与 VRA _HOSTS 一致）
XHS_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com",
             "xhslink.cn", "www.xhslink.cn"}

# ---------------------------------------------------------------------------
# 链接解析（VRA validate_xiaohongshu_url）

_LINK_RE = re.compile(r'https?://[^\s<>"）)]+')
_NOTE_PATH_RE = re.compile(r"/(?:explore|discovery/item)/([a-f0-9]{24})/?")
_SHORT_PATH_RE = re.compile(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+/?")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _short_probe(url):
    """短链探测：不跟随重定向，返回 (status, location)。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _short_probe.opener.open(req, timeout=15) as resp:
            return resp.status, None
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location", "")


_short_probe.opener = urllib.request.build_opener(_NoRedirect)


def resolve_note_url(value):
    """分享文字/链接 → (note_id, 规范链接)。长链保留 xsec_token/xsec_source，短链最多 5 跳。"""
    links = _LINK_RE.findall(value.strip())
    if len(links) != 1:
        raise CliError("请粘贴小红书完整分享链接或分享文字", 2)
    link = links[0]
    for _ in range(5):
        try:
            parsed = urllib.parse.urlsplit(link)
            host = parsed.hostname
            valid = parsed.scheme in {"http", "https"} and host in XHS_HOSTS \
                and parsed.netloc == host
        except ValueError:
            valid = False
        if not valid:
            raise CliError("请输入小红书视频笔记分享链接", 2)
        link = parsed._replace(scheme="https").geturl()
        if host in ("xiaohongshu.com", "www.xiaohongshu.com"):
            match = _NOTE_PATH_RE.fullmatch(parsed.path)
            if not match:
                break
            query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
                     if k in ("xsec_token", "xsec_source")]
            canonical = WEB + "/explore/" + match.group(1)
            if query:
                canonical += "?" + urllib.parse.urlencode(query)
            return match.group(1), canonical
        if not _SHORT_PATH_RE.fullmatch(parsed.path):
            break
        try:
            status, location = _short_probe(link)
        except urllib.error.URLError as err:
            raise CliError(f"小红书短链接解析失败: {err.reason}", 4) from None
        if status not in (301, 302, 303, 307, 308):
            break
        link = urllib.parse.urljoin(link, location)
    raise CliError("未找到视频笔记，请重新复制完整分享链接", 2)


# ---------------------------------------------------------------------------
# HTTP（urllib 等价实现，匿名优先 / cookie 重试一次）

class _LoginRequired(Exception):
    """匿名被拒（401/403/461、登录页、安全拦截）。reason 记录原始形态，供错误信息透传。"""

    def __init__(self, reason=""):
        super().__init__(reason)
        self.reason = reason


def _http_get(url, cookie=None):
    """GET → (status, body, final_url)。跟随重定向；4xx 不抛异常由调用方判定。"""
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read(), resp.geturl()
    except urllib.error.HTTPError as err:
        final = getattr(err, "url", None) or getattr(err, "filename", None) or url
        return err.code, err.read(), final
    except urllib.error.URLError as err:
        raise CliError(f"网络不可用: {err.reason}", 4) from None


# ---------------------------------------------------------------------------
# __INITIAL_STATE__ 解析（yt-dlp js_to_json 的 stdlib 精简版）

_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v",
               "0": "\0", "\\": "\\", "'": "'", '"': '"', "`": "`", "\n": ""}


def _match_braces(text, start):
    """text[start] 须为 '{'；跳过字符串与转义做括号配对，返回收口 '}' 的索引。"""
    depth, i, n, quote = 0, start, len(text), None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise CliError("页面 __INITIAL_STATE__ 不完整（疑似被反爬截断），稍后重试", 4)


def _match_parens(text, start):
    """text[start] 须为 '('；跳过字符串与转义做括号配对，返回收口 ')' 的索引，失败 -1。"""
    depth, i, n, quote = 0, start, len(text), None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _scan_quoted(code, i):
    """从 code[i] 的引号起扫描到闭引号，返回 (解码后文本, 闭引号后索引)。"""
    quote, j, n, buf = code[i], i + 1, len(code), []
    while j < n:
        c = code[j]
        if c == "\\" and j + 1 < n:
            e = code[j + 1]
            try:
                if e == "u" and j + 5 < n:
                    buf.append(chr(int(code[j + 2:j + 6], 16)))
                    j += 6
                    continue
                if e == "x" and j + 3 < n:
                    buf.append(chr(int(code[j + 2:j + 4], 16)))
                    j += 4
                    continue
            except ValueError:
                raise CliError("页面 __INITIAL_STATE__ 字符串解析失败", 4) from None
            buf.append(_JS_ESCAPES.get(e, e))
            j += 2
            continue
        if c == quote:
            return "".join(buf), j + 1
        if c == "\n":
            break
        buf.append(c)
        j += 1
    raise CliError("页面 __INITIAL_STATE__ 字符串解析失败", 4)


def _convert_new(ctor, arg):
    """JS 构造调用 → JSON（yt-dlp js_to_json 语义）。实测页面出现 new Map([])（SSR 未脱糖）。"""
    arg = arg.strip()
    if ctor == "Map":
        if arg.startswith("[") and arg.endswith("]"):
            try:
                entries = json.loads(_js_to_json(arg))
                return json.dumps({str(e[0]): e[1] for e in entries}, ensure_ascii=False)
            except (ValueError, TypeError, IndexError):
                pass
        return "{}"
    if ctor == "Date" and arg[:1] in "\"'`":
        body, _ = _scan_quoted(arg, 0)
        return json.dumps(body, ensure_ascii=False)
    return json.dumps(f"new {ctor}({arg})", ensure_ascii=False)


def _js_to_json(code):
    """JS 对象字面量 → JSON 文本：undefined/NaN → null、单引号/反引号 → 双引号、
    未加引号的键 → 加引号、尾逗号与注释剔除。双引号字符串按 JSON 原样保留。"""
    out, i, n = [], 0, len(code)

    def skip_blank(k):
        while k < n:
            if code[k].isspace():
                k += 1
            elif code[k:k + 2] == "//":
                k = code.find("\n", k)
                k = n if k < 0 else k
            elif code[k:k + 2] == "/*":
                k = code.find("*/", k + 2)
                k = n if k < 0 else k + 2
            else:
                break
        return k

    while i < n:
        c = code[i]
        if c == '"':  # 双引号字符串假定 JSON 兼容，原样保留
            j = i + 1
            while j < n and code[j] != '"':
                j += 2 if code[j] == "\\" else 1
            out.append(code[i:j + 1])
            i = j + 1
        elif c in "'`":
            body, j = _scan_quoted(code, i)
            out.append(json.dumps(body, ensure_ascii=False))
            i = j
        elif code[i:i + 2] == "//":
            i = code.find("\n", i)
            i = n if i < 0 else i
        elif code[i:i + 2] == "/*":
            j = code.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == ",":
            k = skip_blank(i + 1)
            out.append("" if k < n and code[k] in "}]}" else c)  # 尾逗号剔除
            i = i + 1
        elif c.isalpha() or c in "_$":
            match = _IDENT_RE.match(code, i)
            word, k = match.group(0), match.end()
            after = skip_blank(k)
            if word == "new":
                # 构造调用：new Map(...) → 对象；new Date("…") → 字符串；其余整体字符串化
                ctor_match = _IDENT_RE.match(code, skip_blank(k))
                if ctor_match:
                    ctor = ctor_match.group(0)
                    open_at = skip_blank(ctor_match.end())
                    if open_at < n and code[open_at] == "(":
                        close = _match_parens(code, open_at)
                    else:
                        close = -1
                    if close > open_at:
                        out.append(_convert_new(ctor, code[open_at + 1:close]))
                        i = close + 1
                        continue
                out.append(word)
                i = k
            elif word in ("undefined", "NaN", "Infinity"):
                out.append("null")
            elif word in ("true", "false", "null"):
                out.append(word)
            elif after < n and code[after] == ":" and word not in ("in", "of"):
                out.append(json.dumps(word))  # 未加引号的键
            else:
                out.append(word)
            i = k
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_state(page):
    """HTML → __INITIAL_STATE__ dict。"""
    match = _STATE_RE.search(page)
    if not match:
        raise CliError("小红书页面缺少笔记数据（疑似验证页/反爬拦截），"
                       "稍后重试或在 keys.env 配置 XHS_COOKIE", 4)
    start = page.find("{", match.end())
    if start < 0:
        raise CliError("小红书页面 __INITIAL_STATE__ 为空", 4)
    end = _match_braces(page, start)
    try:
        return json.loads(_js_to_json(page[start:end + 1]))
    except ValueError:
        raise CliError("小红书页面 __INITIAL_STATE__ 解析失败（页面结构可能已变更）", 4) from None


# ---------------------------------------------------------------------------
# 笔记解析（VRA extract_xiaohongshu + yt-dlp XiaoHongShuIE 的 stream 路径）

def _fetch_note(note_id, url, cookie=None):
    """GET 分享页 → note dict（不判登录态，见 acquire_note）。"""
    status, raw, final_url = _http_get(url, cookie=cookie)
    if status in (401, 403, 461):
        raise _LoginRequired(f"HTTP {status}")
    final = urllib.parse.urlsplit(final_url)
    query = urllib.parse.parse_qs(final.query)
    message = " ".join(query.get("error_msg", []))
    error_code = " ".join(query.get("error_code", []))
    if final.path.startswith("/login") or "登录" in message:
        raise _LoginRequired("跳转登录页")
    if final.path.startswith("/404") and (message or error_code):
        # 实测形态：200 → /404?source=/404/sec_...&error_code=300031&error_msg=当前笔记暂时无法浏览
        # 页面仍带 __INITIAL_STATE__ 但 noteDetailMap 为空（安全拦截，登录态可能可解）
        raise _LoginRequired(f"error_code {error_code}: {message}".strip())
    state = parse_state(raw.decode("utf-8", "ignore"))
    note = ((state.get("note") or {}).get("noteDetailMap") or {}).get(note_id) or {}
    note = note.get("note") or {}
    if not note:
        raise CliError("笔记数据不存在：链接可能已失效，或缺少 xsec_token"
                       "（从 App 分享重新复制完整链接）", 3)
    if note.get("type") != "video" or not note.get("video"):
        raise CliError("这是图文笔记，只支持视频笔记", 2)
    return note


def _note_duration(video):
    """笔记时长（秒）。优先级：stream 条目 duration（毫秒，yt-dlp 测试集实证）→
    capa.duration（秒）→ media.video.duration（秒）。
    S4 真实笔记实测：media.video.duration=382 / capa=381 / stream=381400 —— VRA 原实现
    把 media.video.duration 当毫秒属误判，已在抽件改造中修正。"""
    stream = (video.get("media") or {}).get("stream") or {}
    durations = [e.get("duration") for lst in stream.values() for e in (lst or [])]
    durations = [d for d in durations
                 if isinstance(d, (int, float)) and not isinstance(d, bool)]
    if durations:
        return max(durations) / 1000
    for value in ((video.get("capa") or {}).get("duration"),
                  (video.get("media") or {}).get("video", {}).get("duration")):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return value
    return None


def note_info(note, note_id):
    """note dict → 管线要用的元信息 + 视频流地址 + 内嵌字幕轨。"""
    video = note.get("video") or {}
    duration = _note_duration(video)

    stream = (video.get("media") or {}).get("stream") or {}
    # codec 键见过 h264/h265/av1 与 EF4-EF7（内部代号）两套命名，均按「先已知顺序再字典序」收
    order = [c for c in ("h264", "h265", "av1") if c in stream]
    order += sorted(k for k in stream if k not in order)
    urls = []
    for codec in order:
        for entry in stream.get(codec) or []:
            if entry.get("masterUrl"):
                urls.append(entry["masterUrl"])
            urls.extend(u for u in (entry.get("backupUrls") or []) if isinstance(u, str) and u)

    subtitles = []
    try:
        v2 = json.loads(video.get("mediaV2") or "{}")
        for language, tracks in ((v2.get("video") or {}).get("subtitles") or {}).items():
            for track in tracks or []:
                if isinstance(track, dict) and track.get("url") \
                        and urllib.parse.urlsplit(track["url"]).path.endswith(".srt"):
                    subtitles.append(
                        {"language": str(track.get("language") or language), "url": track["url"]})
    except (ValueError, TypeError, AttributeError):
        pass

    return {
        "note_id": note_id,
        "title": note.get("title") or note_id,
        "desc": note.get("desc") or "",
        "owner": (note.get("user") or {}).get("nickname"),
        "duration": duration,
        "video_urls": urls,
        "subtitles": subtitles,
    }


def acquire_note(target):
    """分享链接 → (note_id, url, note_info, auth_mode)。匿名优先，被拒才带 XHS_COOKIE 重试一次。"""
    note_id, url = resolve_note_url(target)
    auth_mode = "anonymous"
    reason = ""
    try:
        note = _fetch_note(note_id, url)
    except _LoginRequired as exc:
        reason = exc.reason
        cookie = config.get_key("XHS_COOKIE")
        if not cookie:
            raise CliError(
                f"小红书拒绝匿名访问（{reason or '需登录或已触发反爬'}）：在 "
                "~/.config/ai-tools/bili-digest/keys.env 配置 XHS_COOKIE（从浏览器复制的 Cookie "
                "请求头）后重试，或过段时间再试", 4) from None
        log("匿名访问被拒，使用 XHS_COOKIE 重试一次")
        auth_mode = "cookie"
        try:
            note = _fetch_note(note_id, url, cookie=cookie)
        except _LoginRequired as exc:
            raise CliError(f"小红书未接受当前登录态（{exc.reason}；XHS_COOKIE 可能已过期），"
                           "重新从浏览器复制", 4) from None
    return note_id, url, note_info(note, note_id), auth_mode


# ---------------------------------------------------------------------------
# 内嵌字幕（.srt 下载与解析，pysubs2 的正则等价实现）

SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def pick_subtitle(subtitles):
    """中文轨优先，其次第一条；空列表返回 None。"""
    for sub in subtitles:
        lang = str(sub.get("language") or "").lower()
        if "zh" in lang or "中" in lang:
            return sub
    return subtitles[0] if subtitles else None


def parse_srt(text):
    """SRT 文本 → [{"from","to","text"}]（与 bilibili.download_subtitle 同构）。"""
    segs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        for idx, line in enumerate(lines):
            m = SRT_TIME_RE.search(line)
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            body = re.sub(r"\s+", " ", "\n".join(lines[idx + 1:])).strip()
            segs.append({"from": start, "to": end, "text": body})
            break
    return segs


def download_srt(url):
    """下载 .srt → 分段列表。"""
    status, raw, _ = _http_get(url)
    if status >= 400:
        raise CliError(f"字幕下载失败（HTTP {status}），稍后重试", 4)
    return parse_srt(raw.decode("utf-8-sig", "ignore"))


# ---------------------------------------------------------------------------
# 视频流 → 16k mono wav（audio.to_wav16k 复用；直链 mp4 先落盘，m3u8 交给 ffmpeg）

def fetch_audio(note_id, urls, work_dir):
    """逐候选地址下载视频流并转 16k 单声道 wav，返回 wav 路径。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / f"audio_xhs_{note_id}.wav"
    last_err = None
    for url in filter(None, urls):
        try:
            if urllib.parse.urlsplit(url).path.endswith(".m3u8"):
                audio.to_wav16k(url, wav)  # ffmpeg 直接读 HLS 播放列表
            else:
                status, raw, _ = _http_get(url)
                if status >= 400:
                    raise CliError(f"视频流下载失败（HTTP {status}）", 4)
                fd, tmp = tempfile.mkstemp(dir=work_dir, suffix=".mp4")
                os.close(fd)
                try:
                    with open(tmp, "wb") as fh:
                        fh.write(raw)
                    log(f"视频已下载 {len(raw) / 1e6:.1f} MB")
                    audio.to_wav16k(tmp, wav)
                finally:
                    os.unlink(tmp)
            return wav
        except CliError as err:
            last_err = err
            log(f"该视频地址失败（{err}），换下一个")
    raise last_err or CliError("没有可用视频流地址，无法走本地 ASR", 3)
