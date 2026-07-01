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


def _load_raw() -> dict:
    """The full parsed done.conf as a dict ({} if missing/empty/unparseable).
    Single parse path shared by load() (agents) and harness_setting ([harness])."""
    path = conf_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        # A file that EXISTS but won't parse is a real problem: every persisted
        # model/yolo pin silently resolves to defaults. A missing file is normal
        # first-run and stays quiet; a corrupt one warns.
        logger.warning("done.conf at %s is unparseable (%s); ignoring all persisted "
                       "config this session", path, e)
        return {}


def harness_setting(key: str) -> str | None:
    """Read a string value from the top-level [harness] table in done.conf.
    Returns None when the section, key, or file is absent (or the value isn't a
    string). Home for install-wide settings (e.g. compress_model) that aren't
    per-agent. The [harness] table is preserved across writes."""
    section = _load_raw().get("harness")
    if not isinstance(section, dict):
        return None
    val = section.get(key)
    return val if isinstance(val, str) else None


def set_harness_setting(key: str, value: str) -> None:
    """Set a top-level [harness] string key in done.conf, preserving
    schema_version, all agent tables, and every other top-level section.
    Routes through _serialize(preserve=) so there is one serializer."""
    raw = _load_raw()
    harness = raw.get("harness")
    if not isinstance(harness, dict):
        harness = {}
    harness = {**harness, key: value}
    raw = {**raw, "harness": harness}
    text = _serialize(load(), preserve=raw)
    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load() -> dict[str, AgentConfig]:
    """All agents keyed by their table key. Returns {} if the file is missing,
    empty, or unparseable. Individual agent tables missing `backend` or `model`
    are skipped (not fatal)."""
    data = _load_raw()
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


def _emit_scalar(lines: list, key: str, value) -> None:
    """Emit one `key = value` line for a scalar or list-of-scalars TOML value."""
    if isinstance(value, bool):
        lines.append(f"{key} = {'true' if value else 'false'}")
    elif isinstance(value, list):
        inner = ", ".join(_quote(str(x)) for x in value)
        lines.append(f"{key} = [{inner}]")
    else:
        lines.append(f"{key} = {_quote(str(value))}")


def _emit_nested_agent_tables(lines: list, agent_key: str, preserve: dict | None) -> None:
    """Re-emit dict-valued (nested) keys under [agents.<key>] from the prior raw
    config — e.g. `roles` and `roles.fallback` — which _serialize's flat schema
    does not own. Flat scalars are already emitted by the caller. Without this,
    any write drops nested agent tables (they live under the OWNED `agents` key)."""
    if not preserve:
        return
    agents_raw = preserve.get("agents")
    if not isinstance(agents_raw, dict):
        return
    table = agents_raw.get(agent_key)
    if not isinstance(table, dict):
        return
    for k, v in table.items():
        if not isinstance(v, dict):
            continue  # flat scalars already emitted by the caller
        # one level of nesting, e.g. [agents.<key>.roles]
        lines.append(f"[agents.{agent_key}.{k}]")
        for kk, vv in v.items():
            if not isinstance(vv, dict):
                _emit_scalar(lines, kk, vv)
        lines.append("")
        # a second level, e.g. [agents.<key>.roles.fallback]
        for kk, vv in v.items():
            if isinstance(vv, dict):
                lines.append(f"[agents.{agent_key}.{k}.{kk}]")
                for k3, v3 in vv.items():
                    _emit_scalar(lines, k3, v3)
                lines.append("")


