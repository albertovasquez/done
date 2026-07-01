from harness.tui.model_picker import build_picker_rows
from harness.model_availability import ModelStatus


def test_rows_grouped_by_provider_with_status_marks():
    statuses = [
        ModelStatus("anthropic", "Claude Opus 4.8", "claude-opus-4-8", "available"),
        ModelStatus("neuralwatt", "GLM 5.2", None, "login_needed"),
        ModelStatus("neuralwatt", "Qwen3.5", None, "stale_config"),
    ]
    rows = build_picker_rows(statuses)
    # provider header rows are non-selectable (id is None/empty), model rows carry bind_id or a sentinel
    labels = [r.label for r in rows]
    assert any("anthropic" in l.lower() for l in labels)      # a group header
    assert any("GLM 5.2" in l and "login" in l.lower() for l in labels)   # login_needed marked
    # available row is selectable (has a real bind id)
    avail = [r for r in rows if r.id == "claude-opus-4-8"]
    assert len(avail) == 1
    # login_needed / stale_config rows are not directly bindable (disabled)
    disabled = [r for r in rows if getattr(r, "disabled", False)]
    assert any("GLM 5.2" in r.label for r in disabled)
