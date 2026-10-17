# grindstone-testbed

Test repository for [Grindstone](https://github.com/ex1tium/grindstone). Grindstone pushes branches here, opens pull requests and sets the `grindstone/verification` status, so its delivery mechanics are proven against a real GitHub repository and never against real work. It is public because GitHub's free plans enforce rulesets only on public repositories (Grindstone ADR-40).

Nothing here is a product. Expect automated branches and pull requests.

| Path | Holds |
| --- | --- |
| `src/testbed/` | A tiny standard-library Python package: the code the scripted and live agents change |
| `tests/` | `unittest` tests; each names the acceptance criterion it verifies with `@verifies AC-…` |
| `intent/` | The fixture project's hand-written intent: one product requirement already proven, and ticket TKT-0001, which Grindstone's first live run implements (scenario S1) |
| `fixtures/hostile/` | Inert copies of repository files that must never take effect inside Grindstone: a git hook, agent settings with a hook, an MCP server configuration, agent instructions. Tests copy them into workspaces to prove they are ignored (M1-05). They are stored under names no tool loads |
| `.github/workflows/forge-status.yml` | Manual-only workflow that posts a `grindstone/verification` status with the repository's own token, to prove the ruleset ignores it (SPK-4) |

Run the tests: `python -m unittest discover -s tests -t .` (Python 3.11 or later, no dependencies).

Licensed under the Apache License 2.0.
