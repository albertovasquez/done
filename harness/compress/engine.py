from harness.compress import rules
from harness.compress.validate import validate

MAX_RETRIES = 2


class CompressionError(Exception):
    pass


def compress_text(original: str, *, call_model) -> str:
    compressed = rules.strip_llm_wrapper(call_model(rules.build_compress_prompt(original)))
    for attempt in range(MAX_RETRIES):
        result = validate(original, compressed)
        if result.is_valid:
            return compressed
        if attempt == MAX_RETRIES - 1:
            raise CompressionError(f"invalid after {MAX_RETRIES} retries: {result.errors}")
        compressed = rules.strip_llm_wrapper(
            call_model(rules.build_fix_prompt(original, compressed, result.errors))
        )
    return compressed  # unreachable; loop returns or raises
