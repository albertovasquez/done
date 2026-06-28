import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.acp_emit import message_chunk, with_meta  # noqa: E402
from harness.tui.state import decision_from_meta  # noqa: E402


def _decision_chunk(question, options):
    """Mirror the production emit so the test pins the exact shape the TUI parses."""
    chunk = message_chunk(question)
    if options:
        chunk = with_meta(chunk, {"decision": {
            "question": question,
            "options": [{"title": t, "rationale": r} for t, r in options]}})
    return chunk


def test_decision_meta_shape_round_trips_through_parser():
    chunk = _decision_chunk("Which did you mean?", [("Explain", "read"), ("Fix", "repair")])
    dv = decision_from_meta(chunk.field_meta)
    assert dv is not None
    assert dv.question == "Which did you mean?"
    assert dv.options == (("Explain", "read"), ("Fix", "repair"))


def test_empty_options_attaches_no_decision_meta():
    chunk = _decision_chunk("Clarify please", [])
    # byte-identical-to-today guard: no harness.decision meta when there are no options
    assert (chunk.field_meta or {}).get("harness", {}).get("decision") is None
