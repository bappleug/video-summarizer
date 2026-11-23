# bili

低 token 提取 B 站视频内容的命令行工具：优先抓字幕，输出纯文本；没有字幕轨时明确报错，不做视频/抽帧这类高消耗兜底。

## 安装

```bash
sh install.sh              # 软链到 ~/.local/bin/bili
python3 -m unittest discover -s tests   # 跑单测
```

只依赖 Python 3.8+ 标准库；自动续期额外用到系统 `openssl`，登录时在终端画二维码用到 `qrencode`（`brew install qrencode`，没装也能用 `--open`/`--url`）。

## 使用

```bash
bili login                 # 终端画二维码，用 B 站 App 扫码
bili login --open          # 在浏览器打开确认页，直接点「确认」
bili login --url           # 只输出二维码链接
bili status                # 看登录态和续期能力
bili info <链接|BV号>       # 标题 / UP / 时长 / 简介 / 分P
bili fetch <链接|BV号>      # 输出字幕纯文本（默认）
bili fetch <链接|BV号> --timestamps          # 每行带 [mm:ss]
bili fetch <链接|BV号> --format srt -o a.srt
bili fetch <链接|BV号> --format json         # 含 segments 的结构化输出
bili fetch <链接|BV号> --page 2 --lang ai-zh --max-chars 8000
bili refresh --force       # 强制续期
```

`info` / `status` 支持 `--json`，便于脚本消费。内容走 stdout，进度与告警走 stderr。

退出码：`0` 成功、`2` 用法错误、`3` 未登录/无字幕、`4` 网络或风控。

## 它怎么工作

1. `x/web-interface/view` 取 `aid`/`cid`（免登录）。
2. `x/player/wbi/v2`（WBI 签名）取字幕轨列表——**匿名请求固定返回 0 轨，必须带登录态**。
3. 按 `subtitle_url` 下载字幕 JSON（CDN 本身不校验 cookie，但 `auth_key` 分钟级过期，拿到立即下）。
4. 按语言偏好选轨：人工中文字幕（`zh-CN`/`zh-Hans`）优先于 AI 字幕（`ai-zh`）；可用 `--lang` 指定。
5. 输出前丢掉与上一段完全相同的行（AI 字幕常见重复）。

## 凭据与自动续期

登录时服务端会下发 `refresh_token`。工具在每次命令执行前检查一次（6 小时内不重复查），需要续期时走：

1. `passport.bilibili.com/x/passport-login/web/cookie/info` 判断是否需要刷新；
2. `refresh_{毫秒时间戳}` 用 B 站前端公钥做 RSA-OAEP(SHA-256)，得到 `correspondPath`；
3. 从 `www.bilibili.com/correspond/1/{path}` 取一次性 `refresh_csrf`；
4. POST `cookie/refresh` 换新 cookie，并轮换 `refresh_token`；
5. POST `confirm/refresh` 让旧凭据失效。

任何一步失败都会保留旧凭据并提示重新 `bili login`，不会静默降级。

## 已知边界

- **未登录拿不到字幕**：这是接口侧约束，不是工具限制。扫码一次即可，之后靠续期。
- **风控**：批量抓会被限流（HTTP 412 或 `code -412`），工具会直接报错退出；控制频率、别挂频繁变动的出口 IP。
- **无字幕视频**：工具不会回退到语音识别。需要转写时，可用 `x/player/playurl?fnval=16` 取 DASH 音频（免登录），再交给本地 `whisper.cpp` 之类的 ASR。
- **公钥与接口可能变**：公钥写死在 `bili.py`，B 站轮换时续期会失败并提示重新登录；字幕接口若改版，需要更新 `subtitle_tracks`。
- 字幕是机器或 UP 主提供的文本，专有名词可能有错；重要内容对一下标题和简介。
