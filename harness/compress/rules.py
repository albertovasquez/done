import hashlib
import re

RULES_VERSION = "1"  # bump when the prompts below change in spirit

_OUTER_FENCE_RE = re.compile(r"\A\s*(`{3,}|~{3,})[^\n]*\n(.*)\n\1\s*\Z", re.DOTALL)


def strip_llm_wrapper(text: str) -> str:
    m = _OUTER_FENCE_RE.match(text)
    return m.group(2) if m else text


def build_compress_prompt(original: str) -> str:
    return f"""Compress this markdown into caveman format.

STRICT RULES:
- Do NOT modify anything inside ``` code blocks
- Do NOT modify anything inside inline backticks
- Preserve ALL URLs exactly
- Preserve ALL headings exactly
- Preserve file paths and commands
- Return ONLY the compressed markdown body — no outer fence.

Only compress natural language.

TEXT:
{original}
"""


def build_fix_prompt(original: str, compressed: str, errors: list[str]) -> str:
    errors_str = "\n".join(f"- {e}" for e in errors)
    return f"""Fix this caveman-compressed markdown. Only fix the listed errors.

CRITICAL RULES:
- DO NOT recompress or rephrase
- ONLY fix the listed errors — leave everything else exactly as-is

ERRORS TO FIX:
{errors_str}

ORIGINAL (reference only):
{original}

COMPRESSED (fix this):
{compressed}

Return ONLY the fixed compressed file. No explanation.
"""


def rules_sha256() -> str:
    # Hash the rule-bearing strings so any edit invalidates siblings.
    material = "\x00".join([
        RULES_VERSION,
        build_compress_prompt("\x01"),
        build_fix_prompt("\x01", "\x02", ["\x03"]),
    ])
    return hashlib.sha256(material.encode()).hexdigest()
