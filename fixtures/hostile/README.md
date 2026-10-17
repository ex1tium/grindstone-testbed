# Hostile fixtures — inert by design

Each file is a repository file that must never take effect inside Grindstone (Grindstone QS-06, M1-05, ADR-18). They are stored here under names that no tool loads; tests copy them to their real locations inside a scratch workspace and assert that nothing happens. Never move them to their real paths in this repository: an agent session opening this repository would load them.

| File | Real location | Would do | Must |
| --- | --- | --- | --- |
| `githooks/pre-commit` | `.git/hooks/pre-commit` | Write `/tmp/grindstone-hostile-hook-ran` | Never run when Grindstone commits (hooks disabled) |
| `claude-settings.json` | `.claude/settings.json` | Register a session hook writing a marker file | Never reach an agent session |
| `mcp.json` | `.mcp.json` | Offer an extra MCP server | Never reach an agent session |
| `agent-instructions.md` | `CLAUDE.md`, `AGENTS.md` | Tell the agent to delete tests | Treated as untrusted project content, never as instructions that change permissions |

The markers only prove execution; they do nothing else.
