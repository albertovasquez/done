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

Run the suite from the worktree root and confirm green before opening the PR:

```bash
.venv/bin/python -m pytest tests/ -q     # target tests/ only — upstream/tests needs optional deps
```

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

## 7. Commit message trailer

End commit messages with:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
(or the appropriate trailer for whichever agent authored the change.)
