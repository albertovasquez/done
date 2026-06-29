import re
from collections import Counter
from dataclasses import dataclass, field

_URL_RE = re.compile(r"https?://[^\s)\]]+")
_TRAILING_PUNCT = ".,;:!?\"')]"
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6} .+$", re.MULTILINE)


def _find_urls(text: str) -> list[str]:
    """Extract URLs and strip trailing sentence punctuation from each."""
    return [u.rstrip(_TRAILING_PUNCT) for u in _URL_RE.findall(text)]


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)


def _missing(items_a, items_b, label):
    errs = []
    counts_a = Counter(items_a)
    counts_b = Counter(items_b)
    for it, count_a in counts_a.items():
        if counts_b[it] < count_a:
            errs.append(f"{label} not preserved: {it[:60]}")
    return errs


def validate(original: str, compressed: str) -> ValidationResult:
    """Validate that URLs, fenced code blocks, and headings are exactly preserved."""
    errors: list[str] = []
    errors += _missing(_find_urls(original), _find_urls(compressed), "URL")
    errors += _missing(_FENCE_RE.findall(original), _FENCE_RE.findall(compressed), "code block")
    errors += _missing(_HEADING_RE.findall(original), _HEADING_RE.findall(compressed), "heading")
    return ValidationResult(is_valid=not errors, errors=errors)
