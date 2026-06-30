from harness.proxy_service import management


class _FakeResp:
    def __init__(self, status, payload): self.status_code, self._p = status, payload
    def json(self): return self._p
    def raise_for_status(self): pass


def test_is_ready_true_on_200(monkeypatch):
    monkeypatch.setattr(management, "_get",
                        lambda path, password, base: _FakeResp(200, {"status": "ok"}))
    assert management.is_ready("pw") is True


def test_auth_url_returns_url_and_state(monkeypatch):
    monkeypatch.setattr(management, "_get",
        lambda path, password, base: _FakeResp(200, {"url": "https://x", "state": "anth-1"}))
    url, state = management.auth_url("anthropic", "pw")
    assert url == "https://x" and state == "anth-1"
