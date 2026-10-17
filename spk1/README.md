# SPK-1 spike code (never merged)

Agent host and Claude Code. Report: `docs/spikes/SPK-1-host-claude-vscode.md` in ex1tium/grindstone.

- `src/acp-client.mjs`: a minimal raw ACP client over stdio.
- `src/launcher.mjs`: the vendored ACP agent plugin as a launcher: runs the start-up self-test, then `claude-agent-acp` behind a stdio proxy that merges Grindstone's Claude Code options (`settingSources: []` by default) into every `session/new`, load, resume and fork.
- `src/selftest.mjs`: the credential self-test (ADR-18).
- `src/acp-smoke.mjs`, `src/exp4.mjs`, `src/exp5.mjs`: experiments. Results in `results/`.

Live runs use the operator's subscription (`GRINDSTONE_CLAUDE_OAUTH_TOKEN`, passed to the child as `CLAUDE_CODE_OAUTH_TOKEN` only).
Run: `npm ci && node src/exp5.mjs`.
