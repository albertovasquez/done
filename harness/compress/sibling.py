import hashlib
import os
import re
from pathlib import Path

from harness.compress import rules

_FIELD_RE = re.compile(r"<!--\s*compress-aware\s*(.*?)-->", re.DOTALL)
_KV_RE = re.compile(r"(\S+):\s*(\S+)")


def sibling_path(source: Path) -> Path:
    return source.with_name(source.stem + ".compressed.md")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def build_header(*, source_sha: str, body_sha: str, date: str) -> str:
    return (
        "<!-- compress-aware "
        f"source-sha256:{source_sha} "
        f"engine-version:{rules.RULES_VERSION} "
        f"rules-sha256:{rules.rules_sha256()} "
        f"body-sha256:{body_sha} "
        f"built:{date} "
        "notice:generated-by-Done-do-not-edit "
        "-->\n"
    )


def parse_header(text: str) -> dict | None:
    m = _FIELD_RE.search(text)
    if not m:
        return None
    fields = dict(_KV_RE.findall(m.group(1)))
    required = {"source-sha256", "engine-version", "rules-sha256", "body-sha256"}
    if not required.issubset(fields):
        return None
    return fields


def split_header(text: str) -> tuple[str, str]:
    m = _FIELD_RE.search(text)
    if not m:
        return "", text
    end = text.index("-->", m.start()) + len("-->")
    # consume one trailing newline
    body = text[end:]
    if body.startswith("\n"):
        body = body[1:]
    return text[:end], body


def freshness(source_text: str, sibling_text: str) -> str:
    fields = parse_header(sibling_text)
    if fields is None:
        return "corrupt"
    _, body = split_header(sibling_text)
    if fields["source-sha256"] != sha256_text(source_text):
        return "stale"
    if fields["engine-version"] != rules.RULES_VERSION:
        return "stale"
    if fields["rules-sha256"] != rules.rules_sha256():
        return "stale"
    if fields["body-sha256"] != sha256_text(body):
        return "stale"
    return "fresh"


def is_safe_sibling(source: Path, sib: Path) -> bool:
    if not source.exists():
        return False
    if sib.is_symlink():
        return False
    try:
        return sib.resolve().parent == source.resolve().parent
    except OSError:
        return False


def write_sibling(source: Path, body: str, *, today: str) -> Path:
    src_text = source.read_text(errors="ignore")
    header = build_header(
        source_sha=sha256_text(src_text),
        body_sha=sha256_text(body),
        date=today,
    )
    out = header + body
    sib = sibling_path(source)
    tmp = sib.with_suffix(sib.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write(out)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, sib)
    return sib
