---
name: wardrowbe-skill
description: Manage the user's Wardrowbe wardrobe — suggest outfits, log wears/washes, browse items, act on pending outfit suggestions. Auto-connects to the Wardrowbe MCP server running in the user's Home Assistant add-on. Use whenever the user asks "what should I wear", "what needs washing", "did I wear X this week", or wants to record they wore/washed something.
---

**IMPORTANT — Path Resolution:**
This skill can be installed in several locations (plugin system, manual
install, project-specific). Before running any commands below, work out
where `$SKILL_DIR` resolves to from where you loaded this `SKILL.md`.

Common install paths:

- Plugin system: `~/.claude/plugins/marketplaces/<name>/skills/wardrowbe-skill`
- Manual global: `~/.claude/skills/wardrowbe-skill`
- Project-specific: `<project>/.claude/skills/wardrowbe-skill`

# Wardrowbe wardrobe automation

This skill connects you to the user's [Wardrowbe](https://github.com/Anyesh/wardrowbe)
instance via the MCP server bundled with the `ha-wardrowbe` Home Assistant
add-on. Tools are not local; every call hits the user's wardrowbe backend.

## What this skill is good for

- *"What should I wear for [occasion]?"* → `suggest_outfit`
- *"Show me my recent outfits"* → `get_recent_outfits` (returns image URLs)
- *"Did you accept the morning suggestion?"* → `accept_latest_outfit` / `reject` / `skip`
- *"What's in my wash pile?"* → `get_items_to_wash`
- *"I just washed my [item]"* → `log_wash`
- *"Log that I wore [item] today"* → `log_wear`
- *"How many items do I have?"* → `get_wardrobe_summary`
- *"Show me my black t-shirts"* → `list_items` with `search` / `category`

## What this skill is NOT for

- Uploading new clothes (use the wardrowbe web UI; image upload is a
  separate route the MCP doesn't expose).
- Editing item metadata (no PATCH endpoint exposed yet).
- HA automation triggers — use the HA `wardrowbe` integration (`hacs-wardrowbe`)
  for that.

## Setup (one-time, on the user's machine)

1. Confirm the add-on is running and MCP is enabled in its config:
   `mcp_enabled: true`, note the `mcp_port` (default `8080`) and the API key
   either set explicitly or auto-generated at `/config/.mcp_api_key` inside
   the addon (visible via the addon's "Configuration → secrets" pane in HA).
2. Wire the MCP server into the user's MCP client:

   **Claude Code / Claude Desktop** (`~/.claude.json` or
   `~/Library/Application Support/Claude/claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "wardrowbe": {
         "type": "http",
         "url": "http://<ha-host>:8080/mcp",
         "headers": {
           "Authorization": "Bearer <mcp_api_key>"
         }
       }
     }
   }
   ```

   For clients that only speak SSE, replace `"type": "http"` with
   `"type": "sse"` and `/mcp` with `/sse`.

3. Restart Claude Code / Claude Desktop. The `wardrowbe` server should
   appear in the MCP list and its tools should be callable.

## Usage notes

- **Visual replies**: outfit and item responses include `image_url` fields.
  Don't paste raw URLs at the user — summarise the look in 1–2 sentences
  ("a navy blazer over a white tee, with chinos"). The user's client
  surfaces the images separately.
- **Occasion vocabulary**: `suggest_outfit` only accepts a fixed list of
  occasions (see the tool's docstring). Map free-form user input ("business
  meeting" → `business-casual` or `office`) before calling.
- **Pending outfits**: `accept_latest_outfit` / `reject_latest_outfit` /
  `skip_latest_outfit` act on the most recent `pending` / `sent` outfit.
  If there isn't one, the tool returns `{"error": "No actionable outfit found."}`
  — surface this to the user before silently retrying.
- **`log_wash` vs `log_wear`**: wash resets `wears_since_wash`; wear
  increments `wear_count`. Both take an `item_id` (use `list_items` or
  `get_items_to_wash` to find one).

## Worked examples

See [`examples/`](examples/) for end-to-end workflows:

- [`morning-outfit.md`](examples/morning-outfit.md) — daily suggest → accept
- [`wash-day.md`](examples/wash-day.md) — find the pile → log multiple washes
- [`wardrobe-audit.md`](examples/wardrobe-audit.md) — stats → drill-downs

## Troubleshooting

**"unauthorized"** on every call → wrong/missing `Authorization` header.
Re-check the `mcp_api_key` value.

**"Wardrowbe is not in dev mode"** when calling tools → the add-on is in
OIDC mode but the MCP server is configured for dev auth. Have the user
set `mcp_oidc_refresh_token` in the addon config (see the addon DOCS).

**Tool calls hang** → the MCP server probably can't reach the wardrowbe
backend (`http://127.0.0.1:8000` inside the addon). Check the addon log
for `wardrowbe-mcp` lines.
