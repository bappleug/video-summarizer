# cookie-exporter — Cookie 导出助手（Chrome MV3 扩展）

一个极简的 Chrome 浏览器插件：检测**小红书 / 哔哩哔哩**的登录态，点一下按钮就把完整 Cookie **直写**进 `~/.config/ai-tools/bili-digest/keys.env`（Native Messaging 本机助手，免手动粘贴）。面向不想打开开发者工具翻 Application 面板的非程序员，是 [bili-digest](../README.md) 工具链的配套，但本身是通用工具，加站点只改一个文件。

- 零第三方库：原生 JavaScript + python3 标准库，无构建步骤。
- 零网络权限：插件与本机助手都不发起任何网络请求（见下方「安全模型」）。

## 目录结构

```
cookie-exporter/
├── manifest.json        # MV3 清单：权限白名单 + key（锁定稳定扩展 ID 的公钥）
├── popup.html/.css      # 弹窗界面（中文）
├── js/
│   ├── sites.js         # 站点配置（单一事实源：加站点只改这里）
│   ├── exporters.js     # 导出方式注册表（1.2.0 仅直写；加方式只改这里）
│   ├── lib.js           # 纯逻辑：Cookie 拼装 / 登录态判定（无 chrome 依赖，可单测）
│   ├── popup.js         # 弹窗编排（api/ui 注入式，可用 mock 单测）+ DOM 渲染
│   ├── chrome-api.js    # chrome 特权 API 薄封装（全项目唯一触碰处）
│   └── main.js          # 弹窗入口：装配真实 API 与 DOM
├── native/              # 本机助手（Native Messaging 直写 keys.env）
│   ├── host.py          # 只认 write_key：target 白名单 + 固定路径 + 0600
│   ├── com.raymin.cookie_exporter.json   # host manifest 模板（占位符，由安装脚本实例化）
│   ├── install-host.sh / uninstall-host.sh
└── tests/               # node --test（扩展侧）+ python3 unittest（host 侧），零依赖
```

## 安装（面向非程序员，约 2 分钟）

> 前提：你平时用来**登录小红书 / B站**的那个 Chrome 浏览器（插件只能读到它自己所在 Chrome 配置文件里的 Cookie；如果你用了多 Profile，请确认登录网站与安装插件是同一个 Profile）。

**第 1 步：装本机助手**（一次性，直写功能依赖它）

打开「终端」（Terminal），粘贴下面这行并回车：

```sh
bash ~/path/to/video-summarizer/cookie-exporter/native/install-host.sh
```

（把路径里的 `~/path/to/video-summarizer` 换成本仓库 clone 后的实际位置。）装完**完全退出 Chrome（Cmd+Q）再重开**——Chrome 只在启动时读取助手清单。
（截图位：终端运行 install-host.sh 的输出特写）

**第 2 步：装扩展**

1. 打开 Chrome，在地址栏输入 `chrome://extensions` 并回车，进入「扩展程序」管理页。
   （截图位：地址栏输入 chrome://extensions 的特写）
2. 打开页面右上角的**「开发者模式」**开关。
   （截图位：右上角开关，打开后变为蓝色）
3. 点击左上角**「加载已解压的扩展程序」**，选中本目录 `cookie-exporter/`（即包含 `manifest.json` 的文件夹）。
4. 列表出现「**Cookie 导出助手**」即装好。建议点击工具栏拼图图标 🧩 把它**固定**到工具栏。
5. 详情页确认扩展 ID 为 `cilhmdbgnppkdmeonmdfnijpejfmfclm`（与助手注册的允许名单一致，直写才能通）。

## 使用方法

1. 点击工具栏的「Cookie 导出助手」图标，弹出小窗口。
2. 每个站点一张卡片：站点名 + 右上角登录徽标 + 一个主按钮。
   - **已登录 · 共 N 个 Cookie**：关键 Cookie 齐全，导出的凭据大概率有效；
   - **未登录 · 共 N 个 Cookie**：请先去网站登录，回来重新打开弹窗。
3. 点**「导出到 keys.env」**：按钮变「已导出 ✓」，下方显示一行 `~/.config/ai-tools/bili-digest/keys.env` 路径（可选中复制）即写入完成——字段已存在则整行替换，不存在则追加，文件保持 0600 权限。
4. 想看 keys.env 路径时点卡片右上角的 **ⓘ**：展开/收起一行等宽路径文本（可选中复制）。
5. 「上次使用」小标记记住你上次导出的站点（存浏览器本地 `chrome.storage`，仅存站点 id，不含 Cookie）。

## 常见问题

