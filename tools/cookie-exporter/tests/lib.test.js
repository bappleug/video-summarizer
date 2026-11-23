// lib.test.js — 纯逻辑单测：Cookie 拼装、登录态判定、站点配置、
// manifest 权限白名单。全部用伪造的 cookie 名值，不涉及真实凭据。

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { SITES } from "../js/sites.js";
import { buildCookieHeader, isLoggedIn } from "../js/lib.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// ---- buildCookieHeader ----

test("拼装为标准 Cookie 头格式：'; ' 分隔、无尾随分隔符", () => {
  const cookies = [
    { name: "a1", value: "fake-a1" },
    { name: "web_session", value: "fake-session" },
  ];
  assert.equal(buildCookieHeader(cookies), "a1=fake-a1; web_session=fake-session");
});

test("单个 cookie 不带分隔符；空数组得到空串", () => {
  assert.equal(buildCookieHeader([{ name: "SESSDATA", value: "fake" }]), "SESSDATA=fake");
  assert.equal(buildCookieHeader([]), "");
});

test("保持输入顺序，cookie 值原样保留（不编码不转义）", () => {
  const cookies = [
    { name: "b", value: "x%2Fy+z" },
    { name: "a", value: "1" },
  ];
  assert.equal(buildCookieHeader(cookies), "b=x%2Fy+z; a=1");
});

// ---- isLoggedIn ----

test("关键 cookie 全部存在 -> 已登录", () => {
  const site = SITES.find((s) => s.id === "xhs");
  const cookies = [
    { name: "a1", value: "fake" },
    { name: "web_session", value: "fake" },
    { name: "webId", value: "fake" },
  ];
  assert.equal(isLoggedIn(site, cookies), true);
});

test("缺少任一关键 cookie -> 未登录（小红书未登录时 a1 仍在，不能误判）", () => {
  const site = SITES.find((s) => s.id === "xhs");
  const cookies = [
    { name: "a1", value: "fake" },
    { name: "webId", value: "fake" },
  ];
  assert.equal(isLoggedIn(site, cookies), false);
});

test("无任何 cookie -> 未登录", () => {
  const site = SITES.find((s) => s.id === "bili");
  assert.equal(isLoggedIn(site, []), false);
});

// ---- 站点配置（单一事实源的结构保障）----

test("站点配置结构完整且 id 唯一", () => {
  assert.ok(SITES.length >= 2);
  const ids = new Set();
  for (const s of SITES) {
    assert.match(s.id, /^[a-z][a-z0-9-]*$/, `id 不合规: ${s.id}`);
    assert.ok(!ids.has(s.id), `id 重复: ${s.id}`);
    ids.add(s.id);
    assert.ok(s.domain.startsWith("."), `domain 须以点开头（站点级）: ${s.domain}`);
    assert.ok(Array.isArray(s.keyCookies) && s.keyCookies.length > 0, "keyCookies 非空");
    assert.match(s.target, /^[A-Z][A-Z0-9_]*$/, `target 须为 env 风格大写字段: ${s.target}`);
    assert.ok(typeof s.label === "string" && s.label.length > 0);
  }
});

test("内置两站点：小红书与哔哩哔哩", () => {
  const xhs = SITES.find((s) => s.id === "xhs");
  const bili = SITES.find((s) => s.id === "bili");
  assert.equal(xhs.domain, ".xiaohongshu.com");
  assert.deepEqual(xhs.keyCookies, ["web_session", "a1"]);
  assert.equal(xhs.target, "XHS_COOKIE");
  assert.equal(bili.domain, ".bilibili.com");
  assert.deepEqual(bili.keyCookies, ["SESSDATA", "bili_jct"]);
  assert.equal(bili.target, "BILI_COOKIE");
});

// ---- manifest 权限白名单（多余权限即不合格）----

const manifest = JSON.parse(readFileSync(path.join(root, "manifest.json"), "utf8"));

test("manifest 基本面：MV3 + popup", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.action.default_popup, "popup.html");
});

test("permissions 与白名单逐项一致：cookies / nativeMessaging / storage", () => {
  assert.deepEqual([...manifest.permissions].sort(), [
    "cookies",
    "nativeMessaging",
    "storage",
  ]);
});

test("host_permissions 与白名单逐项一致，且与 sites.js 域名同步", () => {
  const whitelist = ["*://*.xiaohongshu.com/*", "*://*.bilibili.com/*"];
  assert.deepEqual([...manifest.host_permissions].sort(), [...whitelist].sort());
  const fromSites = SITES.map((s) => `*://*${s.domain}/*`).sort();
  assert.deepEqual([...manifest.host_permissions].sort(), fromSites);
});

test("manifest.key 存在且为可解码的 base64（锁定稳定扩展 ID 的公钥 DER）", () => {
  assert.equal(typeof manifest.key, "string");
  const der = Buffer.from(manifest.key, "base64");
  assert.ok(der.length > 250, "RSA-2048 公钥 DER 应约 294 字节");
});
