"""Persisted agent -> model selection (`done.conf`).

A small, self-contained reader/writer for the TOML file at
`paths.config_dir()/done.conf`. Reads with stdlib `tomllib`; writes with a tiny
hand-rolled serializer (the schema is flat: a top-level `schema_version` plus
`[agents.<key>]` tables of string scalars), so there is no write-only TOML
dependency. Knows nothing about the TUI or ACP agent.

Reserved key `default` is the always-present primary agent (no `name`). Future
agents are uuid-keyed and carry a human `name`; this module round-trips them but
nothing here selects them yet.

Persistence is best-effort: a missing/empty/corrupt file yields {}, never raises
into the boot path; callers handle write failures (see save_default)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from harness import paths

SCHEMA_VERSION = 1
RESERVED_KEY = "default"


@dataclass(frozen=True)
class AgentConfig:
    backend: str            # "mock" | "vibeproxy"
    model: str              # model string, e.g. "gpt-5.4"
    name: str | None = None  # None for the reserved default; set for uuid agents
    yolo_pinned: bool = False  # persisted "always launch in YOLO" (default only)


def conf_path() -> Path:
    """Absolute path to done.conf under the XDG config dir (not created here)."""
    return paths.config_dir() / "done.conf"


def load() -> dict[str, AgentConfig]:
    """All agents keyed by their table key. Returns {} if the file is missing,
    empty, or unparseable. Individual agent tables missing `backend` or `model`
    are skipped (not fatal)."""
    path = conf_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    agents_raw = data.get("agents")
    if not isinstance(agents_raw, dict):
        return {}
    out: dict[str, AgentConfig] = {}
    for key, table in agents_raw.items():
        if not isinstance(table, dict):
            continue
        backend = table.get("backend")
        model = table.get("model")
        if not isinstance(backend, str) or not isinstance(model, str):
            continue
        name = table.get("name")
        pinned = table.get("yolo_pinned")
        out[key] = AgentConfig(
            backend=backend,
            model=model,
            name=name if isinstance(name, str) else None,
            yolo_pinned=pinned if isinstance(pinned, bool) else False,
        )
    return out


def load_default() -> AgentConfig | None:
    """The reserved [agents.default] entry, or None when absent/unreadable."""
    return load().get(RESERVED_KEY)


def _quote(value: str) -> str:
    """Serialize a Python str as a TOML basic string (escape \\ and ")."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _serialize(agents: dict[str, AgentConfig]) -> str:
    """Render the flat schema: top-level schema_version then one [agents.<key>]
    table per agent (name only when set). Deterministic key order: the reserved
    default first, then the rest sorted, so diffs stay stable."""
    lines = [f"schema_version = {SCHEMA_VERSION}", ""]
    ordered = ([RESERVED_KEY] if RESERVED_KEY in agents else []) + sorted(
        k for k in agents if k != RESERVED_KEY
    )
    for key in ordered:
        cfg = agents[key]
        lines.append(f"[agents.{key}]")
        if cfg.name is not None:
            lines.append(f"name = {_quote(cfg.name)}")
        lines.append(f"backend = {_quote(cfg.backend)}")
        lines.append(f"model = {_quote(cfg.model)}")
        if cfg.yolo_pinned:
            lines.append("yolo_pinned = true")
        lines.append("")
    return "\n".join(lines)


def update_default(
    *,
    backend: str | None = None,
    model: str | None = None,
    yolo_pinned: bool | None = None,
) -> None:
    """Upsert [agents.default], overlaying ONLY the kwargs passed (None = leave
    unchanged). Preserves untouched default fields and every other agent table.
    Writes atomically (temp file + os.replace) under a created config dir.
    Best-effort: callers that must not fail on I/O errors should guard the call.

    This is the merge-safe write: changing the model never clears a pin, and
    pinning never clears the model — each call site touches only its own fields."""
    agents = load()
    cur = agents.get(RESERVED_KEY)
    base_backend = cur.backend if cur is not None else ""
    base_model = cur.model if cur is not None else ""
    base_pinned = cur.yolo_pinned if cur is not None else False
    agents[RESERVED_KEY] = AgentConfig(             # default carries no name
        backend=base_backend if backend is None else backend,
        model=base_model if model is None else model,
        yolo_pinned=base_pinned if yolo_pinned is None else yolo_pinned,
    )
    text = _serialize(agents)

    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_default(cfg: AgentConfig) -> None:
    """Upsert the default's backend+model, preserving its yolo_pinned and every
    other agent table. Thin wrapper over update_default (kept for the set_model
    call site + tests). NOTE: deliberately ignores cfg.yolo_pinned — set_model
    passes a default-constructed cfg and must not clear an existing pin."""
    update_default(backend=cfg.backend, model=cfg.model)


def yolo_pinned() -> bool:
    """Whether the persisted default is pinned to launch in YOLO. False when the
    default is absent or the file is unreadable."""
    cur = load_default()
    return cur.yolo_pinned if cur is not None else False
