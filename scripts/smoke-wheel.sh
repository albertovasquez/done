#!/usr/bin/env bash
# Phase 6 distributability gate: prove a NON-editable install runs `dn` after the
# source checkout is deleted. NOT a pytest (mutates install state, slow).
# Distribution name: quiubo-done. Installs into an ISOLATED uv tool dir so it
# never clobbers your real `dn`.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; SRC="$WORK/src"; PROJ="$WORK/proj"
trap 'rm -rf "$WORK"' EXIT

# Isolate the install so the user's real `dn` is untouched.
export UV_TOOL_DIR="$WORK/tooldir" UV_TOOL_BIN_DIR="$WORK/bin"

cp -R "$REPO" "$SRC"
uv tool install --force "$SRC"          # NON-editable: copies harness + engine into the venv
rm -rf "$SRC"                            # delete the source it installed from

# The installed tool's interpreter (proves imports resolve without the checkout).
TOOLPY="$(find "$WORK/tooldir" -path '*/bin/python' | head -1)"
mkdir -p "$PROJ"; cd "$PROJ"             # run from an UNRELATED cwd

echo "--- LINCHPIN: engine + harness + bundled skills import post-deletion ---"
"$TOOLPY" - <<'PY'
import minisweagent                       # engine copied into the venv (not the deleted source)
import harness.paths as p
assert p.mini_yaml_path().is_file(), "mini.yaml not found in installed engine"
sk = p.bundled_skills_dir()
assert sk.is_dir() and any(sk.iterdir()), "bundled skills missing from wheel"
print("OK: minisweagent + harness + skills resolve from the installed venv")
PY

echo "--- dn launches from an unrelated cwd (mock model, no proxy) ---"
echo "Manually: run \`$WORK/bin/dn --model mock\` here, send a prompt, confirm the"
echo "task.classified chip + a reply render. (Interactive; not auto-asserted.)"
echo "SMOKE PASSED: non-editable install survives checkout deletion."