def _serialize(
    agents: dict[str, AgentConfig],
    *,
    preserve: dict | None = None,
    partial: dict[str, dict] | None = None,
) -> str:
    """Render the flat schema: top-level schema_version then one [agents.<key>]
    table per agent (name only when set). Deterministic key order: the reserved
    default first, then the rest sorted, so diffs stay stable.

    preserve: raw parsed TOML dict from an existing file. Any top-level keys/
    sections that are NOT `schema_version` and NOT `agents` are re-emitted
    verbatim after the agents block, so sections like [harness] survive writes.

    partial: a dict of {agent_key: {field: value}} for incomplete agent tables
    (those without backend/model) that should be emitted after the complete
    agent tables. Values are TOML-typed (bool → true/false, str → quoted)."""
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
        # Round-trip any nested [agents.<key>.roles(.fallback)] tables the flat
        # schema doesn't own — else every write silently drops them.
        _emit_nested_agent_tables(lines, key, preserve)
    # Emit partial (incomplete) agent tables — no backend/model.
    if partial:
        for key in sorted(partial.keys()):
            fields = partial[key]
            lines.append(f"[agents.{key}]")
            for field, value in sorted(fields.items()):
                if isinstance(value, bool):
                    lines.append(f"{field} = {'true' if value else 'false'}")
                else:
                    lines.append(f"{field} = {_quote(str(value))}")
            lines.append("")
    # Re-emit any top-level sections/keys from the existing file that we don't own.
    if preserve:
        _OWNED = {"schema_version", "agents"}
        for top_key in sorted(preserve.keys()):
            if top_key in _OWNED:
                continue
            value = preserve[top_key]
            if isinstance(value, dict):
                lines.append(f"[{top_key}]")
                for k, v in sorted(value.items()):
                    if isinstance(v, bool):
                        lines.append(f"{k} = {'true' if v else 'false'}")
                    elif isinstance(v, str):
                        lines.append(f"{k} = {_quote(v)}")
                    elif isinstance(v, int):
                        lines.append(f"{k} = {v}")
                    elif isinstance(v, list):
                        # Emit as a TOML array; elements must be scalars.
                        elems = []
                        for elem in v:
                            if isinstance(elem, bool):
                                elems.append("true" if elem else "false")
                            elif isinstance(elem, str):
                                elems.append(_quote(elem))
                            elif isinstance(elem, int):
                                elems.append(str(elem))
                            else:
                                raise ValueError(
                                    f"cannot serialize preserved key {k!r}: "
                                    f"list element {elem!r} is not a scalar"
                                )
                        lines.append(f"{k} = [{', '.join(elems)}]")
                    elif isinstance(v, dict):
                        # Nested dict: emit as a sub-table [top_key.k]
                        lines.append(f"[{top_key}.{k}]")
                        for sk, sv in sorted(v.items()):
                            if isinstance(sv, bool):
                                lines.append(f"{sk} = {'true' if sv else 'false'}")
                            elif isinstance(sv, str):
                                lines.append(f"{sk} = {_quote(sv)}")
                            elif isinstance(sv, int):
                                lines.append(f"{sk} = {sv}")
                            else:
                                raise ValueError(
                                    f"cannot serialize preserved key {k!r}.{sk!r}: "
                                    f"value {sv!r} is not a scalar"
                                )
                    else:
                        raise ValueError(
                            f"cannot serialize preserved key {k!r}: "
                            f"value {v!r} has unsupported type {type(v).__name__!r}"
                        )
                lines.append("")
            else:
                # Top-level scalar key (not schema_version or agents)
                if isinstance(value, bool):
                    lines.append(f"{top_key} = {'true' if value else 'false'}")
                elif isinstance(value, str):
                    lines.append(f"{top_key} = {_quote(value)}")
                elif isinstance(value, int):
                    lines.append(f"{top_key} = {value}")
                else:
                    lines.append(f"{top_key} = {_quote(str(value))}")
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
    # Read the raw file so _serialize can preserve unknown top-level sections.
    path = conf_path()
    existing_raw: dict | None = None
    try:
        raw = path.read_bytes()
        if raw.strip():
            try:
                existing_raw = tomllib.loads(raw.decode("utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError):
                existing_raw = None
    except OSError:
        existing_raw = None
    text = _serialize(agents, preserve=existing_raw)

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
    survives until a full config row is written. In both cases, unknown top-level
    sections (e.g. [harness]) are preserved."""
    agents = load()
    if persona_id in agents:
        # Full table exists — overlay compress_aware, preserve all other fields.
        update_agent(persona_id, compress_aware=on)
        return
    # No complete table yet — upsert the key directly in the raw TOML.
    # Read existing file to preserve top-level sections and complete agent tables.
    path = conf_path()
    existing_raw: dict | None = None
    try:
        raw_bytes = path.read_bytes()
        if raw_bytes.strip():
            try:
                existing_raw = tomllib.loads(raw_bytes.decode("utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError):
                existing_raw = None
    except OSError:
        existing_raw = None
    # Route through _serialize to avoid duplicating serialization logic.
    # agents already contains all complete tables from load() above.
    # Build partial= from ALL pre-existing partial agent tables (those without
    # backend+model in the complete set) plus the target persona's new entry,
    # so touching one persona never drops another persona's partial table.
    partial: dict[str, dict] = {}
    if existing_raw:
        agents_raw_all = existing_raw.get("agents")
        if isinstance(agents_raw_all, dict):
            for ak, at in agents_raw_all.items():
                if ak not in agents and ak != persona_id and isinstance(at, dict):
                    # Carry forward only scalar fields we can safely round-trip.
                    carried: dict = {}
                    for fk, fv in at.items():
                        if isinstance(fv, (bool, str, int)):
                            carried[fk] = fv
                    if carried:
                        partial[ak] = carried
    partial[persona_id] = {"compress_aware": on}
    text = _serialize(agents, preserve=existing_raw, partial=partial)
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
