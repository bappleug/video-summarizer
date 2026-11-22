// chrome-api.js — 浏览器特权 API 薄封装，全项目唯一触碰 chrome / navigator 的文件。
// 只能在扩展页面（chrome-extension://）里运行；调用方（popup.js / exporters.js）
// 只依赖这里的函数签名，单测以 mock 注入覆盖，不加载本文件。
//
// 完整 api 约定（popup.js 编排与所有 exporter 依赖的能力面）：
//   getCookies(domain) -> Promise<cookie[]>
//   sendNativeMessage(host, message) -> Promise<reply>     Native Messaging 本机助手
//   getLastSiteId() / saveLastSiteId(id)                   上次导出的站点（「上次使用」标记）

export function getCookies(domain) {
  return chrome.cookies.getAll({ domain });
}

export function sendNativeMessage(hostName, message) {
  return chrome.runtime.sendNativeMessage(hostName, message);
}

export async function getLastSiteId() {
  const { lastSiteId } = await chrome.storage.local.get("lastSiteId");
  return typeof lastSiteId === "string" ? lastSiteId : null;
}

export function saveLastSiteId(siteId) {
  return chrome.storage.local.set({ lastSiteId: siteId });
}

export const chromeApi = { getCookies, sendNativeMessage, getLastSiteId, saveLastSiteId };
