# The Stream Painter keeps two block-placement signals on purpose

When extracting the Stream Painter (#242), we deliberately preserved BOTH signals
that decide where an arriving delta lands — the explicit `boundary()` flag AND the
positional `prior_is_last` check that inspects the Transcript's children — even
though they look redundant. We chose a verbatim, behavior-preserving port over
collapsing them in the same pass, because three shipped bug-fixes (PR #81
stream-misroute, PR #138 footer-above-answer, PR #217 coalesce-deltas) all
converged on exactly this decision logic, and the two signals are NOT known to be
equivalent: the flag is set on in-turn boundaries, but the positional check also
catches the Late-Delta case, where a Footer is mounted after the turn returns with
no `boundary()` call.

## Considered Options

- **Collapse to `boundary()`-only in one pass** — cleaner and deeper, but
  unproven equivalent; the risk is silently reintroducing #81/#138.
- **Verbatim port now, delete `prior_is_last` in a tested follow-up** (chosen) —
  the first PR moves the logic unchanged behind the Stream Painter and puts the
  new-answer / new-step / Late-Delta / footer-present cases under fast unit tests
  against a fake Transcript View. Only once those tests demonstrate the
  flag-only path is equivalent across every case do we delete the positional
  check, as a separate reviewable change.

## Consequences

A future reader who sees the two signals and assumes they are duplication should
NOT collapse them without the equivalence tests green. If the follow-up proves
they are not equivalent, both stay and this ADR records why.

## Scope and seam (refined after adversarial review)

Two corrections came out of the pre-implementation review:

- **The Stream Painter owns stream-widget lifecycle only.** `_reset_conversation`
  (App token/snapshot/status state) and `_add_user_message` (rendering the user's
  own line + forced scroll) stay App orchestration; they call `painter.reset()` /
  `painter.end()` rather than moving into the painter. This keeps the seam small
  and the painter's interface honest.
- **The Transcript View seam delegates scheduling to the App, not the answer
  widget.** The 12Hz paint timer and the post-refresh first paint currently bind
  to the App's message pump and are torn down with it. If the adapter scheduled
  on the Markdown widget instead, timer/refresh lifecycle would diverge from
  today's behavior — so `schedule` and `after_refresh` explicitly route to the App.
