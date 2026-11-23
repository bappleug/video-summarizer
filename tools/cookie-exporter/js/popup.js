// popup.js — 弹窗编排逻辑。
// createApp：纯编排（api / ui 均由外部注入），node --test 用 mock 覆盖；
// createDomUi：浏览器 DOM 实现，只在真实弹窗页面由 main.js 调用。
// 1.2.0：直写 keys.env 是唯一导出方式（EXPORTERS[0]），无导出方式选择 UI；
// 加回多种方式时在 exporters.js 扩充 EXPORTERS 并恢复此处的选择编排。
// Cookie 值只流入 exporter.run，不落任何日志。

import { SITES } from "./sites.js";
import { EXPORTERS, KEYS_ENV_PATH } from "./exporters.js";
import { buildCookieHeader, isLoggedIn } from "./lib.js";

const EXPORTER = EXPORTERS[0];

// ui 约定：createCard(site) -> card，card 方法见下方 cardHandle。
export function createApp({ api, ui }) {
  const entries = new Map(); // site.id -> { site, card, cookies, infoVisible }
  const pending = [];

  for (const site of SITES) {
    const card = ui.createCard(site);
    const entry = { site, card, cookies: [], infoVisible: false };
    entries.set(site.id, entry);
    card.setButton(EXPORTER.buttonLabel, true);
    card.onExport(() => handleExport(entry));
    // ⓘ：显示/收起 keys.env 路径（点击切换，不用 hover）。
    card.onInfo(() => {
      entry.infoVisible = !entry.infoVisible;
      card.showInfoPath(entry.infoVisible);
    });
    pending.push(refresh(entry));
  }

  // 「上次使用」标记：storage 记住上次导出的站点，失败不影响主流程。
  pending.push(
    api
      .getLastSiteId()
      .then((id) => {
        const entry = entries.get(id);
        if (entry) entry.card.markLastUsed();
      })
      .catch(() => {}),
  );

  async function refresh(entry) {
    const { site, card } = entry;
    try {
      const cookies = await api.getCookies(site.domain);
      entry.cookies = cookies;
      card.setBadge(isLoggedIn(site, cookies), cookies.length);
      card.setButton(EXPORTER.buttonLabel, cookies.length === 0);
    } catch (err) {
      card.setError(`读取失败：${err.message}`);
      card.setButton(EXPORTER.buttonLabel, true);
    }
  }

  async function handleExport(entry) {
    const { site, card, cookies } = entry;
    if (!cookies.length) {
      card.setStatus(["当前站点没有可导出的 Cookie"], "warn");
      return;
    }
    card.setButton("导出中…", true);
    try {
      const segments = await EXPORTER.run(buildCookieHeader(cookies), site, api);
      card.setButton("已导出 ✓", false);
      card.setStatus(segments, "ok");
      api.saveLastSiteId(site.id).catch(() => {});
    } catch (err) {
      card.setButton(EXPORTER.buttonLabel, false);
      const segments = Array.isArray(err && err.segments)
        ? err.segments
        : ["导出失败：", String(err.message)];
      card.setStatus(segments, "err");
    }
  }

  // ready：初始 cookie 检测与标记全部完成，供测试等待。
  return { ready: Promise.all(pending) };
}

// 浏览器 DOM 版 ui。cardsEl：popup.html 里的 #cards 容器。
export function createDomUi(cardsEl) {
  return {
    createCard(site) {
      const el = document.createElement("section");
      el.className = "card";
      el.innerHTML = `
        <header class="card-head">
          <span class="site-label"></span>
          <span class="badge">检测中…</span>
          <button type="button" class="info" aria-label="查看 keys.env 路径">ⓘ</button>
        </header>
        <code class="path" hidden></code>
        <div class="actions">
          <button type="button" class="export"></button>
          <span class="last-used" hidden>上次使用</span>
        </div>
        <p class="status" hidden></p>`;
      // 站点配置一律走 textContent，不作为 HTML 注入。
      el.querySelector(".site-label").textContent = site.label;
      const pathEl = el.querySelector(".path");
      pathEl.textContent = KEYS_ENV_PATH;
      const badge = el.querySelector(".badge");
      const infoButton = el.querySelector(".info");
      const button = el.querySelector(".export");
      const status = el.querySelector(".status");
      const lastUsed = el.querySelector(".last-used");
      cardsEl.appendChild(el);

      const cardHandle = {
        onExport(fn) {
          button.addEventListener("click", () => fn());
        },
        onInfo(fn) {
          infoButton.addEventListener("click", () => fn());
        },
        showInfoPath(visible) {
          pathEl.hidden = !visible;
        },
        setBadge(loggedIn, count) {
          badge.textContent = loggedIn
            ? `已登录 · 共 ${count} 个 Cookie`
            : `未登录 · 共 ${count} 个 Cookie`;
          badge.classList.toggle("ok", loggedIn);
          badge.classList.toggle("off", !loggedIn);
        },
        setError(message) {
          badge.textContent = message;
          badge.className = "badge err";
        },
        setButton(text, disabled) {
          button.textContent = text;
          button.disabled = disabled;
        },
        // segments：字符串或 { command } 组成的数组（字符串归一为数组），
        // command 片段渲染为可选中复制的等宽样式。
        setStatus(segments, kind) {
          status.textContent = "";
          const parts = Array.isArray(segments) ? segments : [segments];
          for (const part of parts) {
            if (typeof part === "string") {
              status.appendChild(document.createTextNode(part));
            } else {
              const code = document.createElement("code");
              code.className = "status-cmd";
              code.textContent = part.command;
              status.appendChild(code);
            }
          }
          status.className = `status ${kind}`;
          status.hidden = false;
        },
        markLastUsed() {
          lastUsed.hidden = false;
        },
      };
      return cardHandle;
    },
  };
}
