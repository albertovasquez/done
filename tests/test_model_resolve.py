from harness.model_resolve import resolve_model


def test_shell_env_wins_over_everything():
    assert resolve_model(shell_env="S", dotenv="D", persisted="P", engine_default="E") == "S"

def test_persisted_beats_dotenv_and_default():
    assert resolve_model(shell_env=None, dotenv="D", persisted="P", engine_default="E") == "P"

def test_dotenv_beats_default():
    assert resolve_model(shell_env=None, dotenv="D", persisted=None, engine_default="E") == "D"

def test_falls_to_engine_default():
    assert resolve_model(shell_env=None, dotenv=None, persisted=None, engine_default="E") == "E"

def test_empty_strings_are_treated_as_absent():
    # an empty env/persisted value must not win — fall through to the next rung
    assert resolve_model(shell_env="", dotenv="", persisted="", engine_default="E") == "E"
