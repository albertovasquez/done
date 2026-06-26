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
  main` before merging.
- Finish via merge-to-main locally, then remove the worktree and prune:
  `git worktree remove .worktrees/<name> && git worktree prune && git branch -d <name>`.
- **Only exception:** a truly trivial, single-file change (a typo, a one-line doc
  tweak) may be done on a short branch in the primary checkout. Anything
  multi-file or multi-step gets a worktree. When in doubt, use a worktree.

Rationale: isolates in-progress work, keeps `main` green, and lets several agents
run at once without stepping on each other.

## 2. Commit your work promptly; don't leave it uncommitted across branch switches

Uncommitted working-tree edits are lost on a branch switch. Commit each logical
unit as you complete it (small, frequent commits). Never switch branches with
unverified, uncommitted changes you care about.

## 3. Tests must pass before merging

Run the suite from the worktree root and confirm green before merging to `main`:

```bash
.venv/bin/python -m pytest tests/ -q     # target tests/ only — upstream/tests needs optional deps
```

`main` must always pass the suite. Do not merge a red branch.

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
