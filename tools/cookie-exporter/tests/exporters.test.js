// exporters.test.js — 导出方式注册表与 native exporter 的单测（api 全 mock 注入）。
// 1.2.0：EXPORTERS 只含 native；run 的返回/抛错都走「状态行片段 segments」约定。

import test from "node:test";
import assert from "node:assert/strict";

import {
  EXPORTERS,
  nativeExporter,
  NATIVE_HOST_NAME,
  KEYS_ENV_PATH,
  INSTALL_CMD,
} from "../js/exporters.js";
import { SITES } from "../js/sites.js";

const XHS = SITES.find((s) => s.id === "xhs");
const HEADER = "a1=fake-a1; web_session=fake-session";

// mock api：记录 Native Messaging 调用，可定制回包/注入失败。
function createMockApi({ nativeReply = { ok: true, replaced: false }, nativeError = null } = {}) {
  const calls = { native: [] };
  return {
    calls,
    sendNativeMessage: async (hostName, message) => {
      calls.native.push({ hostName, message });
      if (nativeError) throw nativeError;
      return nativeReply;
    },
  };
}

// ---- 注册表结构 ----

test("注册表：1.2.0 只含 native 一个 exporter，结构完整", () => {
  assert.equal(EXPORTERS.length, 1);
  assert.equal(EXPORTERS[0], nativeExporter);
  assert.equal(nativeExporter.id, "native");
  assert.ok(typeof nativeExporter.label === "string" && nativeExporter.label.length > 0);
  assert.equal(nativeExporter.buttonLabel, "导出到 keys.env");
  assert.equal(typeof nativeExporter.run, "function");
});

test("host 名与展示路径常量与 native/ 侧约定一致", () => {
  assert.equal(NATIVE_HOST_NAME, "com.raymin.cookie_exporter");
  assert.equal(KEYS_ENV_PATH, "~/.config/ai-tools/bili-digest/keys.env");
  assert.equal(INSTALL_CMD, "bash tools/cookie-exporter/native/install-host.sh");
});

// ---- native exporter ----

test("成功：按协议发送 write_key，返回纯路径片段（replaced 不上 UI）", async () => {
  for (const replaced of [false, true]) {
    const api = createMockApi({ nativeReply: { ok: true, replaced } });
    const segments = await nativeExporter.run(HEADER, XHS, api);
    assert.deepEqual(api.calls.native, [
      {
        hostName: NATIVE_HOST_NAME,
        message: { cmd: "write_key", target: "XHS_COOKIE", value: HEADER },
      },
    ]);
    assert.deepEqual(segments, [KEYS_ENV_PATH]);
  }
});

test("host 回报业务失败（如白名单拒绝）时抛原始错误", async () => {
  const api = createMockApi({ nativeReply: { ok: false, error: "target 不在白名单" } });
  await assert.rejects(() => nativeExporter.run(HEADER, XHS, api), /target 不在白名单/);
});

test("host 未安装：抛带 segments 的错误，含可复制的安装命令与重开提示", async () => {
  const api = createMockApi({
    nativeError: new Error("Specified native host not found."),
  });
  await assert.rejects(() => nativeExporter.run(HEADER, XHS, api), (err) => {
    assert.ok(Array.isArray(err.segments), "err.segments 应为数组");
    assert.equal(err.segments[0], "本机助手未安装：");
    assert.deepEqual(err.segments[1], { command: INSTALL_CMD });
    assert.equal(err.segments[2], "（需完全退出 Chrome 重开）");
    return true;
  });
});
