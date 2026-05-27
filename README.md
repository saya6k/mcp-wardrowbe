# wardrowbe-mcp

MCP (Model Context Protocol) server exposing the [Wardrowbe](https://github.com/Anyesh/wardrowbe)
wardrobe API as tools an LLM can call. Works against any Wardrowbe instance —
self-hosted (e.g., behind a reverse proxy or the `ha-wardrowbe` Home Assistant
add-on) or a hosted/cloud deployment.

22 tools covering outfit suggestions, item browsing, wear/wash logging,
acceptance flow, analytics, and notifications. Tool surface mirrors
`hacs-wardrowbe`'s LLM API one-for-one, plus three read-only helpers
(`list_items`, `get_item`, `get_outfit`).

## Install

```bash
pip install wardrowbe-mcp
```

Or from source:

```bash
pip install git+https://github.com/saya6k/mcp-wardrowbe
```

## Run

```bash
wardrowbe-mcp \
  --wardrowbe-url https://wardrowbe.example.com \
  --api-key "$(openssl rand -hex 32)" \
  --auth oidc \
  --oidc-issuer-url https://id.example.com \
  --oidc-client-id wardrowbe \
  --oidc-refresh-token "<refresh-token>"
```

The process listens on `0.0.0.0:8080` by default and serves both transports
at the root:

- `http://<host>:8080/mcp` — Streamable HTTP (recommended)
- `http://<host>:8080/sse` — Server-Sent Events (legacy MCP clients)
- `http://<host>:8080/` — anonymous health/info probe

All non-probe routes require `Authorization: Bearer <api-key>`.

### Stdio transport (for proxies)

Bridges like [`sparfenyuk/mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy)
and the [`HASS-MCPProxy`](https://github.com/rwfsmith/HASS-MCPProxy) add-on
spawn MCP servers as child processes and read JSON-RPC frames from their
stdout. Pass `--transport stdio` (or set `MCP_TRANSPORT=stdio`) to run in
that mode:

```bash
wardrowbe-mcp --transport stdio \
  --wardrowbe-url https://wardrowbe.example.com \
  --auth oidc --oidc-issuer-url https://id.example.com \
  --oidc-client-id wardrowbe --oidc-refresh-token "<refresh-token>"
```

stdio mode skips Starlette/uvicorn and the Bearer middleware (`--host`,
`--port`, `--api-key` are ignored) — the parent proxy owns the trust
boundary. Backend auth (`--auth`) still applies because it controls how
the server reaches the wardrowbe backend.

#### HASS-MCPProxy `servers.yaml` example

```yaml
- name: wardrowbe
  enabled: true
  type: github-python
  repo: "https://github.com/saya6k/mcp-wardrowbe"
  branch: "main"
  install: "uv pip install -e ."
  run: "python -m wardrowbe_mcp --transport stdio"
  args: []
  env:
    WARDROWBE_URL: "https://wardrowbe.example.com"
    MCP_AUTH_MODE: "oidc"
    MCP_OIDC_ISSUER_URL: "https://id.example.com"
    MCP_OIDC_CLIENT_ID: "wardrowbe"
    MCP_OIDC_REFRESH_TOKEN: "${WARDROWBE_REFRESH_TOKEN}"
```

After **Apply & Restart**, the proxy exposes the server at
`http://homeassistant.local:8080/servers/wardrowbe/sse` — add that URL to
Home Assistant via **Settings → Devices & Services → MCP Server**.

### Configuration reference

Every flag has a matching environment variable so you can drive it from a
shell environment, a `.env`, or a container orchestrator without rewriting
the command line:

| Flag | Env var | Notes |
| --- | --- | --- |
| `--transport` | `MCP_TRANSPORT` | `http` (default) or `stdio`. |
| `--host` | `MCP_BIND_HOST` | Bind address. Default `0.0.0.0`. http only. |
| `--port` | `MCP_BIND_PORT` | Bind port. Default `8080`. http only. |
| `--wardrowbe-url` | `WARDROWBE_URL` | Base URL of the Wardrowbe backend. |
| `--api-key` | `MCP_API_KEY` | Required Bearer token for incoming MCP calls. http only. |
| `--auth` | `MCP_AUTH_MODE` | `dev` (default) or `oidc`. |
| `--external-id` | `MCP_EXTERNAL_ID` | Dev-mode identity sent to `/auth/sync`. |
| `--oidc-issuer-url` | `MCP_OIDC_ISSUER_URL` | OIDC discovery base. |
| `--oidc-client-id` | `MCP_OIDC_CLIENT_ID` | |
| `--oidc-client-secret` | `MCP_OIDC_CLIENT_SECRET` | Optional for public clients. |
| `--oidc-refresh-token` | `MCP_OIDC_REFRESH_TOKEN` | Obtained from a one-time external PKCE flow. |
| `--log-level` | `MCP_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |

## Auth to the Wardrowbe backend

Two modes, selected by `--auth`:

- **`dev`** — sends `{external_id, email, display_name}` to
  `POST /auth/sync`. Works only when the Wardrowbe backend itself is in dev
  mode (its `DEBUG=true` and `SECRET_KEY` left at the upstream default).
- **`oidc`** — refreshes a stored `refresh_token` against the configured
  issuer to mint a fresh `id_token`, then `/auth/sync`. The refresh token
  must come from a one-time interactive PKCE flow you run yourself —
  there is no in-process browser callback because the MCP server is
  headless. Once obtained, the server rotates the token automatically if
  the IDP returns a new one.

[`get_refresh_token.py`](get_refresh_token.py) — a minimal one-shot PKCE
helper — is the easiest way to bootstrap OIDC. Edit the four constants at
the top (`ISSUER`, `CLIENT_ID`, `CLIENT_SECRET`, `REDIRECT_URI`), register
that redirect URI on your IDP client, then run:

```bash
python3 get_refresh_token.py
```

A browser opens against the IDP; after you authenticate the script prints
the `refresh_token` to stdout. Paste it into `--oidc-refresh-token` (or
the `MCP_OIDC_REFRESH_TOKEN` env var) when starting `wardrowbe-mcp`. The
script needs the `offline_access` scope to be allowed on your IDP client;
otherwise the token endpoint omits `refresh_token` from the response.
Common IDPs verified: Pocket ID. Should work with any OIDC-conformant
provider (Keycloak, Authentik, Auth0, etc.).

## Client config

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "wardrowbe": {
      "type": "http",
      "url": "http://<host>:8080/mcp",
      "headers": { "Authorization": "Bearer <api-key>" }
    }
  }
}
```

For SSE-only clients, swap `"type": "http"` → `"type": "sse"` and `/mcp` →
`/sse`.

### Other MCP-aware clients

Any client that supports HTTP transport with a Bearer header works. The
server advertises tools, instructions, and a bundled
[agentskills.io](https://agentskills.io)-compatible skill at
`skill://wardrowbe-skill/SKILL.md` (plus sibling resources).

## Skill bundle

`wardrowbe_mcp/skill/` ships inside the package as the agent-facing
documentation: a `SKILL.md` manifest with usage guidance and three worked
examples under `examples/`. The MCP server registers each file as an MCP
resource at startup, so MCP clients that auto-install skills pick it up
automatically. Manual install is also possible by copying the directory
into the client's skill discovery path.

The bundle's prose currently mentions the `ha-wardrowbe` add-on as the
canonical deployment. If you're running this against a different Wardrowbe
deployment, edit `SKILL.md` to match.

## Development

```bash
git clone https://github.com/saya6k/mcp-wardrowbe
cd mcp-wardrowbe
pip install -e .
wardrowbe-mcp --help
```

Smoke test against a running instance:

```bash
curl -fsS http://127.0.0.1:8080/ | jq                       # anonymous health
curl -fsS http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

## License

MIT. See [LICENSE](LICENSE).
