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

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from harness import paths

logger = logging.getLogger("harness.config")

SCHEMA_VERSION = 1
RESERVED_KEY = "default"


@dataclass(frozen=True)
class AgentConfig:
    backend: str            # "mock" | "vibeproxy"
    model: str              # model string, e.g. "gpt-5.4"
    name: str | None = None  # None for the reserved default; set for uuid agents
    yolo_pinned: bool = False  # persisted "always launch in YOLO" (default only)
    compress_aware: bool = True  # persisted compress-aware mode flag (default ON)


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
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        # A file that EXISTS but won't parse is a real problem: every persisted
        # model/yolo pin silently resolves to {} (defaults). A missing file
        # (OSError above) is normal first-run and stays quiet; a corrupt one warns.
        logger.warning("done.conf at %s is unparseable (%s); ignoring all persisted "
                       "agent config this session", path, e)
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
        ca = table.get("compress_aware")
        out[key] = AgentConfig(
            backend=backend,
            model=model,
            name=name if isinstance(name, str) else None,
            yolo_pinned=pinned if isinstance(pinned, bool) else False,
            compress_aware=ca if isinstance(ca, bool) else True,
        )
    return out


def load_agent(persona_id: str) -> AgentConfig | None:
    """The [agents.<persona_id>] entry, or None when absent/unreadable."""
    return load().get(persona_id)


def load_default() -> AgentConfig | None:
    """The reserved [agents.default] entry, or None when absent/unreadable."""
    return load_agent(RESERVED_KEY)


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
        if not cfg.compress_aware:
            lines.append("compress_aware = false")
        lines.append("")
    return "\n".join(lines)


def update_agent(
    persona_id: str,
    *,
    backend: str | None = None,
    model: str | None = None,
    yolo_pinned: bool | None = None,
    compress_aware: bool | None = None,
) -> None:
    """Upsert [agents.<persona_id>], overlaying ONLY the kwargs passed (None =
    leave unchanged). Preserves untouched fields and every other agent table.
    Writes atomically (temp file + os.replace) under a created config dir.
    Best-effort: callers that must not fail on I/O errors should guard the call.

    Refuses to CREATE a new table with empty required fields: if the table does
    not exist yet and the merged backend/model would be blank, it no-ops rather
    than writing backend=""/model="" (which a later flagless launch would
    resolve to `--model ""` and crash the agent). Updating an EXISTING
    (already-complete) table is unaffected. The default's name stays None; named
    agents preserve their existing name."""
    agents = load()
    cur = agents.get(persona_id)
    base_backend = cur.backend if cur is not None else ""
    base_model = cur.model if cur is not None else ""
    base_pinned = cur.yolo_pinned if cur is not None else False
    base_compress_aware = cur.compress_aware if cur is not None else True
    base_name = cur.name if cur is not None else None
    merged_backend = base_backend if backend is None else backend
    merged_model = base_model if model is None else model
    if not merged_backend or not merged_model:
        return                              # don't persist an incomplete table
    agents[persona_id] = AgentConfig(
        backend=merged_backend,
        model=merged_model,
        name=base_name,
        yolo_pinned=base_pinned if yolo_pinned is None else yolo_pinned,
        compress_aware=base_compress_aware if compress_aware is None else compress_aware,
    )
    text = _serialize(agents)

    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def update_default(
    *,
    backend: str | None = None,
    model: str | None = None,
    yolo_pinned: bool | None = None,
    compress_aware: bool | None = None,
) -> None:
    """Upsert [agents.default]. Thin wrapper over update_agent("default", ...)."""
    update_agent(
        RESERVED_KEY,
        backend=backend,
        model=model,
        yolo_pinned=yolo_pinned,
        compress_aware=compress_aware,
    )


def save_agent(persona_id: str, cfg: AgentConfig) -> None:
    """Upsert persona_id's backend+model, preserving its yolo_pinned and every
    other agent table. NOTE: deliberately ignores cfg.yolo_pinned — set_model
    passes a default-constructed cfg and must not clear an existing pin."""
    update_agent(persona_id, backend=cfg.backend, model=cfg.model)


