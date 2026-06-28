# tests/test_compaction.py
from harness.compaction import compress, render, CompactResult
from harness.compaction import estimate_tokens, build_compaction, Compaction

def _msgs(n, role="user", text="x"):
    return [{"role": role, "content": f"{text}{i}"} for i in range(n)]

# count_tokens: chars * 50 so small test transcripts clear MIN_BUDGET_FLOOR=1000
# (C3 fix — with a plain len() the 1000-token floor dominates tiny ctx_window
# values and compaction would never fire, making "above budget" tests false no-ops).
TOK = lambda s: len(s) * 50

def test_below_budget_is_noop_and_never_summarizes():
    spy = {"called": False}
    def summ(_): spy["called"] = True; return "S"
    prior = _msgs(3)
    r = compress(prior, summarize=summ, count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=10_000_000)  # huge window -> below budget
    assert r.compressed is False
    assert r.method == "none"
    assert r.messages == prior          # same content
    assert spy["called"] is False       # hot path: no LLM

def test_above_budget_summarizes_middle_keeps_head_tail():
    prior = _msgs(40)                    # 40 small msgs; TOK*50 -> well over 1000
    r = compress(prior, summarize=lambda m: "SUMMARY", count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=200,  # int(0.5*200)=100 < floor -> budget=1000
                 protect_head_n=2, protect_last_n=5, target_ratio=0.2)
    assert r.compressed is True          # C3: assert it actually fired, not a no-op
    assert r.method == "summary"
    assert r.messages[:2] == prior[:2]                 # head verbatim
    assert r.messages[-5:] == prior[-5:]               # tail verbatim
    mids = [m for m in r.messages if "SUMMARY" in str(m.get("content"))]
    assert len(mids) == 1                               # exactly one summary msg
    assert mids[0]["role"] == "user"
    assert r.after_msgs < r.before_msgs

def test_empty_middle_is_noop():
    # 8 msgs, protect_last_n=10 -> tail consumes all -> middle empty. Must FIRE the
    # trigger first (clear the 1000 floor) so we exercise the empty-middle path,
    # not just the below-budget early return.
    prior = _msgs(8)
    r = compress(prior, summarize=lambda m: "S", count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=2,    # budget floored to 1000; 8*~150 TOK > 1000 -> fires
                 protect_head_n=0, protect_last_n=10)      # tail floor >= len -> no middle
    assert r.method == "none"
    assert r.messages == prior

def test_summarizer_failure_falls_back_to_truncation():
    prior = _msgs(40)
    def boom(_): raise RuntimeError("provider down")
    r = compress(prior, summarize=boom, count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=200,
                 protect_head_n=2, protect_last_n=5)
    assert r.method == "truncated"
    assert r.messages == prior[:2] + prior[-5:]        # head + tail, no crash

def test_degenerate_budget_is_noop():
    prior = _msgs(40)
    # overhead (subtracted) exceeds int(threshold*ctx_window) -> budget<=0 -> noop
    r = compress(prior, summarize=lambda m: "S", count_tokens=TOK,
                 fixed_overhead_tokens=10_000, ctx_window=200)  # int(0.5*200)=100 - 10000 < 0
    assert r.method == "none"
    assert r.messages == prior


# ---------------------------------------------------------------------------
# _sanitize_tool_pairs tests
# ---------------------------------------------------------------------------
from harness.compaction import _sanitize_tool_pairs


