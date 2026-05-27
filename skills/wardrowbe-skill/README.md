# wardrowbe-skill

A vendor-neutral [agentskills.io](https://agentskills.io)-compatible skill
that teaches an agentic LLM (Claude Code, Codex, any MCP-aware client) how
to use the Wardrowbe MCP server bundled with the `ha-wardrowbe` Home
Assistant add-on.

The skill is *thin* — all real work happens on the MCP server side. This
bundle just contains the prompt that orients the LLM around the tool
catalogue, plus a few worked example workflows.

## Layout

```
SKILL.md          ← frontmatter (name, description) + how-to-use guide
README.md         ← this file
examples/         ← multi-turn workflow walkthroughs
  morning-outfit.md
  wash-day.md
  wardrobe-audit.md
```

No `lib/`, no `package.json`, no `pyproject.toml` — there's no code to run
locally. Everything is documentation that becomes part of the LLM's
context when the skill loads.

## Install

The bundle is portable. Symlink it into wherever your agentic client looks
for skills, e.g.:

```bash
ln -s /addons/local/ha-wardrowbe/mcp_server/skill \
      ~/.claude/skills/wardrowbe-skill
```

For project-local use, the addon's own `.claude/skills/wardrowbe-skill`
symlink resolves to this directory automatically.

## License

MIT, same as the parent addon.
