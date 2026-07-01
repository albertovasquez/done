import tomllib
import pytest
from harness import config


@pytest.fixture(autouse=True)
def _cfgdir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


_SEED = '''schema_version = 1

[agents.bob]
backend = "vibeproxy"
model = "parent"

[agents.bob.roles]
worker = "w1"
reviewer = "r1"

[agents.bob.roles.fallback]
worker = ["w2"]
'''


def _seed(tmp_path):
    (tmp_path / "done.conf").write_text(_SEED)


def _reload(tmp_path):
    return tomllib.loads((tmp_path / "done.conf").read_text())


def test_update_agent_preserves_roles(tmp_path):
    _seed(tmp_path)
    config.update_agent("bob", model="new-parent")
    doc = _reload(tmp_path)
    roles = doc["agents"]["bob"]["roles"]
    assert roles["worker"] == "w1"
    assert roles["reviewer"] == "r1"
    assert roles["fallback"]["worker"] == ["w2"]
    assert doc["agents"]["bob"]["model"] == "new-parent"


def test_set_harness_setting_preserves_roles(tmp_path):
    _seed(tmp_path)
    config.set_harness_setting("theme", "dark")
    doc = _reload(tmp_path)
    assert doc["agents"]["bob"]["roles"]["worker"] == "w1"


_ROLE_ONLY_SEED = '''schema_version = 1

[agents.bob]
backend = "vibeproxy"
model = "parent"

[agents.default.roles]
reviewer = "R"

[agents.default.roles.fallback]
reviewer = ["R2"]
'''


def test_role_only_agent_table_survives_write(tmp_path):
    # [agents.default.roles] with NO backend/model must not be dropped on write.
    (tmp_path / "done.conf").write_text(_ROLE_ONLY_SEED)
    config.update_agent("bob", model="new")
    doc = _reload(tmp_path)
    assert doc["agents"]["default"]["roles"]["reviewer"] == "R"
    assert doc["agents"]["default"]["roles"]["fallback"]["reviewer"] == ["R2"]
    # bob's flat write still applied and no double-emit
    assert doc["agents"]["bob"]["model"] == "new"