- **明明登录了，却显示「未登录」？** 多半是登录网站和安装插件不在同一个 Chrome Profile，或在隐身窗口里登录的。请在同一个 Profile 下登录后重试。
- **点了导出报「本机助手未安装」？** 按状态行里显示的命令运行 install-host.sh（在本仓库根目录下运行，或自行补全绝对路径），然后**完全退出 Chrome（Cmd+Q）再重开**。仍失败时确认扩展详情页 ID 是 `cilhmdbgnppkdmeonmdfnijpejfmfclm`（与 host 注册的 allowed_origins 一致才会放行）。
- **Cookie 会过期吗？** 会。网站登录态失效后重新登录、再点一次导出即可。

## 运行测试（开发者）

本机已装 node（≥ 20）与 python3 即可，无需安装任何依赖：

```sh
cd cookie-exporter
npm test           # = node --test（扩展侧 30 例）+ python3 unittest（host 侧 8 例，子进程全链路）
```

扩展侧覆盖（30 个用例）：Cookie 拼装格式、登录态判定（含「未登录时 a1 仍在」的误判防护）、exporter 注册表与直写链路（write_key 报文、成功只显路径不含「替换/新增」、未安装错误的 segments 结构）、站点配置结构校验、manifest 权限白名单与 key 字段断言、popup 编排全流程（mock 注入 chrome API 与卡片 UI：徽标、导出成功/失败/空 cookie、ⓘ 路径 toggle、storage 记忆与异常兜底）。

host 侧覆盖（8 个用例，`tests/test_host.py`）：以真实协议帧通过子进程管道驱动 `native/host.py`——新增/替换/幂等写入、0600 权限、目录自动创建、target 白名单拒绝、value 含换行拒绝、未知指令拒绝、stdin 提前关闭不崩溃。写入路径经 `COOKIE_EXPORTER_KEYS_ENV` 指向临时目录，绝不触碰真实 keys.env。

## 如何加新站点

只改两个文件，各加一行：

1. `js/sites.js` 的 `SITES` 数组加一个条目：

   ```js
   {
     id: "douyin",                      // 唯一短 id（小写）
     label: "抖音",                     // 卡片上显示的名字
     domain: ".douyin.com",             // 点开头；chrome.cookies 按此读取该域及子域
     keyCookies: ["sessionid"],         // 判定登录态的关键 Cookie 名（须全部存在才算已登录）
     target: "DOUYIN_COOKIE",           // keys.env 里的字段名（大写+下划线）
   },
   ```

2. `manifest.json` 的 `host_permissions` 加上 `*://*.douyin.com/*`；`native/host.py` 的 `ALLOWED_TARGETS` 加上 `"DOUYIN_COOKIE"`。

3. 跑 `npm test` 确认全绿，重新加载扩展即可。

## 如何加新导出方式（exporter 插件层）

导出方式是插件化的：`js/exporters.js` 里每种方式一个对象 `{ id, label, buttonLabel, run(cookieHeader, site, api) }`。1.2.0 只保留直写（`EXPORTERS` 单元素）；未来加回剪贴板/下载等方式时：在 `js/exporters.js` 实现对象并加入 `EXPORTERS`，再在 `js/popup.js` 恢复导出方式选择 UI（历史实现见 git log 1.1.0 版本的 popup.js/exporters.js）。

- `run` 成功返回「状态行片段」数组（字符串或 `{ command }`，command 渲染为可选中复制的命令样式）；
- `run` 失败抛 Error，可操作错误在 `err.segments` 上带同样的片段数组；
- 保持零网络纪律：`run` 内不得引入 `fetch` / `XMLHttpRequest` 等网络调用。

## 安全模型

- **无网络权限**：manifest 未申请任何 API 权限之外的 host 访问；两处 host 权限（`*.xiaohongshu.com`、`*.bilibili.com`）仅用于 `chrome.cookies` **读取**该站 Cookie，插件代码中不存在任何 `fetch` / `XMLHttpRequest` / `WebSocket`，无法把数据发往任何服务器。本机助手同样零网络：python 标准库、只做本地文件读写。
- **直写的边界（host 侧）**：`nativeMessaging` 权限只用于与本地 `host.py` 通信；host 只认 `write_key` 一个指令，且：
  - **target 白名单**：只接受 `XHS_COOKIE` / `BILI_COOKIE`，扩展消息无法向任意字段注入；
  - **路径写死**：只写 `~/.config/ai-tools/bili-digest/keys.env`，不接受消息指定路径（`COOKIE_EXPORTER_KEYS_ENV` 环境变量仅供单测注入）；
  - **value 含换行直接拒绝**：防止一次写入注入多行；
  - **0600 权限**：写入后显式 `chmod 0600`；
  - **无值日志**：日志只走 stderr 且不含 cookie 值，stdout 只有协议回包；
  - **allowed_origins 锁定**：host manifest 只接受本扩展的稳定 ID（见下），其他扩展无法调用。
