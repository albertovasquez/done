# Done-owned output-filter seam (rtk-inspired) — design

**Date:** 2026-06-30
**Branch:** `rtk-native-tool`
**Status:** Spec (awaiting user review → writing-plans)
**Supersedes:** the "bundle rtk as a native tool" spec (rejected — see Background).

## Goal

Cut LLM token consumption on verbose dev-command output by **post-processing bash
output inside Done's existing `env.execute` path** with a small, Done-owned filter.
No bundled binary, no new tool, no permission-model change. Capture most of rtk's
benefit while owning the code.

## Background: why DIY instead of bundling rtk

The first design bundled [rtk](https://github.com/rtk-ai/rtk) as a managed binary +
first-class tool. A grounded Codex adversarial review (verified against live code)
returned **2 BLOCKERs + 6 MAJOR/MINOR**, and every one was *integration tax for
shipping someone else's binary*, not anything about the filtering itself:

- BLOCKER: a subprocess-running tool bypasses the fail-closed bash permission gate
  (`_dispatch_tool` only gates `{read,write,edit}`; `test_tracing_agent_perm.py:81`
  asserts non-file tools run despite `allow=False`).
- BLOCKER: raw `subprocess` bypasses the env lifecycle — cancel, client-terminal,
  `Submitted`, balanced start/done (`acp_env.py:38-83`).
- MAJOR ×5: no safe `[rtk]` config writer; PATH fallback contradicts "version-pinned";
  decline-vs-install-failure conflated; checksum/version unimplementable (pinned a
  stale version); `str.split` arg parsing + `rtk run`/`proxy` raw passthrough.

**Decision (Alberto, 2026-06-30):** build a small filter we own instead.

### Evidence the DIY win is real (from `rtk gain`, global scope, 38,897 cmds)

| Command | Saved | Share | Note |
|---|---|---|---|
| `lint eslint` (+`--quiet`) | ~8.4M | **52%** | clean runs = pages of "✓" → ~nothing |
| `vitest run` | 4.5M | **28%** | test pass-noise |
| **eslint + test runners** | **~12.9M** | **~80%** | the entire high-yield set |
| `git status`/`log`/`branch` | ~0% | — | rtk barely helps; PASS THROUGH |
| `grep` (2205×), `read`, `ls` | 16–24% | low | high-volume, low-yield |

**Takeaway:** 80% of the 16M saved comes from collapsing **two structurally-verbose
formats** (lint output, test-runner output). That logic is small and ours to write —
not a 50-filter Rust binary. The low-yield long tail we deliberately *don't* filter
(pass through untouched = zero risk).

**Caveat — measure Done's own workload.** The 16M is Alberto's *global* rtk usage
(mostly JS projects: eslint/vitest). Done's own agent runs are a **Python** codebase
(`pytest`, `ruff`, `mypy`, `pip`). The seam is format-agnostic; which formatters to
ship first should be ranked against Done's real workload, not assumed from the global
numbers. See Open Question 1.

## Load-bearing facts (verified against live code)

- **Single output chokepoint**: `AcpEnvironment.execute` produces `out` in three
  branches — client-terminal (`acp_env.py:67`), cancellable (`:73`), super()
  (`:80`) — all converging at `return out` (`:83`), *after* the `finally` that emits
  `("done", command, out)` and after `_check_finished`/`Submitted`. One filter call
  before `return out` covers every branch without touching submit-sentinel logic.
- **bash is the carrier**: eslint/vitest/pytest run as bash commands through
  `env.execute`; `out["output"]` is the raw combined stdout+stderr
  (`_run_cancellable` uses `stderr=STDOUT`, `:95`). The filter keys off the
  `command` string to choose a formatter.
- **No permission/lifecycle impact**: filtering `out["output"]` in place changes
  neither the gate nor cancel/Submitted — it is a pure transform on an already-
  produced result. This is precisely why it sidesteps both BLOCKERs.

## Design

### Component 1 — `harness/output_filters/` (the filters)

A package of **pure functions**, each `(command: str, output: str) -> str | None`:
- Returns a filtered string when it recognizes the format AND can shrink it.
- Returns `None` to decline (→ output passes through unchanged).

A small dispatcher `filter_output(command, output, returncode) -> str`:
1. Pick the first filter whose matcher recognizes `command` (e.g. contains `eslint`,
   `pytest`/`vitest`/`jest`, `ruff`, `mypy`).
2. Apply it; on `None`, exception, or empty result → return the original output
   **unchanged** (fail-open: a filter bug must never lose real output).
3. No matcher → return original unchanged.

Each filter is independently testable with a captured-output fixture (real eslint /
pytest output in → compact out). This is the unit of work and the unit of review.

**Initial filters (rank against Done's workload first — OQ1):** lint (eslint/ruff),
test runner (pytest/vitest). Add more only when measured.

### Component 2 — the seam in `env.execute`

One insertion point in `harness/acp_env.py` right before `return out` (`:83`):

```python
if self._output_filter is not None and out.get("returncode") is not None:
    out = {**out, "output": self._output_filter(command, out.get("output", ""),
                                                out.get("returncode", 0))}
```

- `self._output_filter` is injected at env construction (default `None` = exact
  current behavior / byte-identical no-op, the `load_memory`-gating discipline).
- Skip when cancelled (the early `return out` at `:75` already bypasses this).
- Applied to ALL three branches because they converge at `:83`.

**Why a member, not a hard import:** keeps `acp_env` decoupled and lets the CLI/mock/
headless paths opt in or stay raw. Wire the default filter where the env is built
(confirm exact site during impl — alongside `_check_permission`, `acp_agent.py:~660`).

### Component 3 — config toggle (optional, minimal)

A single `[harness] output_filter = true|false` (default on once shipped). Reuses the
**existing** `[harness]`-preserving config serializer (`config.py` `preserve=` path,
`set_compress_aware` is the precedent) — so NO new config-writer machinery, avoiding
Codex MAJOR #4 entirely. If even this is too much for v1, ship default-on with no
toggle and add the flag later.

### Component 4 — observability (the "rtk gain" idea, ours)

Emit a trace event per filtered command with `{command_kind, bytes_in, bytes_out,
pct_saved}` via the existing tracer (the `("done", …)` emit already fires at `:82`;
add the savings to it or a sibling event). This is how we *measure Done's own
workload* to decide which filters to add next — closing the loop OQ1 asks for.
Cheap: no new subsystem, just structured numbers on an event that already exists.

## Data flow

```
model → bash("pytest -q")
   │
env.execute  (client-terminal | cancellable | super)  → out{output, returncode}
   │  (before return out, :83)
filter_output("pytest -q", out.output, rc)
   │  matcher=pytest → collapse passes, keep failures   (or None → unchanged)
out.output = filtered
   │  trace: {kind:"pytest", bytes_in, bytes_out, pct}
model sees compact output
```

## Error handling (fail-open is the rule)

- Filter raises / returns None / returns empty / returns longer-than-input →
  **return original output unchanged.** A filter must never drop signal (a real
  test failure, a stack trace). Correctness > savings, always.
- Non-zero returncode: filters may still compact (e.g. keep only failures) but the
  fail-open guard means a misbehaving filter on an error run shows the full output.
- Default `_output_filter=None` → the entire seam is inert (no-op parity).

## Testing

- Per-filter: real captured fixture (clean eslint run, failing pytest run, mixed) →
  asserts (a) failures/errors preserved verbatim, (b) measurable shrink on noise.
- `filter_output` dispatcher: unknown command → identity; filter raises → identity;
  filter returns longer → identity (fail-open).
- Seam: `_output_filter=None` → `out` byte-identical (no-op parity test).
- Seam applies across all three `execute` branches (client-terminal, cancellable,
  super) — same filtered result.
- Cancelled run (`exception_info=="cancelled"`) → filter NOT applied.

## Open questions

1. **Which filters first** — rank by Done's *own* workload (pytest/ruff/mypy/pip),
   not the global JS numbers. Bootstrap: ship the trace event (Component 4) with a
   pass-through identity filter for a few sessions, read the `bytes_in` by command
   kind, then write the top 2–3 filters. *(Evidence before code.)*
2. **Filter aggressiveness** — how much to collapse on *failing* runs (keep full
   failure context vs. summarize). Default conservative: never touch error output
   until a filter proves it preserves the failure signal.
3. **Reuse rtk's filter heuristics as reference** — rtk is MIT; we may study its
   eslint/test filters for ideas without taking the binary. (Inspiration, not dep.)

## Out of scope

- Bundling/managing the rtk binary, version pinning, checksums, `dn rtk …`,
  first-run install prompt, PATH resolution — all rejected (the Codex-finding class).
- A first-class `rtk`/filter *tool* the model invokes — unnecessary; the model keeps
  using `bash`, filtering is transparent.
- Filtering non-bash tool output (read/write/edit) — out of scope; the win is in
  command output, and read already has its own path.
- Filtering arbitrary unknown formats — only recognized high-yield formats; the
  default is always pass-through.

## References

- Codex adversarial review of the bundling spec (2026-06-30) — all 8 findings
  verified vs live code; motivated this pivot.
- `acp_env.py:38-103` (the execute chokepoint), `config.py` `set_compress_aware`
  (the `[harness]`-preserving writer precedent), `harness/compaction.py` /
  PR #143/#154 (Done's existing token-reduction layer — sibling concern).
- rtk (MIT, v0.43.0): https://github.com/rtk-ai/rtk — inspiration + filter reference.
