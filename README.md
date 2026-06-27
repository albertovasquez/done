<div align="center">

# DoneDone

**Get it done.** A coding agent you drive from your terminal — or from your editor.

</div>

---

DoneDone (`dn`) is a coding agent that lives where you work. Launch it in any
directory like `git`, hand it a task, and watch it read, run commands, and edit
code to get the job done — streaming every step so you stay in control.

Under the hood, the agent is an **[Agent Client Protocol](https://github.com/i-am-bee/acp)
(ACP) server**: a single engine that any client can drive — the bundled terminal
UI, an editor like Zed, or your own automation. The engine is the product;
clients just talk to it over JSON-RPC.

DoneDone is built on a vendored, **unmodified** copy of
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), wrapped with an
event tracer, a request router, a skills layer, and the ACP interface.

## Highlights

- **Run it anywhere.** `dn` operates on your current working directory, just like
  `git`. Point it at another project with `--cwd`.
- **Editor-ready.** The same engine speaks ACP over stdio, so editors like Zed can
  drive it directly — no separate integration.
- **Skills.** The router picks relevant engineering-methodology skills per request
  and injects them into the agent's context — test-driven development, systematic
  debugging, verification-before-completion, and more (see *System skills* below).
- **You're in control.** The agent asks permission before running commands, and
  you can cancel an in-flight turn at any time.
- **Fully traceable.** Every run is recorded as structured `events.jsonl` and
  `traj.json` for replay and debugging.
- **Try it for free.** A built-in mock model lets you run the whole thing at zero
  cost before plugging in a real LLM.

## Quick start