- **不存储**：Cookie 值只在内存中拼接，唯一去向是你主动触发的 keys.env 直写。不写 `console`、不落 `chrome.storage`，弹窗关闭即弃。`chrome.storage` 只存一个不含敏感信息的偏好：上次站点 id。
- 权限白名单（多余即不合格，有单测看护）：`permissions: ["cookies", "nativeMessaging", "storage"]`。

## 稳定扩展 ID（无私钥）

host 的 `allowed_origins` 必须写死扩展 ID，而「加载已解压的扩展程序」默认按扩展所在路径派生 ID——换目录、换机器都会变。因此 `manifest.json` 里放了 `key` 字段（公钥，随 git 保管），把扩展 ID 固定为：

```
cilhmdbgnppkdmeonmdfnijpejfmfclm
```

- **使用者无需任何密钥文件**：Chrome 只凭 `manifest.json` 里的公钥就能算出这个 ID，clone 仓库后直接加载即可。扩展运行时不读取任何私钥。
- **本插件没有私钥**：不发布 Chrome 应用商店（clone + 开发者模式加载的分发方式），签名私钥已于 2026-11-23 销毁。副作用是良性的——没有任何人（包括维护者）能以本 ID 签发商店包，ID 的不可冒领不依赖保管任何秘密。
- **不要把 .pem 放进本目录**：放进扩展目录会在 Chrome 加载时触发「This extension includes the key file …」警告，且扩展运行并不需要它；本目录 `.gitignore` 保留 `native/extension_key.pem` 一条作为误提交兜底。
- 若未来确需以同 ID 发布商店包，或更换 ID：重新生成密钥对 → 更新 `manifest.key` 与 `install-host.sh` 里的 ID → 所有使用者重跑安装脚本并重新加载扩展（ID 会变，等于全体重装）。生成命令（私钥生成到仓库外，不要生成到本目录）：

  ```sh
  KEY=~/path/to/extension_key.pem   # 仓库外
  openssl genrsa -out "$KEY" 2048 && chmod 600 "$KEY"
  openssl rsa -in "$KEY" -pubout -outform DER | base64 | tr -d '\n'   # 贴入 manifest.key
  openssl rsa -in "$KEY" -pubout -outform DER | shasum -a 256         # 前 32 hex → a-p 字母表即新 ID
  ```

## 验证边界与手测清单

本插件的开发与验收由无法操作真实浏览器的 agent 完成，因此验证分两层，如实标注：

- **已由自动化单测覆盖**（`npm test` = node 30 例 + python 8 例，全绿）：扩展侧——Cookie 拼装与格式、登录态判定、exporter 注册表与直写链路（write_key 报文、segments 反馈结构、未安装指引）、manifest 权限白名单与 key 字段、popup 编排全分支（mock 注入 chrome API 与卡片 UI，含 ⓘ toggle）。host 侧——子进程管道全链路：协议帧收发、新增/替换/幂等写入、0600、白名单与换行拒绝、未知指令拒绝、异常输入不崩溃。
- **需要在真实 Chrome 中手动验证**（预计 5 分钟）：

  1. 按第 1/2 步装好本机助手与扩展（已装过的用户 1.2.0 升级后需重新加载扩展）。
  2. 打开弹窗：卡片只有站点名 + 登录徽标 + ⓘ + 「导出到 keys.env」按钮，没有下拉框、目标行和设置行。
  3. 徽标与实际登录状态一致（已登录 → 绿色「已登录 · 共 N 个 Cookie」）。
  4. 点「导出到 keys.env」→ 按钮变「已导出 ✓」→ 下方出现一行 keys.env 路径（可选中复制）→ 打开 `~/.config/ai-tools/bili-digest/keys.env` 确认对应行已替换/追加、其余行不动、文件权限 0600。
  5. 点 ⓘ 展开路径、再点收起。
  6. 卸载指引：`uninstall-host.sh` 并重启 Chrome → 导出 → 状态行显示「本机助手未安装：bash …install-host.sh（需完全退出 Chrome 重开）」，其中命令可选中复制。
  7. 关闭弹窗再打开：上次导出的站点带「上次使用」标记。
  8. 导出的凭据交给 bili-digest 实际跑一次，确认有效。
  9. `chrome://extensions` 详情页确认权限仅限白名单（cookies / nativeMessaging / storage + 两个站点 host）。
