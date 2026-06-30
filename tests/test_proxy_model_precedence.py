from harness import vibeproxy
from harness.persona_sessions import resolve_session_model


def _resolve(env, persisted=None, backend="vibeproxy"):
    """Mimic the acp_main snapshot: shell_set_model + shell_env from the helpers."""
    shell_set = vibeproxy.model_set_in(env)
    val = vibeproxy.model_value(env)
    # acp_main passes the post-load_env value as both shell_env and dotenv.
    import harness.config as config
    config_load = config.load_agent
    try:
        config.load_agent = lambda pid: type("C", (), {"model": persisted})() if persisted else None
        return resolve_session_model(
            "p1", shell_set_model=shell_set, shell_env=val, dotenv=val, backend=backend)
    finally:
        config.load_agent = config_load


def test_proxy_model_in_shell_resolves():
    # Regression for the CRITICAL finding: PROXY_MODEL (not VIBEPROXY_MODEL) set
    # in the shell must win — under the old literal snapshot it was invisible.
    assert _resolve({"PROXY_MODEL": "glm"}) == "glm"


def test_legacy_vibeproxy_model_still_resolves():
    assert _resolve({"VIBEPROXY_MODEL": "gpt-5.4"}) == "gpt-5.4"


def test_proxy_wins_when_both_set():
    assert _resolve({"PROXY_MODEL": "glm", "VIBEPROXY_MODEL": "gpt-5.4"}) == "glm"


def test_persona_persisted_used_when_no_env():
    assert _resolve({}, persisted="claude-sonnet") == "claude-sonnet"
