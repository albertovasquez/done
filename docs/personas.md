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

On first run, DoneDone seeds this directory with all three files as **inert
templates** — each holds only a commented hint, so the agent's behavior is
unchanged until you edit one. You don't create anything; you edit the files
already there. A file you leave as-is (or delete) is simply skipped.

A persona workspace may also hold an **`AGENTS.md`** (standing instructions — the
persona's "ops manual") and a `persona.toml` (display `name`, enabled `flows`,
extra `skills` roots). See [agents-md.md](agents-md.md) and
[router-flows.md](router-flows.md).

## Quick start

A fresh install already placed template files in
`~/.config/harness/agents/default/`. Open one and replace the comment with real
text:

```bash
cat > ~/.config/harness/agents/default/SOUL.md <<'EOF'
You are terse. You never explain your reasoning unless asked.
You address the user as "Captain".
EOF

dn
```

(If the directory isn't there — e.g. you deleted it — DoneDone re-seeds the
templates on the next run. It never overwrites a file you've already edited.)

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

## Selection

Run as a named persona workspace with `--persona <id>`:

    dn --persona fred

Without `--persona`, the built-in `default` persona is used. The id must be an
existing workspace under `~/.config/harness/agents/<id>/` — an unknown id is a
hard error. (To make a new persona from inside the TUI, press **n** in the agents
rail; see below.) Each persona has its own sessions, memory, and model (persisted
in `done.conf` under `[agents.<id>]`); a live `/models` swap is remembered per
persona.

A persona workspace may also declare extra skill directories in a `persona.toml`
file at the workspace root:

```toml
skills = ["/path/to/extra-skills", "~/my-skills"]
```

These are loaded in addition to the system and user skill roots. (`persona.toml`
never holds the worker model — that lives in `done.conf [agents.<id>]`.)

`persona.toml` may also set a display `name` (used by the agents rail; falls back
to the workspace id):

```toml
name = "Fred the Reviewer"
```

## The agents rail (TUI)

Press **Tab** (or run `/persona`) to open the **agents rail** — a list of every
persona workspace, with the active one highlighted (and named via `persona.toml`).
**Esc** closes it. The status-bar persona chip also shows the live persona.

From the rail you can:

- **Switch personas** — select a persona to switch to it **in-process**: the same
  long-lived agent process repoints to that persona's session, memory, and model
  (no restart, no `--persona` relaunch). This follows the mature-harness approach
  (OpenClaw / OpenCode / Codex): one process alive, routing between loaded sessions
  in-process — process-restart was rejected because it leaks per-persona state.
- **Create a persona** — press **n** to name a new persona; the name is slugified
  into a safe workspace id, the workspace is seeded with the inert templates, and
  the session switches to it. The typed name is kept as the display label.

## Not yet (later phases)

Selection (`--persona`), per-persona model persistence, in-process switching, and
persona creation (the **n** key in the rail) have all shipped. What remains
deferred:

- **Guided onboarding.** First-run seeding drops editable templates (done), but
  there's no interactive `BOOTSTRAP.md` setup ritual or wiped-workspace
  attestation yet.
- **Scheduling / proactive runs** (a persona acting without you present).

For the design rationale behind these phases, see the dated specs under
`docs/superpowers/specs/` (`*persona-fleet*`, `*phaseA-persona-contract*`).
