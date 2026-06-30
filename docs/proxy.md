# CLIProxyAPI — proxy setup and provider logins

Done routes all LLM calls through **CLIProxyAPI**, a local OpenAI-compatible proxy
that handles authentication and provider routing. This document covers setup,
adding upstreams (like NeuralWatt for GLM), and migrating from VibeProxy.

## Quick start

### Install and configure CLIProxyAPI

```bash
# Download and register CLIProxyAPI as an OS service (macOS launchd or Linux systemd)
dn proxy install

# Check status
dn proxy status
```

The `install` command:
1. Generates the default config file at `~/.config/harness/cliproxy.yaml`
2. Downloads the CLIProxyAPI binary (if not already present)
3. Registers it as an OS service so it runs automatically on boot

### Log in to a provider

Once CLIProxyAPI is installed, authenticate with your chosen LLM provider:

```bash
# Browser-based login (Claude/Anthropic, Codex, Antigravity)
dn proxy login anthropic     # Opens your browser
dn proxy login codex         # Opens your browser
dn proxy login antigravity   # Opens your browser

# CLI-flag login (Grok/xAI, Kimi)
# These do not have a browser flow; instead, pass the API key as an environment
# variable when launching Done. See the provider-specific setup below.

# Gemini (Google)
# Gemini uses API-key authentication only. No browser login exists; see below.
```

Check which providers are authenticated:

```bash
dn proxy status
```

This shows the CLIProxyAPI daemon status and a list of each provider's
authentication state.

## Provider login matrix

| Provider | Auth method | Command | Notes |
|----------|-------------|---------|-------|
| **Claude (Anthropic)** | Browser OAuth | `dn proxy login anthropic` | Automatic; opens your browser |
| **Codex (Anthropic)** | Browser OAuth | `dn proxy login codex` | Separate from Claude/Anthropic |
| **Antigravity** | Browser OAuth | `dn proxy login antigravity` | Web-based auth flow |
| **Grok (xAI)** | API key | `export PROXY_GROK_API_KEY=...` | Pass via environment; no `dn proxy login` |
| **Kimi (Moonshot)** | API key | `export PROXY_KIMI_API_KEY=...` | Pass via environment; no `dn proxy login` |
| **Gemini (Google)** | API key | `.env` file only | See *Adding an API-key upstream* below |

## Adding an API-key upstream (example: NeuralWatt → GLM)

To add a new LLM provider that uses API keys (not browser OAuth), edit
`~/.config/harness/cliproxy.yaml` and append an `openai-compatibility` upstream
block. This example adds NeuralWatt as a CLIProxyAPI upstream for GLM access:

```yaml
# At the end of ~/.config/harness/cliproxy.yaml, add:

openai-compatibility:
  - name: "neuralwatt"
    base-url: "https://api.neuralwatt.com/v1"
    api-key-entries:
      - "${NEURALWATT_API_KEY}"
    models:
      - model-id: "<GLM-MODEL-ID>"    # Replace with actual GLM model ID, e.g. "glm-4-turbo"
        aliases:
          - "glm"
```

Then set your NeuralWatt API key in `~/.config/harness/.env`:

```bash
NEURALWATT_API_KEY=your_neuralwatt_api_key_here
```

Once configured, you can reference the GLM model via its alias:

```bash
dn --model gpt-4-turbo    # Route to NeuralWatt's GLM-4-Turbo via the "glm" alias
```

**To find the correct GLM model ID:** visit NeuralWatt's API endpoint directly:

```bash
curl -s https://api.neuralwatt.com/v1/models \
  -H "Authorization: Bearer $NEURALWATT_API_KEY" | jq '.data[].id'
```

This lists all available models; use the exact model ID in the `openai-compatibility` block above.

## Migrating from VibeProxy

If you were using **VibeProxy** previously, here's how to migrate to CLIProxyAPI:

### Step 1: Install CLIProxyAPI

```bash
dn proxy install
```

This sets up the config file, binary, and OS service registration.

### Step 2: Log in to your providers

Authenticate with each provider you use:

```bash
# Browser-based providers
dn proxy login anthropic
dn proxy login codex
dn proxy login antigravity

# Or, for API-key providers, set environment variables
export PROXY_GROK_API_KEY=your_grok_key
export PROXY_KIMI_API_KEY=your_kimi_key
```

### Step 3: Update your environment variables

Rename `VIBEPROXY_*` to `PROXY_*` in `~/.config/harness/.env` (or in your project's `.env`):

**Old (VibeProxy):**
```bash
VIBEPROXY_BASE_URL=http://localhost:8317/v1
VIBEPROXY_MODEL=gpt-4-turbo
VIBEPROXY_API_KEY=dummy-not-used
```

**New (CLIProxyAPI):**
```bash
PROXY_BASE_URL=http://localhost:8317/v1
PROXY_MODEL=gpt-4-turbo
PROXY_API_KEY=dummy-not-used
```

**Note:** Both `VIBEPROXY_*` and `PROXY_*` are honored for backward compatibility.
If both are set, `PROXY_*` takes precedence. You may keep the old names for now,
but updating to `PROXY_*` is recommended for clarity.

### Step 4: Add custom upstreams (optional)

If you used NeuralWatt or other API-key upstreams with VibeProxy, add them to
the `openai-compatibility` section of `~/.config/harness/cliproxy.yaml` (see
*Adding an API-key upstream* above).

### Step 5: Verify and launch

Check that CLIProxyAPI is running:

```bash
dn proxy status
```

Then launch Done normally:

```bash
dn
```

Done will use the same model you configured (now via `PROXY_MODEL` or from
`~/.config/harness/done.conf`), but routed through CLIProxyAPI instead of
VibeProxy.

## Troubleshooting

### CLIProxyAPI not running

If `dn proxy status` shows "not running", try starting it manually:

```bash
# macOS
launchctl start me.router.cliproxy

# Linux
systemctl --user start cliproxy
```

Or restart your machine — the OS service should auto-start on boot.

### Provider authentication failed

Run `dn proxy login <provider>` again. If the browser doesn't open, copy the
URL from the terminal manually and paste it into your browser.

### Model not found

Verify the model is available via CLIProxyAPI:

```bash
curl -s http://localhost:8317/v1/models | jq .
```

The model list depends on which providers are authenticated. If you expect a
model and don't see it, check `dn proxy status` to confirm the provider is
logged in.

### API key not working

If using an API-key upstream (e.g., NeuralWatt), verify:

1. The environment variable is set: `echo $NEURALWATT_API_KEY`
2. The `openai-compatibility` block in `cliproxy.yaml` is correctly formatted
3. Restart CLIProxyAPI after editing the config:

```bash
# macOS
launchctl stop me.router.cliproxy
launchctl start me.router.cliproxy

# Linux
systemctl --user restart cliproxy
```
