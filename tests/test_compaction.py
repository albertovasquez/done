# tests/test_compaction.py
from harness.compaction import compress, render, CompactResult

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
