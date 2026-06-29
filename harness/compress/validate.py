import re
from dataclasses import dataclass, field

_URL_RE = re.compile(r"https?://[^\s)\]]+")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6} .+$", re.MULTILINE)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)


def _missing(items_a, items_b, label):
    errs = []
    for it in items_a:
        if it not in items_b:
            errs.append(f"{label} not preserved: {it[:60]}")
    return errs


def validate(original: str, compressed: str) -> ValidationResult:
    errors: list[str] = []
    errors += _missing(_URL_RE.findall(original), _URL_RE.findall(compressed), "URL")
    errors += _missing(_FENCE_RE.findall(original), _FENCE_RE.findall(compressed), "code block")
    errors += _missing(_HEADING_RE.findall(original), _HEADING_RE.findall(compressed), "heading")
    return ValidationResult(is_valid=not errors, errors=errors)
