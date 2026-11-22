"""config 单测：runs_dir() 三级优先级（环境变量 > 仓库锚定 > ~/.cache 回退）。"""

from bili_digest import config


def test_runs_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_DIGEST_RUNS_DIR", str(tmp_path / "my-runs"))
    assert config.runs_dir() == tmp_path / "my-runs"


def test_runs_dir_anchored_to_repo(monkeypatch):
    """默认（无环境变量）：从包位置上溯找到 bili/ 仓库根（AGENTS.md 标记）→ 仓库级 runs/。"""
    monkeypatch.delenv("BILI_DIGEST_RUNS_DIR", raising=False)
    d = config.runs_dir()
    assert d.name == "runs"
    assert (d.parent / "AGENTS.md").exists() or (d.parent / ".git").exists()


def test_runs_dir_anchor_from_markers(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "AGENTS.md").write_text("marker", encoding="utf-8")
    deep = tmp_path / "repo" / "a" / "b" / "c.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("", encoding="utf-8")
    assert config.runs_dir(deep) == tmp_path / "repo" / "runs"

    git_root = tmp_path / "repo2"  # .git 也可以是标记
    git_root.mkdir()
    (git_root / ".git").mkdir()
    assert config.runs_dir(git_root / "x.py") == git_root / "runs"


def test_runs_dir_fallback_to_cache(monkeypatch, tmp_path, capsys):
    """找不到仓库根 → 回退 ~/.cache/bili-digest/runs 并 stderr 提示。"""
    monkeypatch.delenv("BILI_DIGEST_RUNS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    d = config.runs_dir(tmp_path / "lonely" / "x.py")
    assert d == tmp_path / ".cache" / "bili-digest" / "runs"
    err = capsys.readouterr().err
    assert "回退" in err and "BILI_DIGEST_RUNS_DIR" in err


# ---------------------------------------------------------------- S7.2：可选覆盖变量

def test_summarize_provider_resolution(monkeypatch):
    from bili_digest import config
    monkeypatch.setattr(config, "get_key", lambda name: None)
    assert config.summarize_provider() is None  # 未配置
    monkeypatch.setattr(config, "get_key",
                        lambda name: "deepseek" if name == "SUMMARIZE_PROVIDER" else None)
    assert config.summarize_provider() == "deepseek"
    monkeypatch.setattr(config, "get_key",
                        lambda name: " Zhipu " if name == "SUMMARIZE_PROVIDER" else None)
    assert config.summarize_provider() == "zhipu"  # 宽松归一
    monkeypatch.setattr(config, "get_key",
                        lambda name: "openai" if name == "SUMMARIZE_PROVIDER" else None)
    assert config.summarize_provider() is None  # 非法值忽略
