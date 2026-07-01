from harness.tui.model_picker import build_picker_rows
from harness.model_availability import ModelStatus, reconcile
from harness.model_catalog import Provider, Model


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


def test_empty_proxy_still_yields_catalog_rows():
    # Regression guard: when the proxy serves NOTHING (down/empty), the picker
    # must NOT be empty — the catalog still renders every model as login_needed
    # (or stale_config), so action_select_model must not early-return on empty
    # proxy_ids. This is the offline-resilience the feature exists to provide.
    catalog = [Provider("neuralwatt", "NeuralWatt", ["NEURALWATT_API_KEY"],
                        [Model("glm-5.2", "GLM 5.2")])]
    statuses = reconcile(catalog, proxy_ids=[], keys_present={"neuralwatt": False})
    rows = build_picker_rows(statuses)
    assert rows, "empty proxy must still produce catalog rows"
    assert any("GLM 5.2" in r.label and "login" in r.label.lower() for r in rows)