def _asst(tool_ids):
    """Assistant message carrying tool_calls with the given ids."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": tid, "type": "function", "function": {"name": "f", "arguments": "{}"}} for tid in tool_ids],
    }


def _tool_result(tool_call_id, content="result"):
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def test_sanitize_passthrough_when_all_pairs_intact():
    """No compaction cuts happened; all tool results have matching calls → unchanged."""
    msgs = [
        _asst(["c1"]),
        _tool_result("c1"),
    ]
    out = _sanitize_tool_pairs(msgs)
    assert out == msgs


def test_sanitize_drops_orphan_tool_result():
    """Tool result whose call was cut (not in any assistant tool_calls) is dropped."""
    msgs = [
        _tool_result("c1"),  # no matching assistant message
        {"role": "user", "content": "hello"},
    ]
    out = _sanitize_tool_pairs(msgs)
    assert all(m.get("role") != "tool" for m in out)
    assert len(out) == 1
    assert out[0]["role"] == "user"


def test_sanitize_injects_stub_for_orphan_call():
    """Assistant tool_call with no surviving result gets a stub tool message injected after it."""
    msgs = [
        _asst(["c1"]),  # result was cut — no tool result follows
        {"role": "user", "content": "hi"},
    ]
    out = _sanitize_tool_pairs(msgs)
    # stub injected immediately after the assistant message
    assert out[0]["role"] == "assistant"
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "c1"
    assert "[result omitted during context compaction]" in out[1]["content"]
    assert out[2]["role"] == "user"


def test_sanitize_mixed_orphans():
    """
    Realistic slice: some calls have results, one call's result was cut,
    one result's call was cut.
    """
    msgs = [
        _asst(["c1", "c2"]),       # c2's result was cut
        _tool_result("c1"),
        # c2 result is absent
        _tool_result("c_dangling"), # dangling: its call was cut
        {"role": "user", "content": "ok"},
    ]
    out = _sanitize_tool_pairs(msgs)
    roles = [m["role"] for m in out]
    # dangling tool result dropped
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    tool_ids = [m["tool_call_id"] for m in tool_msgs]
    assert "c_dangling" not in tool_ids
    # c1 result kept
    assert "c1" in tool_ids
    # stub injected for c2
    assert "c2" in tool_ids
    stub = next(m for m in tool_msgs if m["tool_call_id"] == "c2")
    assert "[result omitted during context compaction]" in stub["content"]


# ---------------------------------------------------------------------------
# End-to-end: compress() sanitizes after a real middle-cut
# ---------------------------------------------------------------------------

def _assistant_with_call(cid, text="ran"):
    return {"role": "assistant", "content": text,
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": "bash", "arguments": "{}"}}]}


def test_compress_sanitizes_after_cut():
    # head keeps an assistant call; its result lives in the middle (cut) ->
    # surviving call must get a stub, and no orphan result remains.
    head = [_assistant_with_call("call_M")]
    middle = [_tool_result("call_M")] + [{"role": "user", "content": f"m{i}"} for i in range(40)]
    tail = [{"role": "user", "content": f"t{i}"} for i in range(5)]
    prior = head + middle + tail
    r = compress(prior, summarize=lambda m: "SUMMARY", count_tokens=lambda s: len(s) * 50,
                 fixed_overhead_tokens=0, ctx_window=200,
                 protect_head_n=1, protect_last_n=5)
    # the surviving assistant call gets a stub result right after it
    assert r.messages[0] == head[0]
    assert r.messages[1]["role"] == "tool"
    assert r.messages[1]["tool_call_id"] == "call_M"
    # no tool message references a call that isn't present
    call_ids = {c["id"] for m in r.messages if m.get("role") == "assistant"
                for c in m.get("tool_calls", [])}
    for m in r.messages:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in call_ids


def test_recompress_is_bounded_and_valid_not_equal():
    prior = _msgs(60)
    once = compress(prior, summarize=lambda m: "S", count_tokens=lambda s: len(s) * 50,
                    fixed_overhead_tokens=0, ctx_window=200,
                    protect_head_n=2, protect_last_n=5)
    assert once.method == "summary"  # first pass actually compressed
    twice = compress(once.messages, summarize=lambda m: "S",
                     count_tokens=lambda s: len(s) * 50,
                     fixed_overhead_tokens=0, ctx_window=200,
                     protect_head_n=2, protect_last_n=5)
    assert twice.method == "summary"  # second pass also compressed (the path under guard)
    # bounded: never grows; valid: exactly one summary marker, no stacking
    assert twice.after_msgs <= once.after_msgs
    markers = [m for m in twice.messages
               if str(m.get("content", "")).startswith("[Earlier conversation summarized")]
    assert len(markers) <= 1


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_basic():
    # estimate_tokens = max(1, len//4)
    assert estimate_tokens("abcd") == 1        # 4 chars -> 4//4 = 1
    assert estimate_tokens("a" * 100) == 25    # 100 chars -> 25
    assert estimate_tokens("") == 1            # empty -> max(1, 0) = 1


def test_estimate_tokens_is_positive():
    assert estimate_tokens("x") >= 1


# ---------------------------------------------------------------------------
# build_compaction
# ---------------------------------------------------------------------------

class _FakeModel:
    """Minimal model stub: query() returns content + extra.cost."""
    def __init__(self, response="SUMMARY TEXT"):
        self._response = response
        self.costs = []

    def query(self, messages):
        return {"content": self._response, "extra": {"cost": 0.5}}


def test_build_compaction_returns_none_when_no_cfg():
    result = build_compaction(None, model=_FakeModel(), fixed_overhead_tokens=0,
                              add_cost=lambda c: None)
    assert result is None


def test_build_compaction_returns_none_when_disabled():
    result = build_compaction({"enabled": False}, model=_FakeModel(),
                              fixed_overhead_tokens=0, add_cost=lambda c: None)
    assert result is None


def test_build_compaction_returns_compaction_when_enabled():
    comp = build_compaction({"enabled": True}, model=_FakeModel(),
                            fixed_overhead_tokens=100, add_cost=lambda c: None)
    assert comp is not None
    assert isinstance(comp, Compaction)


def test_build_compaction_summarize_calls_model_and_returns_content():
    model = _FakeModel(response="my summary")
    comp = build_compaction({"enabled": True}, model=model,
                            fixed_overhead_tokens=0, add_cost=lambda c: None)
    result = comp.summarize([{"role": "user", "content": "hi"}])
    assert result == "my summary"


def test_build_compaction_summarize_forwards_cost():
    costs = []
    model = _FakeModel(response="S")
    comp = build_compaction({"enabled": True}, model=model,
                            fixed_overhead_tokens=0, add_cost=costs.append)
    comp.summarize([{"role": "user", "content": "hi"}])
    assert costs == [0.5]


def test_build_compaction_params_has_compress_kwargs():
    comp = build_compaction({"enabled": True, "ctx_window": 16000},
                            model=_FakeModel(), fixed_overhead_tokens=200,
                            add_cost=lambda c: None)
    p = comp.params()
    # Must have the keys compress() needs (minus 'prior' which is per-turn)
    assert "count_tokens" in p
    assert "fixed_overhead_tokens" in p
    assert "ctx_window" in p
    assert p["fixed_overhead_tokens"] == 200
    assert p["ctx_window"] == 16000
    # Must NOT have 'summarize' key — that's a separate attr on Compaction
    # (actually summarize IS in params; check it's callable)
    assert callable(p["summarize"])


def test_build_compaction_default_ctx_window():
    from harness.compaction import DEFAULT_CONTEXT_WINDOW
    comp = build_compaction({"enabled": True}, model=_FakeModel(),
                            fixed_overhead_tokens=0, add_cost=lambda c: None)
    p = comp.params()
    assert p["ctx_window"] == DEFAULT_CONTEXT_WINDOW


def test_build_compaction_enabled_field_is_true():
    comp = build_compaction({"enabled": True}, model=_FakeModel(),
                            fixed_overhead_tokens=0, add_cost=lambda c: None)
    assert comp is not None
    assert comp.enabled is True


def test_compaction_integrates_with_compress():
    """End-to-end: build_compaction wired into compress() produces a summary.

    estimate_tokens = len//4. We need before_tokens > MIN_BUDGET_FLOOR (1000).
    render(msgs) for 40 msgs of 100 chars each:
      each line: "user: " + "a"*100 = 106 chars; 40 lines -> ~4240 chars -> 1060 tokens.
    budget = max(int(0.5*32000) - 0, 1000) = 16000. before=1060 < 16000 -> no fire!
    So use ctx_window small enough that budget < 1060: ctx_window=2000 ->
    budget = max(1000-0, 1000) = 1000. Still 1060 > 1000 -> fires!
    """
    model = _FakeModel(response="COMPACTED")
    # 40 msgs, each 100 chars; rendered ~4240 chars -> ~1060 tokens (via estimate_tokens)
    prior = [{"role": "user", "content": "a" * 100} for _ in range(40)]
    comp = build_compaction(
        {"enabled": True, "ctx_window": 2000, "protect_head_n": 2, "protect_last_n": 5},
        model=model, fixed_overhead_tokens=0, add_cost=lambda c: None,
    )
    r = compress(prior, **comp.params())
    assert r.method == "summary"
    assert any("COMPACTED" in str(m.get("content")) for m in r.messages)
