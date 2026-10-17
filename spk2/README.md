# SPK-2 spike code (never merged)

DBOS Transact 3.3.0 in the lifespan of a FastAPI app, killed with SIGKILL at named points and restarted. Report: `docs/spikes/SPK-2-dbos-fastapi.md` in ex1tium/grindstone.

- `app.py`: the FastAPI app, its workflows and steps, and the kill points (`SPK2_KILL_AT`).
- `fake.py`: the fake external service; counts every request per idempotency key and records each effect once.
- `harness.py`: one function per experiment; resets the system database between them; writes `results/<run>/`.

Run: start PostgreSQL 18.6 (`docker run -d --name spk2-pg -e POSTGRES_USER=spk2 -e POSTGRES_PASSWORD=spk2 -e POSTGRES_DB=spk2 -p 127.0.0.1:5433:5432 postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`), then `uv sync && uv run python harness.py` (or name experiments: `e1 e4`).
