# Personas

A **persona** gives DoneDone's agent an identity — tone, boundaries, and who
it's talking to. It is a small set of plain-text files injected into the agent's
context for the whole session.

Personas and skills are the two context sources, and they're complementary:

- **Skills** are *task-knowledge*. The router selects the relevant ones per
  request (see the README's "System skills").
- **Personas** are *identity*. The same persona applies to every turn in a
  session, on both the chat path and the coding path.

> This document describes what ships today. Personas are being built in phases;
> the "Not yet" section at the end lists what's deferred so you don't expect it.

## The workspace

A persona is a directory of files. The built-in persona is at:

```
~/.config/harness/agents/default/
```

It reads three files, all optional:

| File | Role |
|---|---|
| `SOUL.md` | persona, tone, boundaries — "who are you?" |
| `IDENTITY.md` | name / vibe / emoji |
| `USER.md` | who the user is (static context you write) |

Nothing creates this directory for you — you make it and drop in the files you
want. A file you omit is simply skipped.

## Quick start

```bash
mkdir -p ~/.config/harness/agents/default

cat > ~/.config/harness/agents/default/SOUL.md <<'EOF'
You are terse. You never explain your reasoning unless asked.
You address the user as "Captain".
EOF

dn
```

On a fresh install, `~/.config/harness/agents/default/` is seeded for you with
three template files (`SOUL.md`, `IDENTITY.md`, `USER.md`). They contain only a
commented hint, so they inject nothing until you replace the comment with real
text — the agent's behavior is unchanged until you edit one.

Now both a chat question ("what's the weather model here?") and a coding task
("fix the failing test") run with that persona baked into the agent's system
prompt — terse, and calling you "Captain".

## How it behaves

- **Injected on both paths.** The persona reaches the chat path (as a system
  message) and the coding path (appended to the agent's system prompt, before
  the task skills). It does **not** affect deterministic replies like the
  "what skills do you have?" catalog listing.
- **Composed once per session.** The files are read on the first turn of a
  session and reused for the rest of it. Editing a file mid-session has no effect
  until the next session — restart to pick up changes.
- **Order is identity, then task.** In the coding path the system prompt is
  `base → persona → skills`, so identity frames the task knowledge.
- **Blank files are skipped.** A file that is empty or only whitespace is
  ignored — it never injects an empty section.
- **Large files are trimmed.** Each file is capped at 8000 characters; a longer
  file is truncated with a `…[truncated]…` marker so the prompt stays lean.
- **A malformed file never breaks a run.** A missing, unreadable, or non-UTF-8
  file is skipped, not fatal.
- **The TUI shows a `persona_load` chip** once per session (after the
  request-type chip) listing which files were injected — so you can see the
  persona took effect. It does not appear when no persona is loaded.

## The no-op guarantee

If `~/.config/harness/agents/default/` is absent or empty, DoneDone behaves
**exactly** as it did before personas existed: no persona, no injected text, no
`persona_load` chip, no overhead. Personas are strictly additive — you never pay
for one you didn't create.

## The dev path

The non-ACP developer entrypoint (`./run.sh` / `harness/run_traced.py`) reads the
same `~/.config/harness/agents/default/` workspace and applies the persona to its
chat and agent paths too. The persona is resolved through the same single
resolver the ACP agent uses, so the two paths can't drift.

## Not yet (later phases)

Phase A is the foundation: read the files, inject them, with a no-op default.
Deliberately out of scope for now:

- **Multiple personas / selection.** There's one fixed `default` workspace; no
  `--persona` flag or `/persona` picker yet. *(Phase C.)*
- **Onboarding / scaffolding.** You create the directory and files by hand;
  nothing generates templates or runs a first-run setup. *(Phase D.)*
- **Memory.** The persona is static — it can't learn or accumulate context
  across sessions. *(Phase B.)*
- **Per-persona model / config** (`persona.toml`). *(Phase C.)*
- **Scheduling / proactive runs** (a persona acting without you present).
  *(Phase E.)*

For the design rationale behind these phases, see the dated specs under
`docs/superpowers/specs/` (`*persona-fleet*`, `*phaseA-persona-contract*`).
