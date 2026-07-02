from harness import model_availability as av
from harness.model_catalog import Provider, Model


_CATALOG = [
    Provider("neuralwatt", "NeuralWatt", ["NEURALWATT_API_KEY"],
             [Model("glm-5.2", "GLM 5.2")]),
    Provider("anthropic", "Anthropic", ["ANTHROPIC_API_KEY"],
             [Model("claude-opus-4-8", "Claude Opus 4.8")]),
]


def test_available_when_proxy_serves_matching_id():
    # proxy serves the real id 'glm-5.2' (no alias); catalog id is the same
    out = av.reconcile(_CATALOG, proxy_ids=["glm-5.2", "claude-opus-4-8"],
                       keys_present={"neuralwatt": True, "anthropic": True})
    by = {(s.provider, s.display_name): s for s in out}
    glm = by[("neuralwatt", "GLM 5.2")]
    assert glm.status == "available"
    assert glm.bind_id == "glm-5.2"        # BIND the real proxy id


def test_login_needed_when_key_absent_and_not_served():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": True})
    glm = next(s for s in out if s.display_name == "GLM 5.2")
    assert glm.status == "login_needed"
    assert glm.bind_id is None


def test_stale_config_when_key_present_but_not_served():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": True, "anthropic": True})
    glm = next(s for s in out if s.display_name == "GLM 5.2")
    assert glm.status == "stale_config"
    assert glm.bind_id is None


def test_resolve_or_warn_passes_available_no_warning():
    out = av.reconcile(_CATALOG, proxy_ids=["glm-5.2"], keys_present={"neuralwatt": True, "anthropic": False})
    model, warning = av.resolve_or_warn("glm-5.2", out)
    assert model == "glm-5.2" and warning is None


def test_resolve_or_warn_warns_never_swaps():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("glm-5.2", out)
    assert model == "glm-5.2"          # NEVER substituted
    assert warning and "glm-5.2" in warning


def test_resolve_or_warn_matches_dated_proxy_id_no_warning():
    # proxy serves the DATED id; catalog + config use the bare id
    out = av.reconcile(_CATALOG, proxy_ids=["claude-opus-4-8-20251115"],
                       keys_present={"neuralwatt": False, "anthropic": True})
    # available bind_id is the dated proxy id
    s = next(st for st in out if st.display_name == "Claude Opus 4.8")
    assert s.status == "available"
    # configured with the BARE id must still resolve WITHOUT a warning (canonical match)
    model, warning = av.resolve_or_warn("claude-opus-4-8", out)
    assert model == "claude-opus-4-8"
    assert warning is None


def test_reconcile_carries_catalog_model_id():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    assert all(s.model_id is not None for s in out)
    assert any(s.model_id == "glm-5.2" for s in out)


def test_warning_names_login_needed_reason():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("glm-5.2", out)
    assert model == "glm-5.2"                        # NEVER substituted
    assert "login" in warning.lower() or "key" in warning.lower()
    assert "neuralwatt" in warning


def test_warning_names_stale_config_reason():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": True, "anthropic": False})
    model, warning = av.resolve_or_warn("glm-5.2", out)
    assert model == "glm-5.2"
    assert "stale" in warning.lower()
    assert "dn proxy" in warning


def test_warning_generic_for_unknown_model():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("made-up-model", out)
    assert model == "made-up-model" and warning
