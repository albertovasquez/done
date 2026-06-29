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
- **Skills.** The agent sees a lightweight menu of available skills and pulls the
  full instructions on demand (lazy `load_skill`), so a large skill set costs almost
  nothing until used. Conforms to the [Agent Skills](https://agentskills.io) open
  standard, so Done reads your project `.agents/skills` / `.claude/skills` too (see
  *Skills* below).
- **Memory.** Each persona keeps a persistent, per-persona memory in plain Markdown
  — a small index injected every turn plus a lazy `load_memory` tool, same shape as
  skills. No database, no embeddings, no-op until used (see *Memory* below).
- **Instructions.** Drop an `AGENTS.md` in your project (or persona, or `~/.config/harness/`)
  and it becomes standing policy in the agent's prompt (see [docs/agents-md.md](docs/agents-md.md)).
- **Jobs.** Schedule a persona to run work unattended — a nightly backup, an hourly
  check, a reminder. A background `harness-cron` daemon fires each job *as the persona
  that owns it* (same model, workspace, and memory), with cost/permission gates at
  creation time (see [docs/jobs.md](docs/jobs.md)).
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

If you used the non-editable `uv tool install .`, the tool keeps its own pinned
virtualenv — a `git pull` updates your checkout but **not** that environment. So
whenever a pull adds or changes a dependency (you'll see a `ModuleNotFoundError`
on launch), refresh the installed tool:

```bash
uv tool install --reinstall --force --from . quiubo-done
```

(The `--editable` install runs live source and never needs this.)

### Try it immediately (no LLM needed)

```bash
dn --model mock     # zero-cost mock model — no API key, no VibeProxy, works right now
```

The mock model simulates the agent loop so you can explore the TUI before connecting a
real LLM. It fixes the sample failing test in `examples/sample-repo` and demonstrates
skills, permissions, and the cron dashboard.

### Connect a real LLM (VibeProxy)

DoneDone routes LLM calls through **VibeProxy**, an OpenAI-compatible local proxy
that must be running on `:8317`. Once it is, configure the model:

```bash
# Put your settings in ~/.config/harness/.env (copy .env.example as a starting point):
cp .env.example ~/.config/harness/.env
# Edit ~/.config/harness/.env: set VIBEPROXY_MODEL to a model your VibeProxy serves.
# Then launch:
dn
```

You can also drop a `.env` in the project directory you run `dn` from instead of
`~/.config/harness/.env`. Add your own skills in `~/.config/harness/skills/` (global)
or a project's `.agents/skills` / `.claude/skills` (see *Skills* below); a user skill
overrides a bundled one of the same name. (`$XDG_CONFIG_HOME` is honored if set.)

The harness remembers your selected model across sessions in
`~/.config/harness/done.conf` (TOML). Changing the model at runtime saves it
to the reserved `default` agent there; passing `--model` at launch overrides
the saved value for that session without erasing it.

### Context compaction

DoneDone automatically compresses the conversation transcript when it grows
large, so long sessions don't hit the model's context limit. Compaction is
**on by default** — no config needed. To opt out or tune it, add a
`[compaction]` block to `~/.config/harness/done.conf`:

```toml
[compaction]
enabled       = true    # set to false to disable entirely
threshold     = 0.5     # compress when transcript exceeds this fraction of ctx_window
target_ratio  = 0.2     # keep this fraction of ctx_window in the retained tail
protect_head_n = 0      # never compress the first N messages
protect_last_n = 20     # always keep the last N messages verbatim
# ctx_window is auto-resolved from the model (curated table → litellm → 32000).
# Only set it to override for an unknown or proxy model:
# ctx_window = 128000
```

With `--debug`, three compaction events appear in the JSONL trace every turn:
`context.compaction.eval` (prior tokens, budget, decision),
`context.compaction.summarize` (in/out tokens, cost, elapsed — only when a
summary is produced), and `context.compacted` (before/after message + token
counts — only when compaction actually fires).

Either install puts two commands on your `PATH`:

| Command | What it is |
|---|---|
| `dn` | the terminal UI (TUI) — the everyday way to use DoneDone |
| `dn-agent` | the raw ACP agent server, for editor clients like Zed |

Then run it:

```bash
dn --model mock          # start here — zero-cost mock model, no setup required
dn                       # real LLM via VibeProxy (requires ~/.config/harness/.env)
dn --cwd ~/myproject     # operate on a specific project instead of the cwd
```

| Flag | Values | Default | Meaning |
|---|---|---|---|
| `--model` | `mock`, `vibeproxy` | `vibeproxy` | which LLM the agent uses |
| `--cwd` | a path | `.` | the working directory the agent operates in |
| `--yolo` | flag | off | auto-allow every command — never prompt for permission |

## Skills

DoneDone ships a curated **maturity spine** — general skills that make the agent
work like a professional (reframe before acting, plan before coding, root-cause
before fixing, prove work before claiming done). The agent gets a lightweight
**menu** (skill names + one-line descriptions); it pulls a skill's full instructions
on demand with the `load_skill` tool, so the menu stays cheap no matter how many
skills exist.

| Skill | What it enforces |
|---|---|
| `clarify-before-acting` | tell a question from a work order — answer/scope before editing |
| `planning-before-coding` | lock architecture, edge cases, and the test surface before code |
| `systematic-debugging` | root-cause before fixing (the Iron Law); stop after 3 failed fixes |
| `test-driven-development` | write the failing test first, then minimal code |
| `verification-before-completion` | prove work actually works before declaring it done |
| `receiving-code-review` | fold feedback with rigor, not reflexive agreement |
| `ask-done` | user-invoked (`/ask-done`) — recommends which skill/flow fits your situation |

Each `SKILL.md` carries an **invocation model** in its frontmatter: `disable-model-invocation`
(user-only, like `ask-done`), `user-invocable`, and a `flow` tag. **Flows** group
skills into families (e.g. a future `seo`/`marketing` flow) enabled per-persona in
`persona.toml`; global skills (no flow tag) are always available.

### Adding your own skills

DoneDone conforms to the [Agent Skills](https://agentskills.io) open standard, so it
reads skills from these roots (later wins on a name clash):

```
bundled                       (the maturity spine)
~/.claude/skills              (ecosystem skills — consumed for free)
~/.config/harness/skills      (your global Done skills — native, outranks compat)
<cwd>/.claude/skills          (a project's ecosystem skills)
<cwd>/.agents/skills          (a project's skills — the cross-tool standard, highest)
```

Drop a `<name>/SKILL.md` (frontmatter needs `name` matching the directory + a
`description`) in any of these. A malformed or name-mismatched skill is surfaced to
you (with the reason) rather than silently ignored. See
[docs/router-flows.md](docs/router-flows.md) for the full skills/flows reference.

> Skills shipped in `harness/skills/` are imported from / adapted after
> [obra/superpowers](https://github.com/obra/superpowers),
> [garrytan/gstack](https://github.com/garrytan/gstack), and
> [mattpocock/skills](https://github.com/mattpocock/skills) — see `harness/skills/NOTICE.md`.

## Personas

A **persona** gives the agent an identity — tone, boundaries, and who it's
talking to. Where skills are task-knowledge the agent pulls on demand, a persona
is a small set of plain-text files injected into the agent's context for the whole
session, on **both** the chat and coding paths.

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
blank/inert-skip, the dev path, selection, in-process switching, and creation).

### Selecting a persona

Run as a named persona workspace with `--persona <id>`:

    dn --persona fred

Without `--persona`, the built-in `default` persona is used. The id must be an
existing workspace under `~/.config/harness/agents/<id>/` — an unknown id is a
hard error. (To make a new persona without leaving the TUI, press **n** in the
agents rail; see below.) Each persona has its own sessions, memory, and model
(persisted in `done.conf` under `[agents.<id>]`); a live `/models` swap is
remembered per persona.

### The agents rail (TUI)

Press **Tab** (or `/persona`) to open the **agents rail** — it lists every persona
workspace under `~/.config/harness/agents/`, with the active one marked. Display
names come from each workspace's `persona.toml` `name` (the id is used if unset).
**Esc** closes the rail. The status bar also shows which persona you're on.

Select a persona in the rail to **switch to it in-process** — the same long-lived
agent process repoints to that persona's session, memory, and model, with no
restart and no `--persona` relaunch (the way mature agent harnesses do it). Press
**n** to **create** a new persona: name it, and the rail slugifies the name into a
safe workspace id, seeds the inert templates, and switches to it.

### Deleting a persona

There is no delete command yet — removal is manual. A persona's state lives in up
to three places, all under `~/.config/harness/`:

1. **Its workspace directory** — `agents/<id>/`. This holds everything the persona
   *is*: `SOUL.md`, `IDENTITY.md`, `USER.md`, `persona.toml`, **and its memory**
   (`MEMORY.md` + the `memory/` folder live inside the workspace). Deleting the
   directory removes the persona from the rail and erases its memory:

   ```bash
   rm -rf ~/.config/harness/agents/<id>
   ```

2. **Its model config** — the `[agents.<id>]` table in `done.conf` (backend, model,
   yolo-pin). Deleting the directory leaves this behind. It's harmless — the rail
   lists *directories*, not config entries, so an orphaned table is never read —
   but to remove it cleanly, delete the `[agents.<id>]` section from
   `~/.config/harness/done.conf`. (A persona you never set a custom model for has
   no entry here.)

3. **Any scheduled jobs** — entries in `cron/jobs.json` with `"agent_id": "<id>"`.
   If you leave these, the next run auto-disables the job (it can no longer resolve
   the persona) rather than crashing. To remove them up front, delete those entries
   from `~/.config/harness/cron/jobs.json` (and optionally their logs under
   `cron/runs/`).

Sessions and debug traces are keyed by run, not by persona, so there's nothing
else to clean up. You can't delete the built-in `default` persona this way — it's
re-seeded on the next launch.

## Memory

Each persona has a **persistent memory** in its workspace — plain Markdown files,
no database, no embeddings, no external service, **per-persona isolated**, and a
strict no-op until used. Memory mirrors the skills system: an **index** the agent
sees every turn plus a **load-on-demand tool**.

| Layer | What |
|---|---|
| `MEMORY.md` | the durable index — injected at the start of every turn (trimmed at 8 K chars) |
| `memory/<date>.md` | daily notes — today's and yesterday's auto-injected |
| `memory/<slug>.md` | typed facts — listed in the index, pulled on demand with `load_memory` |

Facts carry frontmatter (`name` / `description` / `type` where `type` is one of
`user` · `feedback` · `project` · `reference`). The agent writes memory itself via
plain shell (or just tell it "remember that…"); you can hand-edit any file. Names
are resolved strictly inside the active workspace, so one persona can never read
another's memory.

Memory deliberately has **no search index or semantic recall yet** — Done weighed
adopting [QMD](https://github.com/tobi/qmd) (a Node sidecar with a ~2 GB model
download, as OpenClaw uses) and chose to keep memory Python-only and dependency-free;
because the files stay source of truth, FTS/QMD can be added later as an additive
layer. See [docs/memory.md](docs/memory.md) for the full reference.

## Jobs (cron)

A **job** runs a persona on a schedule, unattended. Each job is bound to one
persona via a required `agent_id`, and a background **`harness-cron`** daemon
fires it *as that persona* — same model, workspace, memory, and AGENTS.md as a
live turn. If the persona is gone, the job auto-disables instead of running as
someone else.

You don't type a create command: just ask the persona in chat — *"remind me every
Monday at 9am to review deploy metrics"* — and the **`create-job` skill** turns
your plain-language intent into a job. It's guess-first: safe defaults for
timeout / cadence / failures / permissions, asking only when the schedule is
unclear or a risky permission (shell, network, writes outside the project) is
needed. It writes through the single `create_job` tool. Schedules can be a 5-field
cron (`0 2 * * *`), a fixed interval, or a one-shot timestamp.

### Keeping scheduled jobs running

For jobs to fire after a reboot — or when no `dn` window is open — register the
daemon as an OS service:

```sh
dn cron install      # macOS: launchd LaunchAgent; Linux: systemd user service
dn cron status       # show whether the service is installed/active
dn cron uninstall    # remove it
```

On macOS this writes a LaunchAgent plist
(`~/Library/LaunchAgents/com.quiubo.done.cron.plist`) with RunAtLoad + KeepAlive,
so the daemon starts at login and restarts on crash. On Linux it writes a systemd
**user** unit (`~/.config/systemd/user/harness-cron.service`, `Restart=always`)
and enables lingering so it survives logout and reboot.

The **first time you launch `dn`**, it offers to run `dn cron install` for you.
If you decline (or you're on an unsupported platform), jobs still fire while a
`dn` window is open via a best-effort background spawn — but they won't survive a
reboot or fire with all windows closed. Run `dn cron install` any time to make it
permanent.

For headless use or a custom tick cadence, you can also run the daemon directly:

```sh
harness-cron            # run the daemon (ticks every 30 s)
harness-cron --once     # fire all due jobs once and exit
```

In the TUI, **Ctrl+J** toggles the cron dashboard (status per job + a run-duration
chart); the header shows whether ticks are firing. `r`/`t`/`Backspace` run-now,
toggle, and remove (creation is in chat, as above). Jobs live in
`~/.config/harness/cron/`.

> Phase 1: the permission `grant` is **recorded but not yet enforced at runtime** —
> a job can currently do whatever its persona could. Prefer narrow, low-privilege
> tasks. See [docs/jobs.md](docs/jobs.md) for the full reference.

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
| **Skills** | a lazy skill menu + `load_skill` tool (agent pulls bodies on demand); per-persona flows; `AGENTS.md` standing instructions |
| **Memory** | per-persona Markdown memory: a startup index + lazy `load_memory` tool (same shape as skills) |
| **ACP agent** | exposes the engine as an ACP server over stdio |
| **TUI** | a Textual ACP client (`dn`) that drives the agent like an editor would |

## Repository layout

| Path | Contents |
|---|---|
| `harness/` | the DoneDone package — tracer, router, skills, mock model, runner, ACP server |
| `harness/tui/` | the Textual ACP client (render core, `acp.Client`, app); entrypoint `harness/tui_main.py` |
| `harness/tui/styles/` | design system — component catalog (`components.md`) + living brand book (`brandbook.html`, generated by `brandbook.py`) |
| `upstream/` | vendored mini-swe-agent — never edited |
| `harness/skills/` | the bundled maturity spine, lazily loaded via `load_skill` (+ `NOTICE.md` attribution) |
| `examples/sample-repo/` | a tiny repo with one failing test, for demos |
| `docs/` | reference docs (skills/flows, AGENTS.md, personas, memory, debugging), specs, plans, learning log |

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

You only ever create **one** `.venv`, at the repo root. Development happens in git
worktrees (see *AGENTS.md* #1), and `tests/conftest.py` makes `pytest` always import
the source of **whichever worktree the tests live in** — no per-worktree venv, and no
need to `cd` anywhere first. So this works from any directory:

```bash
.venv/bin/python -m pytest path/to/worktree/tests/ -v   # tests that worktree's code
```

(The root editable install pins an absolute import path; conftest shadows it so a
worktree's tests never silently run the root checkout's code.)

## License

See [`LICENSE`](LICENSE). DoneDone bundles
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) under `upstream/`,
which carries its own license.

---

<div align="center">
<sub><strong>DoneDone</strong> · by Bitlabs · <a href="https://donedone.io">donedone.io</a> · execution over appearance</sub>
</div>
