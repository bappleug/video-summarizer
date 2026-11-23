// main.js — 弹窗入口：装配真实 chrome API 与 DOM，启动应用。
import { createApp, createDomUi } from "./popup.js";
import { chromeApi } from "./chrome-api.js";

createApp({
  api: chromeApi,
  ui: createDomUi(document.getElementById("cards")),
});
