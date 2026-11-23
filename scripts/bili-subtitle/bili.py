#!/usr/bin/env python3
"""bili —— 低 token 提取 B 站视频内容（字幕优先）。

子命令:
  login    扫码登录，凭据保存到 ~/.config/ai-tools/bili/credentials.json (0600)
  status   查看登录态
  refresh  手动续期 cookie（登录态下自动续期也走这条链）
  info     视频元信息（标题 / UP / 时长 / 简介 / 分P）
  fetch    抓字幕，输出纯文本（默认）/ SRT / JSON

内容输出走 stdout，进度和告警走 stderr。
退出码: 0 成功 / 2 用法错误 / 3 未登录或无字幕 / 4 网络或风控 / 1 其它
"""

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
API = "https://api.bilibili.com"
PASSPORT = "https://passport.bilibili.com"
WEB = "https://www.bilibili.com"
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
    "ai-tools",
    "bili",
)
CRED_PATH = os.path.join(CONFIG_DIR, "credentials.json")
PUBKEY_PATH = os.path.join(CONFIG_DIR, "correspond_pub.pem")

# B站前端 wasm 里写死的 RSA 公钥，仅用于生成本地 correspondPath。
PUBKEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----
"""

COOKIE_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")

# WBI 重排表
MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]

# 人工投稿字幕优先于 AI 字幕（AI 字幕同音错字多）
LANG_PREF = ("zh-CN", "zh-Hans", "ai-zh", "zh-TW", "zh-HK", "ai-en", "en")


class CliError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def log(msg):
    sys.stderr.write(f"bili: {msg}\n")


def out(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


# ---------------------------------------------------------------- HTTP

def http(url, cookie=None, form=None, referer=WEB + "/"):
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers
    except urllib.error.URLError as err:
        raise CliError(f"网络不可用: {err.reason}", 4)


def api_json(url, cookie=None, form=None, referer=WEB + "/"):
    status, raw, _ = http(url, cookie=cookie, form=form, referer=referer)
    if status == 412:
        raise CliError("被风控拦截（HTTP 412），稍后重试或降低请求频率", 4)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CliError(f"接口返回非 JSON（HTTP {status}），可能被风控", 4)
    if data.get("code") == -412:
        raise CliError("被风控拦截（code -412），稍后重试或降低请求频率", 4)
    return data


def cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k in COOKIE_KEYS and v)


# ---------------------------------------------------------------- 凭据

def load_cred():
    if not os.path.exists(CRED_PATH):
        return None
    try:
        with open(CRED_PATH, "r", encoding="utf-8") as fh:
            cred = json.load(fh)
    except (OSError, ValueError):
        raise CliError(f"凭据文件损坏，请重新 bili login: {CRED_PATH}", 3)
    if not cred.get("cookies", {}).get("SESSDATA"):
        return None
    return cred


def save_cred(cred):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cred, fh, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CRED_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------- WBI

def wbi_keys():
    data = api_json(f"{API}/x/web-interface/nav")
    img = data.get("data", {}).get("wbi_img", {})
    img_key = img.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = img.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        raise CliError("拿不到 WBI 密钥（nav 接口异常）", 4)
    return img_key, sub_key


def mixin_key(img_key, sub_key):
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_TAB)[:32]


def wbi_sign(params, key, now=None):
    cleaned = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in params.items()}
    cleaned["wts"] = int(now if now is not None else time.time())
    query = urllib.parse.urlencode(sorted(cleaned.items()))
    cleaned["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(cleaned.items()))


# ---------------------------------------------------------------- 视频定位

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"(?:^|[^0-9a-z])av(\d+)", re.IGNORECASE)


def resolve_bvid(target):
    """接受 BV 号、av 号、完整链接、b23.tv 短链。"""
    m = BV_RE.search(target)
    if m:
        return m.group(1)
    m = AV_RE.search(target)
    if m:
        data = api_json(f"{API}/x/web-interface/view?aid={m.group(1)}")
        if data.get("code") != 0:
            raise CliError(f"视频不存在或已下架: {target}", 3)
        return data["data"]["bvid"]
    if target.startswith("http"):
        status, raw, _ = http(target, referer=None)
        if status >= 400:
            raise CliError(f"链接打不开（HTTP {status}）: {target}", 4)
        m = BV_RE.search(raw.decode("utf-8", "ignore"))
        if m:
            return m.group(1)
    raise CliError(f"认不出这是哪个视频: {target}", 2)


def video_view(bvid):
    data = api_json(f"{API}/x/web-interface/view?bvid={bvid}")
    if data.get("code") != 0:
        raise CliError(f"取视频信息失败: {data.get('message')}", 3)
    return data["data"]


# ---------------------------------------------------------------- 字幕

def subtitle_tracks(bvid, cid, cookie=None):
    img_key, sub_key = wbi_keys()
    query = wbi_sign({"bvid": bvid, "cid": cid}, mixin_key(img_key, sub_key))
    data = api_json(f"{API}/x/player/wbi/v2?{query}", cookie=cookie)
    tracks = (data.get("data") or {}).get("subtitle", {}).get("subtitles") or []
    if not tracks:
        legacy = api_json(f"{API}/x/player/v2?bvid={bvid}&cid={cid}", cookie=cookie)
        tracks = (legacy.get("data") or {}).get("subtitle", {}).get("subtitles") or []
    return tracks


def pick_track(tracks, want=None):
    if not tracks:
        return None
    if want:
        for track in tracks:
            if want in (track.get("lan"), track.get("lan_doc")):
                return track
    for lang in LANG_PREF:
        for track in tracks:
            if track.get("lan") == lang:
                return track
    return tracks[0]


def download_subtitle(track):
    url = track.get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        raise CliError("字幕轨没有下载地址，重新 fetch 一次", 4)
    status, raw, _ = http(url)
    if status >= 400:
        raise CliError(f"字幕文件下载失败（HTTP {status}），auth_key 可能已过期，重试即可", 4)
    body = json.loads(raw.decode("utf-8")).get("body") or []
    return [{"from": float(x.get("from", 0)), "to": float(x.get("to", 0)),
             "text": re.sub(r"\s+", " ", str(x.get("content", ""))).strip()} for x in body]


def join_text(segments):
    """逐段拼接，丢掉与上一段完全相同的行（AI 字幕常见）。"""
    lines, prev = [], None
    for seg in segments:
        text = seg["text"]
        if not text or text == prev:
            continue
        lines.append(text)
        prev = text
    return "\n".join(lines)


def render_srt(segments):
    def stamp(sec):
        ms = int(round(sec * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)

    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(f"{i}\n{stamp(seg['from'])} --> {stamp(seg['to'])}\n{seg['text']}")
    return "\n\n".join(blocks)


def render_timed(segments):
    def clock(sec):
        return "%02d:%02d" % (int(sec) // 60, int(sec) % 60)

    return "\n".join(f"[{clock(s['from'])}] {s['text']}" for s in segments if s["text"])


# ---------------------------------------------------------------- 登录 / 续期

def show_qr(url):
    """在终端画二维码。B站这个 URL 是给 App 扫的，直接在浏览器打开只会看到确认页。"""
    if not shutil.which("qrencode"):
        log("没装 qrencode，无法在终端画二维码（brew install qrencode）")
        log(f"二维码内容: {url}")
        log("或者加 --open 在浏览器里打开确认页，点「确认」完成登录")
        return False
    result = subprocess.run(
        ["qrencode", "-t", "ANSIUTF8", "-m", "4", "-o", "-", url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log(f"qrencode 出错: {result.stderr.decode().strip()}")
        log(f"二维码内容: {url}")
        return False
    # qrencode 的 ANSIUTF8 默认是黑底白块（反色），多数扫码器能认但留白也是黑的；
    # 换成白底黑块，留白才真的是白的。
    sys.stderr.write(paint_qr_white(result.stdout.decode("utf-8", "ignore")))
    sys.stderr.flush()
    return True


def paint_qr_white(block):
    return block.replace("\x1b[40;37;1m", "\x1b[47;30;1m")


def qr_login(timeout, open_page, url_only):
    qr = api_json(f"{PASSPORT}/x/passport-login/web/qrcode/generate")
    url = qr["data"]["url"]
    key = qr["data"]["qrcode_key"]

    if url_only:
        out(url)
    else:
        log("用 B 站 App 扫下面的二维码")
        show_qr(url)
    if open_page:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.run([opener, url], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log("已在浏览器打开确认页，也可以直接点「确认」完成登录")
        except OSError:
            log(f"打不开浏览器，请手动访问: {url}")

    deadline = time.time() + timeout
    scanned = False
    while time.time() < deadline:
        time.sleep(2)
        status, raw, headers = http(f"{PASSPORT}/x/passport-login/web/qrcode/poll?qrcode_key={key}")
        try:
            poll = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise CliError(f"登录状态接口返回异常（HTTP {status}），重试一次", 4)
        data = poll.get("data") or {}
        state = data.get("code")
        if state == 86101:
            continue
        if state == 86090 and not scanned:
            scanned = True
            log("已扫码，请在手机上点确认")
            continue
        if state == 86038:
            raise CliError("二维码已过期，重新执行 bili login", 3)
        if poll.get("code") == 0 and state == 0:
            return build_cred_from_poll(data, headers)
    raise CliError(f"等待扫码超时（{timeout}s），重新执行 bili login", 3)


def parse_query_raw(url):
    """保留百分号编码：SESSDATA 里的 %2C 是官方客户端原样发送的形式，解码成逗号反而不对。"""
    result = {}
    for part in urllib.parse.urlparse(url).query.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key] = value
    return result


def build_cred_from_poll(data, headers=None):
    query = parse_query_raw(data.get("url", ""))
    cookies = {}
    for key in COOKIE_KEYS:
        if query.get(key):
            cookies[key] = query[key]
    if headers is not None:
        for key, value in parse_set_cookie(headers).items():
            cookies.setdefault(key, value)
    if not cookies.get("SESSDATA"):
        from_header = sorted(parse_set_cookie(headers)) if headers is not None else []
        log(f"poll 返回字段: {sorted(data)}；url 里的键: {sorted(query)}；Set-Cookie: {from_header}")
        raise CliError("登录成功但没拿到 SESSDATA，重试一次", 3)
    return {
        "cookies": cookies,
        "refresh_token": data.get("refresh_token", ""),
        "login_at": int(time.time()),
        "last_check": int(time.time()),
    }


def correspond_path(timestamp_ms):
    """refresh_{ts} 做 RSA-OAEP(SHA-256)，输出小写 hex（256 字符）。"""
    if not os.path.exists(PUBKEY_PATH):
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        with open(PUBKEY_PATH, "w", encoding="utf-8") as fh:
            fh.write(PUBKEY_PEM)
    result = subprocess.run(
        ["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", PUBKEY_PATH,
         "-pkeyopt", "rsa_padding_mode:oaep",
         "-pkeyopt", "rsa_oaep_md:sha256",
         "-pkeyopt", "rsa_mgf1_md:sha256"],
        input=f"refresh_{timestamp_ms}".encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise CliError(f"生成 correspondPath 失败（openssl）: {result.stderr.decode().strip()}", 4)
    return result.stdout.hex()


def parse_set_cookie(headers):
    merged = {}
    for raw in headers.get_all("Set-Cookie") or []:
        first = raw.split(";", 1)[0]
        if "=" in first:
            key, value = first.split("=", 1)
            if key.strip() in COOKIE_KEYS:
                merged[key.strip()] = value.strip()
    return merged


def refresh_cred(cred, force=False):
    cookies = cred["cookies"]
    cookie = cookie_header(cookies)
    info = api_json(f"{PASSPORT}/x/passport-login/web/cookie/info", cookie=cookie)
    if info.get("code") != 0:
        raise CliError(f"检查登录态失败: {info.get('message')}（可能已过期，重新 bili login）", 3)
    payload = info.get("data") or {}
    if not payload.get("refresh") and not force:
        return False
    if not cred.get("refresh_token"):
        raise CliError("缺少 refresh_token，无法自动续期，请重新 bili login", 3)

    timestamp = payload.get("timestamp") or int(time.time() * 1000)
    path = correspond_path(timestamp)
    status, raw, _ = http(f"{WEB}/correspond/1/{path}", cookie=f"SESSDATA={cookies['SESSDATA']}")
    if status >= 400:
        raise CliError(f"取 refresh_csrf 失败（HTTP {status}），重新 bili login", 3)
    match = re.search(r'<div id="1-name">([\s\S]+?)</div>', raw.decode("utf-8", "ignore"))
    if not match:
        raise CliError("correspond 页面里没有 refresh_csrf，B站可能改了机制，重新 bili login", 3)
    refresh_csrf = html_lib.unescape(match.group(1)).strip()

    old_token = cred["refresh_token"]
    status, raw, headers = http(
        f"{PASSPORT}/x/passport-login/web/cookie/refresh",
        cookie=cookie,
        form={
            "csrf": cookies.get("bili_jct", ""),
            "refresh_csrf": refresh_csrf,
            "source": "main_web",
            "refresh_token": old_token,
        },
        referer=f"{WEB}/",
    )
    try:
        result = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise CliError(f"续期接口返回异常（HTTP {status}）", 4)
    if result.get("code") != 0:
        code = result.get("code")
        hint = "（refresh_token 失效，重新 bili login）" if code == 86095 else ""
        raise CliError(f"续期失败 code={code} {result.get('message')}{hint}", 3)

    cookies.update(parse_set_cookie(headers))
    cred["cookies"] = cookies
    cred["refresh_token"] = (result.get("data") or {}).get("refresh_token") or old_token
    cred["last_check"] = int(time.time())
    save_cred(cred)

    # confirm 让旧凭据失效；失败不影响新凭据使用。
    api_json(
        f"{PASSPORT}/x/passport-login/web/confirm/refresh",
        cookie=cookie_header(cookies),
        form={"csrf": cookies.get("bili_jct", ""), "refresh_token": old_token},
    )
    return True


def maybe_refresh(cred):
    if not cred or not cred.get("refresh_token"):
        return cred
    if int(time.time()) - int(cred.get("last_check", 0)) < 6 * 3600:
        return cred
    try:
        if refresh_cred(cred):
            log("已自动续期 cookie")
    except CliError as err:
        log(f"自动续期跳过: {err}")
    return cred


# ---------------------------------------------------------------- 命令

def cmd_login(args):
    cred = qr_login(args.timeout, args.open, args.url)
    save_cred(cred)
    log(f"凭据已保存: {CRED_PATH}")
    try:
        nav = api_json(f"{API}/x/web-interface/nav", cookie=cookie_header(cred["cookies"]))
        if nav.get("code") == 0:
            log(f"登录账号: {nav['data'].get('uname')} (mid {nav['data'].get('mid')})")
    except CliError:
        pass
    out("ok")


def cmd_status(args):
    cred = load_cred()
    if not cred:
        if args.json:
            out(json.dumps({"logged_in": False}, ensure_ascii=False))
        else:
            out("未登录：先执行 bili login")
        return
    nav = api_json(f"{API}/x/web-interface/nav", cookie=cookie_header(cred["cookies"]))
    info = {
        "logged_in": nav.get("code") == 0,
        "uname": (nav.get("data") or {}).get("uname"),
        "mid": (nav.get("data") or {}).get("mid"),
        "login_at": cred.get("login_at"),
        "has_refresh_token": bool(cred.get("refresh_token")),
        "cred_path": CRED_PATH,
    }
    if args.json:
        out(json.dumps(info, ensure_ascii=False, indent=2))
        return
    if not info["logged_in"]:
        out("登录态已失效：重新执行 bili login")
        return
    age = (int(time.time()) - int(cred.get("login_at", 0))) // 86400
    out(f"已登录: {info['uname']} (mid {info['mid']})，凭据已用 {age} 天")
    out(f"自动续期: {'可用' if info['has_refresh_token'] else '不可用（缺 refresh_token）'}")


def cmd_refresh(args):
    cred = load_cred()
    if not cred:
        raise CliError("未登录：先执行 bili login", 3)
    changed = refresh_cred(cred, force=args.force)
    out("已续期" if changed else "当前无需续期")


def cmd_info(args):
    bvid = resolve_bvid(args.target)
    view = video_view(bvid)
    pages = [{"page": p.get("page"), "cid": p.get("cid"), "part": p.get("part"),
              "duration": p.get("duration")} for p in view.get("pages", [])]
    data = {
        "bvid": bvid,
        "aid": view.get("aid"),
        "title": view.get("title"),
        "owner": (view.get("owner") or {}).get("name"),
        "duration": view.get("duration"),
        "desc": view.get("desc"),
        "tags": [t.get("tag_name") for t in (view.get("tags") or [])],
        "stat": view.get("stat"),
        "pages": pages,
    }
    if args.json:
        out(json.dumps(data, ensure_ascii=False, indent=2))
        return
    out(f"{data['title']}")
    out(f"UP: {data['owner']}  时长: {data['duration']}s  BV: {bvid}")
    if len(pages) > 1:
        out(f"分P: {len(pages)}")
        for page in pages:
            out(f"  P{page['page']} {page['part']} ({page['duration']}s)")
    if data["desc"]:
        out("")
        out(data["desc"].strip())


def cmd_fetch(args):
    cred = maybe_refresh(load_cred())
    bvid = resolve_bvid(args.target)
    view = video_view(bvid)
    pages = view.get("pages") or []
    page_no = args.page or 1
    if page_no < 1 or page_no > max(1, len(pages)):
        raise CliError(f"没有第 {page_no} P（共 {len(pages)} P）", 2)
    cid = pages[page_no - 1]["cid"] if pages else view["cid"]

    cookie = cookie_header(cred["cookies"]) if cred else None
    tracks = subtitle_tracks(bvid, cid, cookie=cookie)
    track = pick_track(tracks, args.lang)
    if not track:
        if not cred:
            hint = "未登录，先执行 bili login"
        else:
            hint = "该视频没有可用字幕轨；若账号确实有字幕，执行 bili status 看登录态是否失效"
        raise CliError(f"没拿到字幕：{hint}", 3)

    segments = download_subtitle(track)
    if not segments:
        raise CliError("字幕文件是空的", 3)

    if args.format == "json":
        payload = {
            "bvid": bvid,
            "cid": cid,
            "page": page_no,
            "title": view.get("title"),
            "duration": view.get("duration"),
            "lang": track.get("lan"),
            "lang_doc": track.get("lan_doc"),
            "segments": segments,
            "text": join_text(segments),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    elif args.format == "srt":
        body = render_srt(segments)
    elif args.timestamps:
        body = render_timed(segments)
    else:
        body = join_text(segments)

    if args.max_chars and len(body) > args.max_chars:
        body = body[: args.max_chars].rstrip() + "\n...(已截断)"
        log(f"输出截断到 {args.max_chars} 字符")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body if body.endswith("\n") else body + "\n")
        log(f"已写入 {args.out}（{len(body)} 字符）")
        return
    out(body)


# ---------------------------------------------------------------- CLI

def build_parser():
    parser = argparse.ArgumentParser(prog="bili", description="低 token 提取 B 站视频内容（字幕优先）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="扫码登录并保存凭据")
    p.add_argument("--timeout", type=int, default=180, help="等待扫码秒数（默认 180）")
    p.add_argument("--open", action="store_true", help="在浏览器打开确认页，可直接点「确认」登录")
    p.add_argument("--url", action="store_true", help="只输出二维码链接，不画二维码")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", help="查看登录态")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("refresh", help="续期 cookie")
    p.add_argument("--force", action="store_true", help="即使服务端说不需要也续期")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("info", help="视频元信息")
    p.add_argument("target", help="链接 / BV 号 / av 号")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("fetch", help="抓字幕")
    p.add_argument("target", help="链接 / BV 号 / av 号")
    p.add_argument("--page", type=int, help="第几个分P（默认 1）")
    p.add_argument("--lang", help="指定字幕轨语言，如 ai-zh / zh-CN")
    p.add_argument("--format", choices=("text", "srt", "json"), default="text")
    p.add_argument("--timestamps", action="store_true", help="text 格式下每行带时间戳")
    p.add_argument("--max-chars", type=int, help="输出字符上限")
    p.add_argument("-o", "--out", help="写入文件而不是 stdout")
    p.set_defaults(func=cmd_fetch)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except CliError as err:
        log(str(err))
        return err.code
    except KeyboardInterrupt:
        log("已取消")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
