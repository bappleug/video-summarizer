// popup.test.js — popup 编排逻辑单测（chrome API / DOM 全部 mock 注入）。
// 1.2.0 用例：登录徽标、单一直写链路（报文/成功反馈/未安装指引）、ⓘ 路径切换、
// storage「上次使用」记忆、失败兜底。
// 真实浏览器行为（chrome.cookies 实读、Native Messaging 真通信、DOM 渲染）
// 见 README「手测清单」。cookie 名值均为伪造。

import test from "node:test";
import assert from "node:assert/strict";

import { SITES } from "../js/sites.js";
import { KEYS_ENV_PATH, INSTALL_CMD } from "../js/exporters.js";
import { buildCookieHeader } from "../js/lib.js";
import { createApp } from "../js/popup.js";

// ---- mock 设施 ----

// mock ui：记录每次卡片操作；click() 触发导出，info() 模拟点 ⓘ。
function createMockUi() {
  const cards = [];
  return {
    cards,
    createCard(site) {
      const card = {
        site,
        badges: [],
        buttons: [],
        statuses: [],
        errors: [],
        infoVisibles: [],
        lastUsedCount: 0,
        exportHandler: null,
        infoHandler: null,
        setBadge(loggedIn, count) {
          card.badges.push({ loggedIn, count });
        },
        setButton(text, disabled) {
          card.buttons.push({ text, disabled });
        },
        setStatus(segments, kind) {
          card.statuses.push({ segments, kind });
        },
        setError(message) {
          card.errors.push(message);
        },
        showInfoPath(visible) {
          card.infoVisibles.push(visible);
        },
        markLastUsed() {
          card.lastUsedCount += 1;
        },
        onExport(fn) {
          card.exportHandler = fn;
        },
        onInfo(fn) {
          card.infoHandler = fn;
        },
        click() {
          assert.ok(card.exportHandler, "导出回调未注册");
          return card.exportHandler();
        },
        info() {
          assert.ok(card.infoHandler, "ⓘ 回调未注册");
          card.infoHandler();
        },
        lastBadge() {
          return card.badges[card.badges.length - 1];
        },
        lastButton() {
          return card.buttons[card.buttons.length - 1];
        },
        lastStatus() {
          return card.statuses[card.statuses.length - 1];
        },
      };
      cards.push(card);
      return card;
    },
  };
}

// mock api：可定制 cookie / Native Messaging / storage 行为，记录全部调用。
function createMockApi({
  cookiesByDomain = {},
  nativeReply = { ok: true, replaced: false },
  nativeError = null,
  lastSiteId = null,
} = {}) {
  const calls = { domains: [], native: [], savedSiteIds: [] };
  return {
    calls,
    getCookies: async (domain) => {
      calls.domains.push(domain);
      const cookies = cookiesByDomain[domain];
      if (cookies === undefined) {
        throw new Error(`unexpected domain: ${domain}`);
      }
      return cookies;
    },
    sendNativeMessage: async (hostName, message) => {
      calls.native.push({ hostName, message });
      if (nativeError) throw nativeError;
      return nativeReply;
    },
    getLastSiteId: async () => lastSiteId,
    saveLastSiteId: async (id) => {
      calls.savedSiteIds.push(id);
    },
  };
}

const ck = (name, value = `fake-${name}`) => ({ name, value });

const XHS_LOGGED_IN = [ck("a1"), ck("web_session"), ck("webId"), ck("xhsappid")];
const XHS_LOGGED_OUT = [ck("a1"), ck("webId")]; // 未登录时 a1 依然存在
const BILI_LOGGED_IN = [ck("SESSDATA"), ck("bili_jct"), ck("DedeUserID")];

function defaultCookies() {
  return {
    ".xiaohongshu.com": XHS_LOGGED_IN,
    ".bilibili.com": BILI_LOGGED_IN,
  };
}

async function launch(options = {}) {
  const ui = createMockUi();
  const api = createMockApi(options);
  const app = createApp({ api, ui });
  await app.ready;
  return { ui, api };
}

const cardOf = (ui, id) => ui.cards.find((c) => c.site.id === id);

// ---- 初始化与登录态 ----

test("初始化：每个站点一张卡片，并按各自 domain 读取 cookie", async () => {
  const { ui, api } = await launch({ cookiesByDomain: defaultCookies() });
  assert.equal(ui.cards.length, SITES.length);
  assert.deepEqual(api.calls.domains.sort(), [".bilibili.com", ".xiaohongshu.com"]);
});

test("登录态徽标：关键 cookie 齐全为已登录，缺失为未登录", async () => {
  const { ui } = await launch({
    cookiesByDomain: {
      ".xiaohongshu.com": XHS_LOGGED_OUT,
      ".bilibili.com": BILI_LOGGED_IN,
    },
  });
  assert.deepEqual(cardOf(ui, "xhs").lastBadge(), { loggedIn: false, count: XHS_LOGGED_OUT.length });
  assert.deepEqual(cardOf(ui, "bili").lastBadge(), { loggedIn: true, count: BILI_LOGGED_IN.length });
});

test("无 cookie 的站点：徽标未登录，导出按钮禁用", async () => {
  const { ui } = await launch({
    cookiesByDomain: { ".xiaohongshu.com": [], ".bilibili.com": [] },
  });
  for (const card of ui.cards) {
    assert.deepEqual(card.lastBadge(), { loggedIn: false, count: 0 });
    assert.deepEqual(card.lastButton(), { text: "导出到 keys.env", disabled: true });
  }
});