def save_default(cfg: AgentConfig) -> None:
    """Upsert the default's backend+model. Thin wrapper over save_agent."""
    save_agent(RESERVED_KEY, cfg)


def yolo_pinned(persona_id: str = "default") -> bool:
    """Whether the persisted persona is pinned to launch in YOLO. False when the
    table is absent or the file is unreadable."""
    cur = load_agent(persona_id)
    return cur.yolo_pinned if cur is not None else False


def compress_aware_pinned(persona_id: str = "default") -> bool:
    """Whether compress-aware mode is persisted ON for this persona. Returns True
    when the table is absent, file is unreadable, or the key is missing (default ON).
    Reads raw TOML so it works even when the agent table has no backend/model."""
    try:
        raw = conf_path().read_bytes()
    except OSError:
        return True
    if not raw.strip():
        return True
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return True
    agents_raw = data.get("agents")
    if not isinstance(agents_raw, dict):
        return True
    table = agents_raw.get(persona_id)
    if not isinstance(table, dict):
        return True
    ca = table.get("compress_aware")
    return ca if isinstance(ca, bool) else True


def set_compress_aware(persona_id: str, on: bool) -> None:
    """Persist compress_aware=on for persona_id. When a complete agent table (with
    backend+model) exists, overlays via update_agent (preserving all other fields).
    When no complete table exists yet, writes the raw TOML key directly so the value
    survives until a full config row is written."""
    agents = load()
    if persona_id in agents:
        # Full table exists — overlay compress_aware, preserve all other fields.
        update_agent(persona_id, compress_aware=on)
        return
    # No complete table yet — upsert the key directly in the raw TOML.
    path = conf_path()
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError:
        text = f"schema_version = {SCHEMA_VERSION}\n"
    try:
        existing = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        existing = {"schema_version": SCHEMA_VERSION}
    # Build minimal TOML that adds [agents.<persona_id>] with compress_aware.
    # Preserve unrelated top-level keys (schema_version, [harness], etc.) by
    # re-emitting them verbatim ahead of the agents section we control.
    if not isinstance(existing.get("agents"), dict):
        existing["agents"] = {}
    if not isinstance(existing["agents"].get(persona_id), dict):
        existing["agents"][persona_id] = {}
    existing["agents"][persona_id]["compress_aware"] = on
    # Serialize: emit schema_version + one partial agents table.
    ca_str = "true" if on else "false"
    lines = [f"schema_version = {SCHEMA_VERSION}", ""]
    # Emit any existing complete-agent tables preserved by the full serializer.
    full_agents = {k: v for k, v in agents.items()}  # from load() above (complete rows)
    ordered = ([RESERVED_KEY] if RESERVED_KEY in full_agents else []) + sorted(
        k for k in full_agents if k != RESERVED_KEY
    )
    for key in ordered:
        cfg = full_agents[key]
        lines.append(f"[agents.{key}]")
        if cfg.name is not None:
            lines.append(f"name = {_quote(cfg.name)}")
        lines.append(f"backend = {_quote(cfg.backend)}")
        lines.append(f"model = {_quote(cfg.model)}")
        if cfg.yolo_pinned:
            lines.append("yolo_pinned = true")
        if not cfg.compress_aware:
            lines.append("compress_aware = false")
        lines.append("")
    # Emit the partial table for persona_id (no backend/model).
    lines.append(f"[agents.{persona_id}]")
    lines.append(f"compress_aware = {ca_str}")
    lines.append("")
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def harness_debug() -> bool | None:
    """The top-level `[harness] debug` flag from done.conf — a GLOBAL (not
    per-persona) setting that pins the --debug trace on. Returns None when the
    file/section/key is absent or unreadable, so a caller can apply its own
    precedence (flag > env > this > off). Reads raw TOML rather than load() since
    load() only surfaces [agents.*] tables."""
    try:
        raw = conf_path().read_bytes()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    section = data.get("harness")
    if not isinstance(section, dict):
        return None
    val = section.get("debug")
    return val if isinstance(val, bool) else None
