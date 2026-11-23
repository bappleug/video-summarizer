// lib.js — 纯逻辑模块，不依赖任何 chrome / DOM API，供 popup 与 node --test 共用。

// 把 chrome.cookies.getAll 返回的 cookie 对象数组拼成标准 Cookie 请求头格式：
// "name1=value1; name2=value2"（"; " 分隔，结尾无尾随分隔符，保持输入顺序）。
export function buildCookieHeader(cookies) {
  return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}

// 登录态判定：site.keyCookies 列出的关键 Cookie **全部**存在才算「已登录」。
// 刻意取「全部存在」而非「任一存在」：误报「未登录」只会引导用户重新登录，
// 误报「已登录」则会让用户导出一份实际无效的 Cookie，更难排查。
export function isLoggedIn(site, cookies) {
  const names = new Set(cookies.map((c) => c.name));
  return site.keyCookies.every((name) => names.has(name));
}
