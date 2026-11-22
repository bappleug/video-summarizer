"""B站平台模块：登录态 / cookie 续期 / WBI 签名 / 视频定位 / 字幕抓取。

复制自 tools/bili-subtitle/bili.py，改造点：
- 改造为模块函数供 pipeline 调用，去掉 CLI 入口与输出格式渲染（归 pipeline.py）
- CliError / log 移到 bili_digest.errors
- 新增 page_hint()：从 URL 的 ?p=N 解析分P提示
- 凭据路径不变：~/.config/ai-tools/bili/credentials.json（与 bili-subtitle 共享，0600）
此后本链路的修复以此模块为准（bili-subtitle 保持独立不动）。
"""

import hashlib
import html as html_lib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from ..errors import CliError, log

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
        raise CliError(f"凭据文件损坏，请重新 digest login: {CRED_PATH}", 3)
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


def page_hint(target):
    """从 URL 查询串解析 ?p=N 分P提示；非 URL 或无 p 参数返回 None。"""
    if not target.startswith("http"):
        return None
    m = re.search(r"(?:^|[?&])p=(\d+)", target)
    return int(m.group(1)) if m else None


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
        raise CliError("字幕轨没有下载地址，重新执行一次", 4)
    status, raw, _ = http(url)
    if status >= 400:
        raise CliError(f"字幕文件下载失败（HTTP {status}），auth_key 可能已过期，重试即可", 4)
    body = json.loads(raw.decode("utf-8")).get("body") or []
    return [{"from": float(x.get("from", 0)), "to": float(x.get("to", 0)),
             "text": re.sub(r"\s+", " ", str(x.get("content", ""))).strip()} for x in body]


# ---------------------------------------------------------------- 登录 / 续期

def show_qr(url):
    """在终端画二维码。B站这个 URL 是给 App 扫的，直接在浏览器打开只会看到确认页。"""
    import shutil
    import sys

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


def qr_login(timeout, open_page, url_only, out=print):
    qr = api_json(f"{PASSPORT}/x/passport-login/web/qrcode/generate")
    url = qr["data"]["url"]
    key = qr["data"]["qrcode_key"]

    if url_only:
        out(url)
    else:
        log("用 B 站 App 扫下面的二维码")
        show_qr(url)
    if open_page:
        import sys

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
            raise CliError("二维码已过期，重新执行 digest login", 3)
        if poll.get("code") == 0 and state == 0:
            return build_cred_from_poll(data, headers)
    raise CliError(f"等待扫码超时（{timeout}s），重新执行 digest login", 3)


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
        raise CliError(f"检查登录态失败: {info.get('message')}（可能已过期，重新 digest login）", 3)
    payload = info.get("data") or {}
    if not payload.get("refresh") and not force:
        return False
    if not cred.get("refresh_token"):
        raise CliError("缺少 refresh_token，无法自动续期，请重新 digest login", 3)

    timestamp = payload.get("timestamp") or int(time.time() * 1000)
    path = correspond_path(timestamp)
    status, raw, _ = http(f"{WEB}/correspond/1/{path}", cookie=f"SESSDATA={cookies['SESSDATA']}")
    if status >= 400:
        raise CliError(f"取 refresh_csrf 失败（HTTP {status}），重新 digest login", 3)
    match = re.search(r'<div id="1-name">([\s\S]+?)</div>', raw.decode("utf-8", "ignore"))
    if not match:
        raise CliError("correspond 页面里没有 refresh_csrf，B站可能改了机制，重新 digest login", 3)
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
        hint = "（refresh_token 失效，重新 digest login）" if code == 86095 else ""
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
