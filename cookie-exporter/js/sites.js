// sites.js — 站点配置（单一事实源）。
// 要支持新站点，只需要在 SITES 里加一个条目，popup 会自动渲染对应卡片；
// 同时需在 manifest.json 的 host_permissions 补该站点的 host 权限，
// 并在 native/host.py 的 ALLOWED_TARGETS 里加 target 字段名。
// 结构约束见 tests/lib.test.js 的「站点配置」用例。

export const SITES = [
  {
    id: "xhs",
    label: "小红书",
    domain: ".xiaohongshu.com",
    keyCookies: ["web_session", "a1"], // 用于判定登录态的关键 Cookie
    target: "XHS_COOKIE", // keys.env 里的字段名
  },
  {
    id: "bili",
    label: "哔哩哔哩",
    domain: ".bilibili.com",
    keyCookies: ["SESSDATA", "bili_jct"],
    target: "BILI_COOKIE",
  },
];
