"""运行配置：环境默认值、模型选择与 API key（keys.env / 环境变量）。

HF_ENDPOINT 默认走 hf-mirror（hf.co 直连不通，见 docs/research/01）。
必须在 mlx_whisper / huggingface_hub 首次联网前 import 本模块。
"""

import os
from pathlib import Path

from .errors import log

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# mlx-whisper 模型（HF repo id；已缓存在 ~/.cache/huggingface，勿重复下载）
MLX_MODEL = os.environ.get("BILI_DIGEST_MLX_MODEL", "mlx-community/whisper-large-v3-turbo")

# ---------------------------------------------------------------- API keys
# key 来源优先级：环境变量 > ~/.config/ai-tools/bili-digest/keys.env。
# 文件在仓库外、0600，值绝不打印、绝不入库。

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
    "ai-tools",
    "bili-digest",
)
KEYS_ENV_PATH = os.path.join(CONFIG_DIR, "keys.env")

_KEYS_CACHE = None


def load_keys(path=None):
    """解析 keys.env（KEY=value 行，# 注释），返回 dict；文件不存在返回空 dict。"""
    global _KEYS_CACHE
    if path is None and _KEYS_CACHE is not None:
        return _KEYS_CACHE
    keys = {}
    target = os.path.expanduser(path or KEYS_ENV_PATH)
    if os.path.exists(target):
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name, value = name.strip(), value.strip()
                if name and value:
                    keys[name] = value
    if path is None:
        _KEYS_CACHE = keys
    return keys


def get_key(name):
    """取 API key / 覆盖变量（环境变量优先）；没有返回 None。"""
    return os.environ.get(name) or load_keys().get(name)


# ---------------------------------------------------------------- 可选覆盖变量（S7.2）

def summarize_provider():
    """SUMMARIZE_PROVIDER（zhipu|deepseek，keys.env/环境变量）→ --provider 的持久化默认。
    非法值告警忽略（回退 CLI 默认 zhipu）。"""
    value = (get_key("SUMMARIZE_PROVIDER") or "").strip().lower()
    if not value:
        return None
    if value not in ("zhipu", "deepseek", "siliconflow"):
        log(f"SUMMARIZE_PROVIDER 非法（{value!r}，支持 zhipu|deepseek|siliconflow），已忽略")
        return None
    return value


# ---------------------------------------------------------------- 产物目录
# run 目录根（runs/<bvid>/<run-id>/），与 cwd 无关：
# 环境变量 BILI_DIGEST_RUNS_DIR > 包位置上溯锚定的仓库级 runs/ > ~/.cache/bili-digest/runs（回退）


def _find_repo_root(start) -> Path | None:
    """从 start 文件逐级向上找仓库根（目录内有 AGENTS.md 或 .git 标记）。"""
    for parent in Path(start).resolve().parents:
        if (parent / "AGENTS.md").exists() or (parent / ".git").exists():
            return parent
    return None


def runs_dir(start=None) -> Path:
    """run 目录根（优先级见上）；start 仅供测试注入锚定起点。"""
    env = os.environ.get("BILI_DIGEST_RUNS_DIR")
    if env:
        return Path(env)
    root = _find_repo_root(start or __file__)
    if root is not None:
        return root / "runs"
    fallback = Path.home() / ".cache" / "bili-digest" / "runs"
    log(f"未找到仓库根（AGENTS.md / .git），runs 目录回退 {fallback}；"
        "可用环境变量 BILI_DIGEST_RUNS_DIR 指定")
    return fallback


# ---------------------------------------------------------------- 视频缓存目录
# 视频轨缓存放在 Agent 当前工作目录下（<cwd>/.bili-digest-cache/videos/），
# 让用户按工作项自行清理视频/转录产物；MCP server 的 cwd 即 WorkBuddy 工作目录。
# 优先级：显式 work_dir 参数 > 环境变量 BILI_DIGEST_CACHE_DIR > cwd/.bili-digest-cache。
# 缓存键由 video.py 按 平台+视频ID+流ID 生成，命中则跳过下载。

CACHE_DIRNAME = ".bili-digest-cache"  # 工作目录下的缓存文件夹名（用户可见，便于手动清理）


def videos_cache_dir(work_dir=None) -> Path:
    """视频轨缓存根目录：默认 <Agent工作目录>/.bili-digest-cache/videos。

    work_dir 显式指定时优先（MCP 的 work_dir / CLI 产物目录）；
    环境变量 BILI_DIGEST_CACHE_DIR 为逃生口；最后回退当前 cwd。
    """
    env = os.environ.get("BILI_DIGEST_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    base = Path(work_dir).expanduser() if work_dir else Path.cwd()
    return base / CACHE_DIRNAME / "videos"
