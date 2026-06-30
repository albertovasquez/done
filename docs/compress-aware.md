# Compress-aware mode

Done can load a **pre-compressed copy** of your prose context files into the
agent's prompt instead of the originals, cutting input tokens every turn. It is
**opinionated and on by default**, fully reversible, and never compresses on the
hot path — all compression happens offline, ahead of time.

The guiding rule: **compress the input, never the response.** Compression only
shrinks what Done *sends into* the model. It never changes how your agent
*sounds* — the personality in your soul/identity files comes through in full.

## How it works: shadow files

For any context file `FOO.md`, Done looks for an optional sibling
`FOO.compressed.md`. When compress-aware mode is on and a **fresh** sibling
exists, Done loads the compressed sibling (its metadata header stripped) in
place of the original. Otherwise it loads the original, untouched.

```
AGENTS.md              the human-facing original — never modified
AGENTS.compressed.md   optional shadow — loaded instead, when fresh
```

- **Presence = opt-in.** No sibling → normal behavior. Delete a sibling and that
  file instantly reverts to its original. The feature fails safe.
- **The original is never mutated** (memory is the one exception — see below).
- **No LLM on the read path.** Choosing sibling-vs-original is pure file I/O.

## Which files

Compress-aware applies to the prose files injected into the prompt each turn:

```
SOUL.md  IDENTITY.md  USER.md      the persona "voice" trio (per persona)
MEMORY.md                          the durable memory index (per persona)
AGENTS.md  CLAUDE.md               standing-instruction tiers (project/persona/global)
```

Daily memory notes are left alone, and so is the agent's response — only the
inputs above are ever compressed.

### Skills (cached, opt-in via `dn compress --skills`)

Skill *bodies* (the prose after a skill's frontmatter) can be compressed too —
but never as files next to the source skills, which may be bundled in the wheel
or shared with other tools (`~/.claude/skills`, project dirs). Instead they live
in a **Done-owned side cache** under `~/.config/harness/compress-cache/skills/`,
keyed by a hash of the source body + the compression-rules version (so a skill
edit or a rules change is an automatic clean miss).

Unlike the per-turn shadow files, the skill cache has no metadata header — freshness is encoded entirely in the cache filename (source body + compression-rules version).

Build the cache offline:

```bash
dn compress --skills      # compress every skill's body into the side cache
```

On `load_skill`, a fresh cached body is served in place of the original; a miss
loads the original (no LLM on the read path). The skill **menu** is never
compressed — it's tiny and frontmatter-only. Like all compress-aware behavior,
this is gated on the `compress_aware` flag.

## Freshness: when a sibling is used

A sibling is only used when it is provably **current**. Each compressed file
carries a metadata header recording three fingerprints, and **all three must
match** or Done silently falls back to the original:

1. **Source hash** — the sibling was built from the current contents of the
   source file (edit the source → the sibling is stale).
2. **Engine + rules version** — the sibling was built by the current
   compression rules (improve the rules → old siblings are invalidated).
3. **Body hash** — the sibling hasn't been hand-edited or tampered with.

A stale, missing, corrupt, or unsafe sibling always degrades to the truthful
original — staleness never serves you the wrong context. (Siblings are also
path-checked: symlinked or out-of-tree siblings are rejected.)

## Regenerating siblings: `dn compress`

Compression is **offline only** — Done never compresses mid-turn. You rebuild
siblings with the CLI:

Siblings also **auto-refresh on session end**: when you quit the TUI, Done
detects any *existing* sibling that has gone stale and rebuilds it in the
background (detached, never blocking quit). It never creates a sibling you
didn't ask for — `dn compress <path>` is still how you opt a file in the first
time. See `docs/hooks.md` for the mechanism.

```bash
dn compress                 # rebuild siblings for cwd AGENTS.md / CLAUDE.md
dn compress path/to/FILE.md # rebuild a specific file's sibling
dn compress --status        # report each file's size delta and freshness
```

`dn compress` needs a model. It resolves one in this order: the `COMPRESS_MODEL`
env var (one-off override) → **`[harness] compress_model` in `done.conf`** (the
persistent home — set this to a small/fast model like a haiku id) → `VIBEPROXY_MODEL`
→ your default agent's model. With none configured it reports that compression is
unavailable and does nothing. Compression is a cheap, mechanical task, so a small
model is the right default:

```toml
# ~/.config/harness/done.conf
[harness]
compress_model = "claude-haiku-4-5-20251001"
```

The compressor preserves code blocks, inline code, URLs, file paths, commands,
version numbers, and headings exactly, and validates its output — if it can't
produce a faithful compression, it leaves the file alone.

`--status` is the safe way to see what you're getting before trusting it:

```
AGENTS.md: fresh (980 -> 410 chars, -58%)
CLAUDE.md: no sibling
MEMORY.md: stale
```

## Turning it on and off

Compress-aware is **on by default**. Two ways to control it:

- **Footer chip** — click it to toggle the mode live for the session. A click
  never persists (same live-vs-pin contract as the YOLO chip).
- **Slash command** — `/compress-aware` toggles live; `/compress-aware pin`
  persists "on" as the default; `/compress-aware unpin` persists "off".

The pinned default lives in `done.conf` under the persona's table
(`compress_aware = true|false`); absent means on. The setting is **per persona**.
When the mode is off, originals always load and the feature is inert.

## Memory: the one destructive spot

Memory files are **agent-authored**, not hand-crafted, so memory is the single
place compression is destructive: when compress-aware is on, a memory write
persists the *compressed* form directly (no verbose original is kept). If
compression fails, Done falls back to writing the full verbose text — content is
never lost on failure.

Two consequences worth knowing:

- Turning the mode **off does not un-compress** memory already written — there is
  no verbose original to restore. (Every other file reverts cleanly.)
- Compression is lossy by nature. Validation guarantees code/URLs/numbers/links
  survive, but it cannot guarantee a dropped *clause* is caught. Keep
  high-stakes memory concise and concrete so there's little to lose.

## Verifying it for real

The honest first check, once you have a model configured:

```bash
dn compress AGENTS.md          # build the sibling
cat AGENTS.compressed.md       # eyeball the compression
dn compress --status           # confirm it reads "fresh" and the delta
```

Then run a normal turn and confirm your agent still behaves and sounds like
itself. If anything reads wrong, delete the sibling (`rm AGENTS.compressed.md`)
and you're instantly back to the original — no config change needed.
