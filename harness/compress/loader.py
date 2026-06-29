from pathlib import Path

from harness.compress import sibling


def load_context_file(source: Path, *, mode_on: bool) -> str:
    source = Path(source)
    original = source.read_text(errors="ignore")
    if not mode_on:
        return original
    sib = sibling.sibling_path(source)
    if not sib.exists() or not sibling.is_safe_sibling(source, sib):
        return original
    sib_text = sib.read_text(errors="ignore")
    if sibling.freshness(original, sib_text) != "fresh":
        return original
    _, body = sibling.split_header(sib_text)
    return body
