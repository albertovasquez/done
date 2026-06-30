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
# Browser-based login (Claude/Anthropic, Codex)
dn proxy login anthropic     # Opens your browser
dn proxy login claude        # Alias for anthropic — opens your browser
dn proxy login codex         # Opens your browser
```

If you don't have a browser (headless/SSH), the login command prints the OAuth URL
and polls for authentication — copy the URL to a browser on another machine and
return to complete the flow.

Check which providers are authenticated:

```bash
dn proxy status
```

This shows the CLIProxyAPI daemon status and a list of each provider's
authentication state.

## Provider login matrix

| Provider | Auth method | Command | Notes |
|----------|-------------|---------|-------|
| **Claude (Anthropic)** | Browser OAuth | `dn proxy login anthropic` or `dn proxy login claude` | Opens your browser; `claude` is an alias for `anthropic` |
| **Codex (Anthropic)** | Browser OAuth | `dn proxy login codex` | Separate from Claude/Anthropic |
| **Grok (xAI)** | API key | `export PROXY_GROK_API_KEY=...` | Pass via environment; no `dn proxy login` |
| **Kimi (Moonshot)** | API key | `export PROXY_KIMI_API_KEY=...` | Pass via environment; no `dn proxy login` |
| **Gemini (Google)** | API key | `.env` file only | See *Adding an API-key upstream* below |

## Adding NeuralWatt for GLM access

CLIProxyAPI supports GLM (via NeuralWatt) automatically when you set the
`NEURALWATT_API_KEY` environment variable.

### Setup

Set your NeuralWatt API key in your shell environment or in `~/.config/harness/.env`:

```bash
export NEURALWATT_API_KEY=your_neuralwatt_api_key_here
```

Then run `dn proxy install` (or `dn proxy upgrade` if already installed):

```bash
dn proxy install
```

This automatically appends the NeuralWatt `openai-compatibility` upstream to
`~/.config/harness/cliproxy.yaml`. The proxy restarts and GLM becomes available.

### Verify GLM is available

Check that CLIProxyAPI recognizes GLM:

```bash
dn proxy status
```

Then list available models:

```bash
curl -s http://localhost:8317/v1/models | jq .
```

You should see an entry with alias `"glm"` for the NeuralWatt upstream.

### Use GLM

Once authenticated, route a persona to GLM via the `"glm"` alias:

```bash
dn --model glm    # Use GLM as the default model
```

**To find the current GLM model ID:** visit NeuralWatt's API endpoint directly:

```bash
curl -s https://api.neuralwatt.com/v1/models \
  -H "Authorization: Bearer $NEURALWATT_API_KEY" | jq '.data[].id'
```

The CLIProxyAPI config uses `zai-org/GLM-4.6` by default; if NeuralWatt lists a different
model ID, update the `name` field in the `~/.config/harness/cliproxy.yaml`
`openai-compatibility` block under `neuralwatt.models`.

## Service management

### Start and stop the service

Control CLIProxyAPI via the OS service manager:

```bash
# Start the CLIProxyAPI daemon
dn proxy start

# Stop the daemon
dn proxy stop

# Check status (including per-provider auth)
dn proxy status
```

### Upgrade CLIProxyAPI

To update to the latest pinned binary version:

```bash
dn proxy upgrade
```

This downloads the new binary and restarts the service.

### Uninstall CLIProxyAPI

To remove CLIProxyAPI entirely:

```bash
dn proxy uninstall
```

This stops the daemon, deregisters the OS service, and removes the data directory
(including the binary, configuration, management password, and cached auth tokens).

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
dn proxy login claude      # Alias for anthropic
dn proxy login codex

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

If you used NeuralWatt with VibeProxy, set the `NEURALWATT_API_KEY` environment
variable and run `dn proxy install` or `dn proxy upgrade` — the configuration
is automatically updated (see *Adding NeuralWatt for GLM access* above).

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

If `dn proxy status` shows "not running", try starting it:

```bash
dn proxy start
```

If the start command fails, you can also restart your machine — the OS service
is registered to auto-start on boot. Check the system logs if start fails:

```bash
# macOS — view launchd logs
log show --predicate 'process == "cliproxy"' --level debug

# Linux — view systemd logs
journalctl --user -u cliproxy -n 20
```

### Provider authentication failed

Run `dn proxy login <provider>` again. If the browser doesn't open (e.g., in a
headless environment), copy the URL from the terminal, paste it into a browser
on another machine, complete the OAuth flow, and wait for the poll to succeed.

### Model not found

Verify the model is available via CLIProxyAPI:

```bash
curl -s http://localhost:8317/v1/models | jq .
```

The model list depends on which providers are authenticated. If you expect a
model and don't see it:

1. Check `dn proxy status` to confirm the provider is logged in
2. For GLM: verify `echo $NEURALWATT_API_KEY` is set, then run `dn proxy upgrade` to pick up the config
3. Restart the proxy: `dn proxy stop && dn proxy start`

### NeuralWatt / GLM not appearing

If you set `NEURALWATT_API_KEY` after installing CLIProxyAPI, you must run:

```bash
dn proxy upgrade
```

This re-downloads the binary and regenerates the config to include the NeuralWatt
upstream. If still not visible after `dn proxy status` shows "running", check
that the `NEURALWATT_API_KEY` environment variable is set and restart:

```bash
dn proxy stop && dn proxy start
```
