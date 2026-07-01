"""Canonical model-id normalization — a MATCH KEY ONLY. Never display or bind a
canonical id; it exists solely to reconcile catalog ids (models.dev upstream
names) against proxy-served ids (short aliases, dated Claude ids). Two rules:
(1) map a neuralwatt alias to its upstream id; (2) strip a strict trailing
-YYYYMMDD date suffix. Conservative by design — see #229-era caveman review."""
from __future__ import annotations

import re

from harness.proxy_service.model_map import alias_to_upstream

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def canonical(model_id: str) -> str:
    up = alias_to_upstream().get(model_id, model_id)  # alias -> upstream if known
    return _DATE_SUFFIX.sub("", up)                    # strip strict -YYYYMMDD tail


def matches(a: str, b: str) -> bool:
    return canonical(a) == canonical(b)
