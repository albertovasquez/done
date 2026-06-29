import pytest
from harness.jobs import prompt_state


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def test_not_asked_then_asked():
    assert prompt_state.has_been_asked() is False
    prompt_state.mark_asked()
    assert prompt_state.has_been_asked() is True
