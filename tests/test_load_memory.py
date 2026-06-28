from types import SimpleNamespace

from harness.tools.load_memory import LoadMemoryTool
from harness.tools.registry import build_registry


def _fact(ws, name, body="a remembered thing"):
    d = ws / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: d\ntype: feedback\n---\n{body}\n",
        encoding="utf-8")


def _env(ws):
    return SimpleNamespace(config=SimpleNamespace(cwd=str(ws)))


def test_load_memory_returns_body(tmp_path):
    _fact(tmp_path, "user-terse", "TERSE BODY")
    out = LoadMemoryTool(tmp_path).execute({"memory_name": "user-terse"}, _env(tmp_path))
    assert out["returncode"] == 0 and "TERSE BODY" in out["output"]


def test_load_memory_unknown_lists_available(tmp_path):
    _fact(tmp_path, "user-terse")
    out = LoadMemoryTool(tmp_path).execute({"memory_name": "ghost"}, _env(tmp_path))
    assert out["returncode"] == 1 and "user-terse" in out["output"]


def test_load_memory_root_memory_md_by_name(tmp_path):
    (tmp_path / "MEMORY.md").write_text("INDEX BODY", encoding="utf-8")
    out = LoadMemoryTool(tmp_path).execute({"memory_name": "MEMORY"}, _env(tmp_path))
    assert out["returncode"] == 0 and "INDEX BODY" in out["output"]


def test_load_memory_duplicate_is_short_circuited(tmp_path):
    _fact(tmp_path, "x", "X BODY")
    env = _env(tmp_path)
    env._loaded_memories = set()
    tool = LoadMemoryTool(tmp_path)
    tool.execute({"memory_name": "x"}, env)
    out2 = tool.execute({"memory_name": "x"}, env)
    assert "already loaded" in out2["output"].lower() and "X BODY" not in out2["output"]


def test_loaded_set_reset_between_turns(tmp_path):
    _fact(tmp_path, "x", "X BODY")
    env = _env(tmp_path)
    tool = LoadMemoryTool(tmp_path)
    env._loaded_memories = set()
    tool.execute({"memory_name": "x"}, env)
    env._loaded_memories = set()          # new turn
    out = tool.execute({"memory_name": "x"}, env)
    assert "X BODY" in out["output"]


def test_load_memory_rejects_path_traversal(tmp_path):
    _fact(tmp_path, "x", "X BODY")
    tool = LoadMemoryTool(tmp_path)
    for evil in ["../x", "../../etc/passwd", "/etc/passwd", "a/b"]:
        out = tool.execute({"memory_name": evil}, _env(tmp_path))
        assert out["returncode"] == 1, f"{evil!r} must be rejected"
        assert "passwd" not in out["output"] or "Unknown" in out["output"]


def test_load_memory_two_workspaces_isolated(tmp_path):
    a = tmp_path / "a"; _fact(a, "shared", "A-ONLY")
    b = tmp_path / "b"; _fact(b, "shared", "B-ONLY")
    out_a = LoadMemoryTool(a).execute({"memory_name": "shared"}, _env(a))
    out_b = LoadMemoryTool(b).execute({"memory_name": "shared"}, _env(b))
    assert "A-ONLY" in out_a["output"] and "B-ONLY" not in out_a["output"]
    assert "B-ONLY" in out_b["output"] and "A-ONLY" not in out_b["output"]


# ---------------------------------------------------------------- registry wiring

def test_registry_no_memory_tool_without_root():
    assert "load_memory" not in [t.name for t in build_registry()]


def test_registry_appends_load_memory_with_populated_root(tmp_path):
    _fact(tmp_path, "x")                      # workspace HAS memory content
    names = [t.name for t in build_registry(memory_root=tmp_path)]
    assert "load_memory" in names


def test_registry_no_memory_tool_for_empty_workspace(tmp_path):
    # Codex finding #1: an empty/absent-memory workspace must NOT add a dead
    # load_memory tool (byte-identical no-op when there's nothing to recall).
    (tmp_path).mkdir(exist_ok=True)
    names = [t.name for t in build_registry(memory_root=tmp_path)]
    assert "load_memory" not in names


def test_registry_both_skill_and_memory(tmp_path):
    _fact(tmp_path, "x")                      # memory needs content to register
    names = [t.name for t in build_registry(skill_roots=[tmp_path], memory_root=tmp_path)]
    assert "load_skill" in names and "load_memory" in names
