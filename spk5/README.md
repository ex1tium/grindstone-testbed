# SPK-5 spike code (never merged)

Cooperation mechanics on the SPK-1 host. Report: `docs/spikes/SPK-5-cooperation.md` in ex1tium/grindstone.

- `kernel.py`: stub kernel MCP server: `ask` held open under a ceiling with progress keepalives, single-flight consults with a cache, `load_skill`. Runs on `spk1/mcp`'s environment.
- `run.mjs`: every experiment at once. `SPK5_MINUTE` scales durations (default 60 s), `SPK5_PROGRESS` the keepalive (default 30 s), `SPK5_ONLY=exp5,exp6` runs a subset.
- `results/full/record.json`: the run at real durations (17 October 2026, 17:25–18:11 UTC).
