// exporters.js — 导出方式注册表（插件层）。
// 1.2.0：直写 keys.env 是唯一导出方式，EXPORTERS 只含 nativeExporter。
// 注册表结构保留（插件壳不变）：未来要加回剪贴板/下载等方式，实现对象加入
// EXPORTERS 并在 popup.js 恢复选择 UI 即可。
//
// exporter 约定：{ id, label, buttonLabel, run(cookieHeader, site, api) }
//   run 成功：返回状态行片段 segments——字符串或 { command } 对象组成的数组，
//            由弹窗状态行渲染（字符串原样、command 渲染为可选中复制的命令样式）；
//   run 失败：抛 Error；若是「给用户看的可操作错误」，在 err.segments 上放同样的片段数组。

// Native Messaging 本机助手的名字（与 native/com.raymin.cookie_exporter.json 一致）。
export const NATIVE_HOST_NAME = "com.raymin.cookie_exporter";

// 展示用的 keys.env 路径（真正落盘的一侧是 native/host.py，两处保持一致）。
export const KEYS_ENV_PATH = "~/.config/ai-tools/bili-digest/keys.env";

// host 安装命令。扩展无法感知仓库在本机的绝对位置，只能给仓库相对路径，
// 需在 ai-tools 仓库根目录（或自行补全路径）运行。
export const INSTALL_CMD = "bash cookie-exporter/native/install-host.sh";

export const nativeExporter = {
  id: "native",
  label: "直写 keys.env",
  buttonLabel: "导出到 keys.env",
  async run(cookieHeader, site, api) {
    let reply;
    try {
      reply = await api.sendNativeMessage(NATIVE_HOST_NAME, {
        cmd: "write_key",
        target: site.target,
        value: cookieHeader,
      });
    } catch (err) {
      // Chrome 对「host 未安装/未注册」的报错不含可操作信息，换成一行人话 + 可复制的安装命令。
      const friendly = new Error("本机助手未安装或未就绪");
      friendly.segments = [
        "本机助手未安装：",
        { command: INSTALL_CMD },
        "（需完全退出 Chrome 重开）",
      ];
      throw friendly;
    }
    if (!reply || reply.ok !== true) {
      throw new Error(reply && reply.error ? reply.error : "本机助手返回未知错误");
    }
    // 用户只关心写到哪了；新增还是替换（host 回包的 replaced 字段）不上 UI。
    return [KEYS_ENV_PATH];
  },
};

export const EXPORTERS = [nativeExporter];