DoneDone needs Python 3.11+. Install the `dn` command with
[uv](https://github.com/astral-sh/uv):

```bash
# Use it (portable): builds a wheel and installs dn globally. Works from any
# directory and keeps working even if you delete this checkout.
uv tool install .

# OR — develop it (always-latest): dn runs your live source; edits to code and
# to skills in harness/skills/ apply immediately, no reinstall.
uv tool install --editable .
```

Configure VibeProxy by putting your settings in `~/.config/harness/.env`
(see `.env.example`), or drop a `.env` in the project directory you run `dn`
from. Add your own skills in `~/.config/harness/skills/` — they override the
bundled ones of the same name. (`$XDG_CONFIG_HOME` is honored if set.)

The harness remembers your selected model across sessions in
`~/.config/harness/done.conf` (TOML). Changing the model at runtime saves it
to the reserved `default` agent there; passing `--model` at launch overrides
the saved value for that session without erasing it.

Either install puts two commands on your `PATH`:

| Command | What it is |
|---|---|
| `dn` | the terminal UI (TUI) — the everyday way to use DoneDone |
| `dn-agent` | the raw ACP agent server, for editor clients like Zed |

Then run it:

```bash
dn                       # zero-cost mock model; operates on the current directory
dn --model vibeproxy     # a real LLM, via VibeProxy
dn --cwd ~/myproject     # operate on a specific project instead of the cwd
```

| Flag | Values | Default | Meaning |
|---|---|---|---|
| `--model` | `mock`, `vibeproxy` | `mock` | which LLM the agent uses |
| `--cwd` | a path | `.` | the working directory the agent operates in |
| `--yolo` | flag | off | auto-allow every command — never prompt for permission |

## System skills

DoneDone ships with a curated set of engineering-methodology skills (imported from
[obra/superpowers](https://github.com/obra/superpowers), MIT). The router
auto-selects the relevant ones per request and injects them into the agent's
context:

| Skill | When the router picks it |
|---|---|
| `test-driven-development` | implementing a feature or bugfix — write the failing test first |
| `systematic-debugging` | a bug, test failure, or unexpected behavior — root-cause before fixing |
| `verification-before-completion` | before declaring work done — prove it actually works |
| `receiving-code-review` | responding to code-review feedback |

Add your own skills in `~/.config/harness/skills/<name>/SKILL.md`; a user skill
with the same name as a bundled one overrides it.

## Personas

A **persona** gives the agent an identity — tone, boundaries, and who it's
talking to. Where skills are task-knowledge the router selects per request, a
persona is a small set of plain-text files injected into the agent's context for
the whole session, on **both** the chat and coding paths.

A persona lives in a workspace directory. The built-in one is
`~/.config/harness/agents/default/`, and it reads three files (all optional):

| File | Role |
|---|---|
| `SOUL.md` | persona, tone, boundaries — "who are you?" |
| `IDENTITY.md` | name / vibe / emoji |
| `USER.md` | who the user is (static context you write) |

A fresh install seeds these files for you as inert templates (just a commented
hint, so they change nothing until edited). Edit one to give the agent a persona:

```bash
echo "You are terse and never explain unless asked." > ~/.config/harness/agents/default/SOUL.md
dn   # the agent now answers in that persona, on chat and coding turns alike
```

Until you edit a file, behavior is unchanged — no persona, no overhead. See
[docs/personas.md](docs/personas.md) for the full reference (seeding, trimming,
blank/inert-skip, the dev path, and what's coming in later phases: multiple
personas, selection, memory, scheduling).

### Selecting a persona

Run as a named persona workspace with `--persona <id>`:

    dn --persona fred

Without `--persona`, the built-in `default` persona is used. The id must be an
existing workspace under `~/.config/harness/agents/<id>/` — an unknown id is a
hard error (persona *creation* lands in a later phase). Each persona has its own
sessions, memory, and model (persisted in `done.conf` under `[agents.<id>]`); a
live `/models` swap is remembered per persona.

## Using the TUI

- Type a prompt in the input box and press **Enter** to send. Input is disabled
  while a turn streams, then re-enabled.
- **Esc** cancels the in-flight turn (best-effort, at the next command boundary).
- **Ctrl-Q** quits and tears down the agent cleanly.

As a turn streams, the transcript shows:

- the agent's streamed messages and reasoning;
- each shell command as a tool call (`$ <command>`) with a live status
  (`pending` → `completed ✓` / `failed ✗`);
- a **permission prompt** (Allow / Reject) before a command runs, when the agent
  asks;
- DoneDone's own status chips — which request type was detected and which skills
  were loaded for the turn.

### Design system & brand book

The TUI has a documented design system: one semantic palette
(`harness/tui/theme.py`), one glyph vocabulary (`harness/tui/tokens.py`), and a
component catalog (`harness/tui/styles/components.md`).

To **see** it rendered — the palette, glyph map, status states, and the
components that ship today, drawn on the real terminal background — open the
living brand book:

```bash
open harness/tui/styles/brandbook.html        # macOS (or open in any browser)
```

It is generated from the live tokens, so it never drifts from the running app.
Regenerate it after any token or component change:

```bash
.venv/bin/python -m harness.tui.styles.brandbook
```

## Use it from an editor (ACP)

The engine is an ACP server, so any ACP client can drive it. Start it directly:

```bash
dn-agent --model mock          # zero-cost mock model
dn-agent --model vibeproxy     # real LLM via VibeProxy
```

It speaks ACP over stdin/stdout — an editor (e.g. Zed) connects and drives
sessions: sending prompts, receiving streamed message chunks, cancelling, and
resuming prior sessions by ID. All the agent's capabilities (tracing, skills,
permissions, filesystem/terminal delegation) are available through this
interface. See `tests/test_acp_smoke.py` for a worked example client.

## Architecture

DoneDone separates the **engine** from its **clients**, and every client speaks
the same protocol:

```
┌─────────────┐        ┌─────────────┐        ┌──────────────────┐
│  dn (TUI)   │        │  Zed / IDE  │        │  your automation │
└──────┬──────┘        └──────┬──────┘        └────────┬─────────┘
       │                      │   Agent Client Protocol │
       └──────────────────────┴────────────(stdio)─────┘
                              │
                  ┌───────────▼────────────┐
                  │   DoneDone ACP agent    │
                  │  router · skills · trace│
                  └───────────┬─────────────┘
                              │
                  ┌───────────▼────────────┐
                  │ mini-swe-agent (engine) │
                  └─────────────────────────┘
```

The engine was grown in phases, each adding one layer on top of the unmodified
vendored agent:

| Layer | What it adds |
|---|---|
| **Tracer** | structured `events.jsonl` / `traj.json` for every run |
| **Runner** | drives the engine against a real repository |
| **Router** | classifies each request and decides how to handle it |
| **Skills** | injects relevant skills into the agent's context per turn |
| **ACP agent** | exposes the engine as an ACP server over stdio |
| **TUI** | a Textual ACP client (`dn`) that drives the agent like an editor would |

## Repository layout

| Path | Contents |
|---|---|
| `harness/` | the DoneDone package — tracer, router, skills, mock model, runner, ACP server |
| `harness/tui/` | the Textual ACP client (render core, `acp.Client`, app); entrypoint `harness/tui_main.py` |
| `harness/tui/styles/` | design system — component catalog (`components.md`) + living brand book (`brandbook.html`, generated by `brandbook.py`) |
| `upstream/` | vendored mini-swe-agent — never edited |
| `harness/skills/` | bundled system skills the router can inject (+ `NOTICE.md` attribution) |
| `examples/sample-repo/` | a tiny repo with one failing test, for demos |
| `docs/` | spec, plan, and learning log |

## Development

The project uses a Python 3.11 virtualenv with the vendored engine installed
editable:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ./upstream pytest
.venv/bin/pip install -e .          # install DoneDone itself, editable
```

Run from a source checkout without installing the console scripts:

```bash
.venv/bin/python -m harness.tui_main          # the TUI
.venv/bin/python -m harness.acp_main --model mock   # the ACP agent
```

### Try the zero-cost demo

```bash
./run.sh --model mock
```

This streams events to the console and writes `harness/runs/<ts>/events.jsonl`
and `traj.json`. The mock model fixes the failing test in `examples/sample-repo`.
Reset between runs with:

```bash
git checkout examples/sample-repo/calculator.py
```

### Run against a real model (VibeProxy)

Copy `.env.example` to `.env`, make sure VibeProxy is running on `:8317`, then:

```bash
./run.sh --model vibeproxy --task "Fix the failing test in examples/sample-repo."
```

### Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## License

See [`LICENSE`](LICENSE). DoneDone bundles
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) under `upstream/`,
which carries its own license.

---

<div align="center">
<sub><strong>DoneDone</strong> · <a href="https://donedone.io">donedone.io</a> · execution over appearance</sub>
</div>
