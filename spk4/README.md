# SPK-4 spike code (never merged)

`gh.py` mints a GitHub App JWT and an installation token from the `GRINDSTONE_GITHUB_APP_*` variables and calls the REST API; `exp_reads.py` runs the read-only experiments. Needs Python 3 and PyJWT with `cryptography`. Run from this directory: `python3 exp_reads.py`.

In a Claude Code cloud session the GitHub proxy substitutes its own credentials for any `Authorization` header and refuses `/app/*`, commit-status writes and Actions paths, so the App experiments must run where `api.github.com` is reached directly. The report is in `docs/spikes/SPK-4-github-mechanics.md` in ex1tium/grindstone.