test("读取失败：徽标显示错误且按钮禁用，不抛出未捕获异常", async () => {
  const ui = createMockUi();
  const api = createMockApi({});
  api.getCookies = async () => {
    throw new Error("权限被拒绝");
  };
  await createApp({ api, ui }).ready;
  for (const card of ui.cards) {
    assert.equal(card.errors.length, 1);
    assert.match(card.errors[0], /读取失败：权限被拒绝/);
    assert.deepEqual(card.lastButton(), { text: "导出到 keys.env", disabled: true });
  }
});

// ---- 导出链路（直写 keys.env）----

test("导出成功：发送 write_key，按钮变「已导出 ✓」，状态行只显示路径", async () => {
  const { ui, api } = await launch({ cookiesByDomain: defaultCookies() });
  const bili = cardOf(ui, "bili");

  await bili.click();

  assert.equal(api.calls.native.length, 1);
  assert.equal(api.calls.native[0].hostName, "com.raymin.cookie_exporter");
  assert.deepEqual(api.calls.native[0].message, {
    cmd: "write_key",
    target: "BILI_COOKIE",
    value: buildCookieHeader(BILI_LOGGED_IN),
  });
  // 过程态：导出中禁用，随后成功恢复。
  assert.deepEqual(bili.buttons[bili.buttons.length - 2], { text: "导出中…", disabled: true });
  assert.deepEqual(bili.lastButton(), { text: "已导出 ✓", disabled: false });
  // 状态行 = 纯路径片段，不含「替换/新增」字样。
  assert.deepEqual(bili.lastStatus().segments, [KEYS_ENV_PATH]);
  assert.equal(bili.lastStatus().kind, "ok");
  assert.ok(!bili.lastStatus().segments.join("").includes("替换"));
  assert.deepEqual(api.calls.savedSiteIds, ["bili"]);
});

test("host 未安装：状态行含可复制的安装命令，按钮恢复", async () => {
  const { ui, api } = await launch({
    cookiesByDomain: defaultCookies(),
    nativeError: new Error("Specified native host not found."),
  });
  const xhs = cardOf(ui, "xhs");
  await xhs.click();
  // 调用确实发出过（mock 先记录再抛错），只是 host 未就绪而失败。
  assert.equal(api.calls.native.length, 1);
  const status = xhs.lastStatus();
  assert.equal(status.kind, "err");
  assert.deepEqual(status.segments, [
    "本机助手未安装：",
    { command: INSTALL_CMD },
    "（需完全退出 Chrome 重开）",
  ]);
  assert.deepEqual(xhs.lastButton(), { text: "导出到 keys.env", disabled: false });
  assert.deepEqual(api.calls.savedSiteIds, []);
});

test("host 回报业务失败：显示原始错误", async () => {
  const { ui } = await launch({
    cookiesByDomain: defaultCookies(),
    nativeReply: { ok: false, error: "target 不在白名单" },
  });
  const xhs = cardOf(ui, "xhs");
  await xhs.click();
  const status = xhs.lastStatus();
  assert.equal(status.kind, "err");
  assert.deepEqual(status.segments, ["导出失败：", "target 不在白名单"]);
});

test("未登录但有 cookie：仍然可以导出（徽标只是提示，不拦截导出）", async () => {
  const { ui, api } = await launch({
    cookiesByDomain: { ".xiaohongshu.com": XHS_LOGGED_OUT, ".bilibili.com": [] },
  });
  await cardOf(ui, "xhs").click();
  assert.equal(api.calls.native.length, 1);
});

test("空 cookie 站点点导出：给出提示且不触发本机助手", async () => {
  const { ui, api } = await launch({
    cookiesByDomain: { ".xiaohongshu.com": [], ".bilibili.com": [] },
  });
  await ui.cards[0].click();
  assert.deepEqual(api.calls.native, []);
  assert.equal(ui.cards[0].lastStatus().kind, "warn");
  assert.match(ui.cards[0].lastStatus().segments.join(""), /没有可导出的 Cookie/);
});

// ---- ⓘ 路径显示 ----

test("ⓘ 点击展开路径，再点收起（toggle）", async () => {
  const { ui } = await launch({ cookiesByDomain: defaultCookies() });
  const xhs = cardOf(ui, "xhs");
  xhs.info();
  xhs.info();
  xhs.info();
  assert.deepEqual(xhs.infoVisibles, [true, false, true]);
});

// ---- storage 记忆 ----

test("上次导出的站点会被标记「上次使用」，其余站点不标记", async () => {
  const { ui } = await launch({
    cookiesByDomain: defaultCookies(),
    lastSiteId: "xhs",
  });
  assert.equal(cardOf(ui, "xhs").lastUsedCount, 1);
  assert.equal(ui.cards.reduce((a, c) => a + c.lastUsedCount, 0), 1);
});

test("storage 里是未知/空站点时不标记任何卡片", async () => {
  for (const lastSiteId of [null, "obsolete-site"]) {
    const { ui } = await launch({
      cookiesByDomain: defaultCookies(),
      lastSiteId,
    });
    assert.equal(ui.cards.reduce((a, c) => a + c.lastUsedCount, 0), 0);
  }
});

test("storage 读取失败不影响初始化", async () => {
  const ui = createMockUi();
  const api = createMockApi({ cookiesByDomain: defaultCookies() });
  api.getLastSiteId = async () => {
    throw new Error("storage 不可用");
  };
  await createApp({ api, ui }).ready;
  const xhs = cardOf(ui, "xhs");
  assert.deepEqual(xhs.lastBadge(), { loggedIn: true, count: XHS_LOGGED_IN.length });
});
