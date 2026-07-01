from harness import model_availability as av
from harness.model_catalog import Provider, Model


_CATALOG = [
    Provider("neuralwatt", "NeuralWatt", ["NEURALWATT_API_KEY"],
             [Model("glm-5.2", "GLM 5.2")]),
    Provider("anthropic", "Anthropic", ["ANTHROPIC_API_KEY"],
             [Model("claude-opus-4-8", "Claude Opus 4.8")]),
]


def test_available_when_proxy_serves_matching_id():
    # proxy serves the ALIAS 'glm'; catalog has upstream 'glm-5.2' -> canonical match
    out = av.reconcile(_CATALOG, proxy_ids=["glm", "claude-opus-4-8"],
                       keys_present={"neuralwatt": True, "anthropic": True})
    by = {(s.provider, s.display_name): s for s in out}
    glm = by[("neuralwatt", "GLM 5.2")]
    assert glm.status == "available"
    assert glm.bind_id == "glm"            # BIND the proxy id, not 'glm-5.2'


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
    out = av.reconcile(_CATALOG, proxy_ids=["glm"], keys_present={"neuralwatt": True, "anthropic": False})
    model, warning = av.resolve_or_warn("glm", out)
    assert model == "glm" and warning is None


def test_resolve_or_warn_warns_never_swaps():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("glm", out)
    assert model == "glm"              # NEVER substituted
    assert warning and "glm" in warning
