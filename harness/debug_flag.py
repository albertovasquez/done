"""Resolve the --debug gate with one precedence rule, shared by both
entrypoints: CLI flag > HARNESS_DEBUG env > done.conf [harness] debug > off."""

from __future__ import annotations

from typing import Mapping


def resolve_debug(flag: bool, env: Mapping[str, str], conf_debug: bool | None) -> bool:
    if flag:
        return True
    if env.get("HARNESS_DEBUG") == "1":
        return True
    return bool(conf_debug)
