# Persona name slugify + display name (design)

**Date:** 2026-06-28 · **Base:** `main` @ `76d2030` (persona-create + UX fixes merged,
PRs #73/#74) · **Worktree:** `.claude/worktrees/persona-slugify` (branch `persona-slugify`).

A follow-on to persona-create: let users type ANY name in the create modal and slugify it
to a safe id, preserving the typed name as the display name.

---

## 1. What this is + why

Today the create modal rejects any name that isn't `^[a-z0-9_-]+$` — so `Alberto`,
`My Persona`, `Fred Smith` silently fail (a capital or space is rejected). The strict
charset exists for a real reason: the persona id is BOTH a directory name AND a TOML key
in `done.conf` (`[agents.<id>]`); **dots produce nested-table keys that silently lose the
persisted model** (a real C1 bug), and spaces/uppercase break TOML. The fix is NOT to
loosen the storage rule — it's to **normalize friendly input into a safe id** in front of
it, and keep the typed name as a display label.

## 2. The decisions (locked in brainstorm, 2026-06-28)

1. **Live preview** — as the user types, the modal shows `→ will be created as: <slug>`
   (or a hint when the slug is empty). No surprise about the final id.
2. **Show the typed name in the rail** — the typed `My Persona` is saved as the persona's
   display name (`persona.toml` `name`, which the rail already reads via `read_name`); the
   slug `my-persona` is the internal id (folder + config key).

## 3. Architecture & the slugify seam

The strict `_VALID_ID` charset stays EXACTLY as-is (the storage contract). We add a
normalization layer in front of it. One new pure helper + three small wiring changes.

**The core (new) — `harness/persona_select.py`:**
- `slugify_persona_name(raw: str) -> str` — pure, next to `_VALID_ID` (its natural home).
  Steps: lowercase → strip → replace runs of any non-`[a-z0-9]` (spaces, `_`, `.`, etc.)
  with a single `-` → collapse repeated `-` → trim leading/trailing `-`. Returns the slug,
  or `""` when nothing valid survives (e.g. `"!!!"`, `"😀"`, `""`). **Invariant: a non-empty
  return ALWAYS satisfies `_VALID_ID`** (so the downstream safety net never fires on a
  slugified id).

**Three wiring points:**
1. **Modal** (`harness/tui/widgets/new_persona_modal.py`) — on each keystroke compute the
   slug, show `→ will be created as: <slug>` under the input (or "enter letters or numbers"
   when empty). On submit, pass the **raw typed name** to the app (the app owns the real
   slug + the display name; the modal's slug is display-only).
2. **App** (`harness/tui/app.py` `_do_create_persona`) — slug the raw name; if empty →
   `{ok:false, error:"enter a name with letters or numbers"}` (no ext call); else call the
   ext-method with `{id: slug, display_name: raw}`.
3. **Engine** (`harness/persona.py` `create_persona` + the ext-method) — `create_persona`
   gains an optional `display_name`; the `_VALID_ID`/reserved/exists checks stay (defense in
   depth — the id is expected pre-slugified but still validated); when `display_name` is
   given, write `name = "<display_name>"` to the new workspace's `persona.toml`. The
   `harness/create_persona` ACP ext-method forwards `params["display_name"]`.

**Why this shape:** slugify is one testable pure function; `_VALID_ID` stays as the
last-line guard so even a bad caller can't write an unsafe id; the display name rides to
`persona.toml`, which the rail already reads. No second validation home; no loosening of
the storage rule; no new dependency (the `name = "..."` line is written directly, not via
a TOML-writer lib — it's a single string value).

## 4. Components & data flow

### New / changed units
- `slugify_persona_name(raw) -> str` (NEW, `persona_select.py`) — pure normalizer.
- `create_persona(persona_id, display_name: str | None = None) -> Path` (MODIFIED,
  `persona.py`) — optional display-name write to `persona.toml`. *Depends on:* `paths`,
  `persona_select` (validation), the existing `_copy_persona_templates`.
- `_write_persona_name(workspace_dir, display_name) -> None` (NEW private, `persona.py`) —
  write `name = "<display_name>"` to `<workspace_dir>/persona.toml`. TOML-escape the value
  (escape `\` and `"`); best-effort (swallow OSError — never fail the create over a label).
- `harness/create_persona` ext-method (MODIFIED, `acp_agent.py`) — forwards
  `display_name` from params to `persona.create_persona`.
- `NewPersonaModal` (MODIFIED) — live slug preview; submit passes the raw name.
- `_do_create_persona` (MODIFIED, `app.py`) — slug + empty-guard + carry display_name.

### Data flow (create "My Persona")
```
type "My Persona"
  → keystroke: slug = slugify_persona_name("My Persona") = "my-persona"
       status: "→ will be created as: my-persona"   (empty slug → hint, Enter no-op)
  → Enter → modal hands the RAW "My Persona" to app._do_create_persona
  → app: slug = slugify_persona_name(raw)
       if not slug → {ok:false, error:"enter a name with letters or numbers"}
       resp = ext_method("harness/create_persona",
                         {"id":"my-persona", "display_name":"My Persona"})
  → engine: create_persona("my-persona", display_name="My Persona")
       _VALID_ID ok (safety net) · not reserved · target free
       copy trio + _write_persona_name(ws, "My Persona")  -> persona.toml: name = "My Persona"
  → _activate_seat("my-persona") → {ok, id:"my-persona", session_id, model}
  → modal dismiss(resp) → _apply_persona_switch → rail refresh
  → rail shows "● My Persona" (read_name); id "my-persona" internal
  → transcript: "created persona: My Persona — now talking to it"
```
The modal slugs for the PREVIEW; the app re-slugs for the REAL id (single source of truth,
no trust gap).

## 5. Error handling / edge cases

| Input | Slug | Behavior |
|---|---|---|
| `My Persona`, `Alberto`, `Fred.Smith` | `my-persona` / `alberto` / `fred-smith` | created; rail shows the typed name |
| `  spaced  ` | `spaced` | trimmed |
| `a---b__c.d` | `a-b-c-d` | separator runs collapsed |
| `!!!`, `😀`, `` (empty) | `""` | **rejected** — modal hint "enter a name with letters or numbers"; Enter no-op; app also returns `{ok:false}` (defense in depth) |
| `default` (or anything slugging to it) | `default` | **rejected** — reserved; engine raises `InvalidPersonaId`; modal shows the error, stays open |
| `My Persona` when `my-persona` exists | `my-persona` | **`PersonaExists`** — modal shows "already exists", stays open |
| two display names slugging the same | same slug | second collides (`PersonaExists`) — correct: the id is the identity, the display name does NOT disambiguate |

- **Empty slug** caught at BOTH modal (Enter no-op + hint) AND app (`{ok:false}`).
- **display_name write failure** (read-only home / bad write) is NON-FATAL: the persona is
  still created + usable; `read_name` returns None on any read failure so the rail falls
  back to the id. A failed label never blocks creation.
- **TOML-escaping** the display name: escape `\` then `"`, and strip control chars
  (newlines, etc.) before writing `name = "..."`, so a crafted name can't break the file or
  inject a second key. (`read_name` already tolerates a corrupt file by returning None, but
  we write valid TOML.)
- **Accented/ligature chars are dropped, not transliterated** (`café` → `caf`, `ﬁle` →
  `le`) — verified the slug invariant holds (every non-empty result satisfies `_VALID_ID`).
  Full transliteration (`café`→`cafe`) would need a dependency; out of scope (YAGNI). The
  typed name is preserved verbatim as the display label, so the lossy slug is internal-only.

## 6. Testing (TDD, per unit)

| Unit | Test |
|---|---|
| `slugify_persona_name` | `"My Persona"`→`my-persona`; `"Alberto"`→`alberto`; `"Fred.Smith"`→`fred-smith`; `"  a  "`→`a`; `"a---b__c.d"`→`a-b-c-d`; `"!!!"`/`"😀"`/`""`→`""`; `"my-persona"`→`my-persona` (idempotent passthrough); **property: every non-empty result matches `_VALID_ID`** |
| `create_persona(id, display_name)` | writes `name="My Persona"`, `read_name(ws)=="My Persona"`; a name with a `"`/`\` is escaped + round-trips; no display_name → persona.toml has no `name` (or no file); the `_VALID_ID` safety net still raises on an unsanitized id; write failure (monkeypatched OSError) → persona STILL created (no raise) |
| `harness/create_persona` ext-method | `{id, display_name}` forwarded; persona on disk has the display name; missing display_name still works |
| `_do_create_persona` (app) | slugs the raw name; empty slug → `{ok:false}` + NO ext call; valid → ext call carries `{id: slug, display_name: raw}` |
| modal preview | typing updates "→ will be created as: <slug>"; empty slug shows the hint; Enter on empty-slug is a no-op (no create) |
| rail display | a created persona with a display name renders the display name (existing `read_name`/`persona_rows` path) |

**Test-harness reminders:** persona-on-disk fixtures use `tmp_path/agents/<id>` + `XDG_CONFIG_HOME`
(`tests/test_acp_agent.py`, `tests/test_persona.py`). ext_methods via `asyncio.run`. TUI via
the `run_test()` pilot (`tests/test_tui_pilot.py`). Editable-install shadowing — run worktree
pytest with the WORKTREE as cwd.

## 7. Crux tasks for Codex adversarial review

- `slugify_persona_name` — the gate. It MUST never return a non-empty string that
  `_VALID_ID` rejects (else the downstream safety net fires and create fails on a "valid"
  name). Attack with unicode, mixed separators, leading/trailing junk, strings that are all
  separators, very long names.
- `create_persona` display_name write — must be non-fatal (create succeeds even if the
  write fails) and must write VALID TOML (escaping) so a quote/backslash in the name can't
  corrupt persona.toml.

## 8. Guardrails

- **The storage rule is unchanged:** `_VALID_ID` (`^[a-z0-9_-]+$`) + reserved "default" +
  no-clobber stay as the engine's last-line validation. Slugify is a layer in FRONT, never a
  replacement.
- **No new dependency:** the `name = "..."` write is a single escaped string line, not a
  TOML-writer lib.
- **No-op / no regression:** persona-create without a display_name behaves exactly as today
  (no persona.toml `name` written). The default seed is untouched.
- **Reuse:** the rail's display-name path (`read_name` → `persona_rows`) is reused as-is; no
  new rail logic.
- **Work in the worktree** (AGENTS.md #1); editable-install shadowing (§6).

## 9. Definition of done

- Typing `My Persona` (or `Alberto`, `Fred.Smith`) in the create modal creates a persona —
  the modal previews the slug, the rail shows the typed name, the id is the safe slug.
- A name that can't be slugified (`!!!`, emoji, empty) is rejected with a clear hint, never
  silently; Enter is a no-op.
- Reserved / collision still produce a clear inline error and keep the modal open.
- The display name is written to persona.toml (escaped, non-fatal); the strict charset
  safety net is intact.
- Codex-reviewed; full suite green; PR against `main`; shipped.
