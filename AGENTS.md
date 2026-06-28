# Operating standards — quiubo-harness

Standards for any agent (Claude Code, Codex, Copilot, …) and any human working
in this repo. These are committed and shared; they take precedence over an
agent's default behavior. (Claude-specific notes live in `CLAUDE.md`, which
defers to this file.)

## 1. Always work in a git worktree, never directly on `main`

Do every change in a **dedicated git worktree on its own branch** — not on
`main`, not in the primary checkout. This keeps `main` always-runnable and means
parallel work never collides in a shared working tree.

```bash
git worktree add .worktrees/<short-task-name> -b <short-task-name> main
cd .worktrees/<short-task-name>
```

- One worktree per task/branch. `.worktrees/` is gitignored.
- Branch off the latest `main`. If `main` moved while you worked, `git rebase
  main` before pushing.
- Finish by **pushing the branch and opening a PR against `main`** — not a local
  merge. Production runs off `main`, so every change is review-gated:
  `git push -u origin <branch> && gh pr create --base main`. Do **not** merge the
  PR yourself; that is the maintainer's call. Clean up the worktree only after the
  PR merges: `git worktree remove .worktrees/<name> && git worktree prune`.
- **Never merge into or commit on the primary checkout.** Assume another branch
  (e.g. one an agent or automation left checked out) may be live there; touching it
  collides with in-flight work.
- **No exceptions — not even one-line changes.** A typo fix, a single CSS value, a
  doc tweak: all go in a worktree. "It's trivial" is not a reason to skip it;
  triviality is exactly when the habit slips and two agents collide in the primary
  checkout. Always a worktree, every time.

Rationale: isolates in-progress work, keeps `main` green and review-gated, and
lets several agents run at once without stepping on each other.

## 2. Commit your work promptly; don't leave it uncommitted across branch switches

Uncommitted working-tree edits are lost on a branch switch. Commit each logical
unit as you complete it (small, frequent commits). Never switch branches with
unverified, uncommitted changes you care about.

## 3. Tests must pass before opening the PR

Run the suite and confirm green before opening the PR. There is a single root
`.venv` (you do not create one per worktree); `tests/conftest.py` makes `pytest`
import the source of whichever worktree the tests live in, so you can run from
anywhere:

```bash
# from the worktree root:
<repo-root>/.venv/bin/python -m pytest tests/ -q   # target tests/ only — upstream/tests needs optional deps
# or, equivalently, from the repo root targeting the worktree:
.venv/bin/python -m pytest .worktrees/<task>/tests/ -q
```

conftest resolves imports to the worktree being tested regardless of cwd, so a
worktree's tests never silently run the root checkout's code.

`main` must always pass the suite. Don't open a PR from a red branch; put the test
result in the PR body so the reviewer sees it.

## 4. Zero upstream edits

`upstream/` is the vendored, unmodified mini-swe-agent engine. Never modify
anything under `upstream/`. Extend or override in `harness/` instead. (Changing
*our* `pyproject.toml`'s declaration of the engine source is our config, not an
upstream edit.)

## 5. Match the surrounding code

Follow the existing style, naming, comment density, and file layout. Make
surgical changes — every changed line should trace to the task. Don't refactor
unrelated code or add speculative abstractions. Add tests only where they buy
real safety.

## 6. The engine is the product; clients consume it

The harness is an ACP engine; clients (TUI, editor, future workers) consume it
over the protocol. New capabilities belong in the engine/agent, surfaced over
ACP — not baked into one client. Keep that separation.

> Engine capabilities are documented for users and agents in `README.md` and
> `docs/` — e.g. **personas** (agent identity from `~/.config/harness/agents/`)
> in [`docs/personas.md`](docs/personas.md). Read those before answering "how
> does X work?"; verify against live code, since docs can lag a phase behind.

## 7. Build TUI UI from the approved design system

New or changed TUI UI must be based on the **approved design system**, not on
one-off widgets or ad-hoc styling.

- **Components:** find the component in `harness/tui/styles/components.md` and
  extend it or compose existing components. Only add a new catalog entry when
  nothing fits — and record its rationale in the design spec
  (`docs/superpowers/specs/2026-06-26-tui-design-system-design.md`).
- **Tokens:** use the semantic tokens in `harness/tui/theme.py`
  (`HARNESS_THEME.variables`, `COLORS`, `STATUS_COLOR`). No hardcoded hex outside
  `theme.py` / `COLORS`. Status is carried by color + glyph + weight together.
- **State:** components stay dumb and reactive — they read a slice of the
  `FleetSnapshot` (`harness/tui/state.py`); they never compute state transitions
  (that is the reducer's job).
- **Motion:** follow the restraint policy (brand voice) — motion signals a state
  change, one looping animation max, ≤250ms transitions, reduced-motion +
  monochrome fallbacks.

If a change needs the design system itself to grow, update the spec and
`components.md` in the same PR so the catalog stays the source of truth.

## 8. Commit message trailer

End commit messages with:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
(or the appropriate trailer for whichever agent authored the change.)

## 9. Diagnose from the logs before guessing

When a run misbehaves, read the logs — don't theorize from the code alone. All
runs leave durable artifacts under `harness/runs/` (gitignored): the CLI writes
`events.jsonl` always; the TUI writes `trace.jsonl` and the agent writes
`harness.log` under `--debug` (or `HARNESS_DEBUG=1`). They are JSONL/plain-text —
read them with `jq`/`grep`. **`docs/debugging.md` is the full reference**: the
three formats, how to find the latest run, the event vocabulary, and copy-paste
`jq` recipes. Reach for `--debug` + the trace file when a failure isn't
reproducible in a unit test.
