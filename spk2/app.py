"""SPK-2 app: DBOS Transact in the lifespan of a FastAPI app, one uvicorn worker.

Environment: SPK2_DB (PostgreSQL URL), SPK2_FAKE (fake service URL), SPK2_RUN_DIR,
SPK2_APP_VERSION (default v1), SPK2_KILL_AT (a kill point name, see kill_point),
SPK2_SCHEDULE (cron, six fields; registers the wf_sched schedule when set).
"""

from __future__ import annotations

import json
import os
import signal
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from dbos import DBOS, DBOSConfig, SetWorkflowID
from fastapi import FastAPI

DB = os.environ["SPK2_DB"]
FAKE = os.environ["SPK2_FAKE"]
RUN_DIR = Path(os.environ["SPK2_RUN_DIR"])


def kill_point(name: str) -> None:
    """SIGKILL this process the first time execution reaches `name`, if SPK2_KILL_AT names it."""
    if os.environ.get("SPK2_KILL_AT") == name:
        marker = RUN_DIR / f"killed-{name.replace(':', '_')}"
        if not marker.exists():
            marker.touch()
            os.kill(os.getpid(), signal.SIGKILL)


def fake(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(FAKE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- steps


@DBOS.step(name="effect")
def effect(key: str, kind: str) -> bool:
    """The pattern every external step uses: the call carries an idempotency key, the service finds or creates."""
    created = fake("/effect", {"key": key, "kind": kind})["created"]
    kill_point(f"after_call:{kind}")
    return created


@DBOS.step(name="mark")
def mark(lane: str, ident: str, event: str) -> None:
    fake("/mark", {"lane": lane, "id": ident, "event": event})


@DBOS.step(name="hold")
def hold(seconds: float) -> None:
    time.sleep(seconds)


@DBOS.step(name="write_row")
def write_row(wid: str, n: int) -> None:
    with psycopg.connect(DB) as c:
        c.execute("insert into spk2_rows (wid, n) values (%s, %s)", (wid, n))


# ---------------------------------------------------------------- workflows


@DBOS.workflow(name="wf_step")
def wf_step() -> str:
    effect(f"{DBOS.workflow_id}:effect", "step")
    return "done"


@DBOS.workflow(name="wf_recv")
def wf_recv() -> str:
    DBOS.set_event("waiting", True)
    msg = DBOS.recv("go", timeout_seconds=600)
    effect(f"{DBOS.workflow_id}:{msg}", "recv")
    return str(msg)


@DBOS.workflow(name="wf_sleep")
def wf_sleep(seconds: float) -> float:
    DBOS.set_event("sleeping", True)
    t0 = time.time()
    DBOS.sleep(seconds)
    effect(f"{DBOS.workflow_id}:woke", "sleep")
    return time.time() - t0


@DBOS.workflow(name="wf_child")
def wf_child(seconds: float) -> str:
    effect(f"{DBOS.workflow_id}:child", "child")
    DBOS.sleep(seconds)
    return f"child-of-{seconds}"


@DBOS.workflow(name="wf_parent")
def wf_parent(seconds: float) -> str:
    handle = DBOS.start_workflow(wf_child, seconds)
    DBOS.set_event("child_id", handle.workflow_id)
    result = handle.get_result()
    effect(f"{DBOS.workflow_id}:parent:{result}", "parent")
    return result


@DBOS.workflow(name="wf_sched")
def wf_sched(at: datetime, context: Any) -> None:
    effect(f"sched:{at.isoformat()}", "sched")
    hold(1.0)


@DBOS.workflow(name="wf_recv_twice")
def wf_recv_twice() -> list[Any]:
    DBOS.set_event("waiting", True)
    first = DBOS.recv("m", timeout_seconds=60)
    second = DBOS.recv("m", timeout_seconds=5)
    return [first, second]


@DBOS.workflow(name="wf_lane")
def wf_lane(lane: str, ident: str, seconds: float) -> str:
    mark(lane, ident, "start")
    hold(seconds)
    mark(lane, ident, "end")
    return ident


@DBOS.workflow(name="wf_writer_waits")
def wf_writer_waits(ident: str) -> str:
    """Parent on the writing lane waits for a child on the read-only lane."""
    mark("writing", ident, "start")
    child = DBOS.retrieve_queue("readonly").enqueue(wf_lane, "readonly", f"{ident}-child", 0.5)
    result = child.get_result()
    mark("writing", ident, "end")
    return result


@DBOS.workflow(name="wf_writer_delegates")
def wf_writer_delegates(ident: str) -> str:
    """Parent ends its turn by delegating: the child runs on the writing lane the parent frees."""
    mark("writing", ident, "start")
    child = DBOS.retrieve_queue("writing").enqueue(wf_lane, "writing", f"{ident}-child", 0.3)
    mark("writing", ident, "end")
    return child.workflow_id


@DBOS.workflow(name="wf_writer_waits_on_writer")
def wf_writer_waits_on_writer(ident: str) -> str:
    """The shape the design forbids: a parent holding the writing lane waits on a writing-lane child."""
    child = DBOS.retrieve_queue("writing").enqueue(wf_lane, "writing", f"{ident}-child", 0.1)
    return child.get_result()


@DBOS.workflow(name="wf_park")
def wf_park() -> str:
    DBOS.set_event("parked", True)
    decision = DBOS.recv("decision", timeout_seconds=3600)
    effect(f"{DBOS.workflow_id}:{decision}", "park")
    return f"{decision} on {os.environ.get('SPK2_APP_VERSION', 'v1')}"


@DBOS.workflow(name="wf_short")
def wf_short(n: int) -> int:
    write_row(DBOS.workflow_id or "", n)
    write_row(DBOS.workflow_id or "", n + 1)
    return n


def _write(wid: str, n: int) -> None:
    with psycopg.connect(DB) as c:
        c.execute("insert into spk2_rows (wid, n) values (%s, %s)", (wid, n))


WORKFLOWS = {f.__name__: f for f in (wf_step, wf_recv, wf_sleep, wf_parent, wf_recv_twice, wf_park)}


# ---------------------------------------------------------------- FastAPI


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    with psycopg.connect(DB) as c:
        c.execute("create table if not exists spk2_rows (wid text, n int)")
    config: DBOSConfig = {
        "name": "spk2",
        "system_database_url": DB,
        "application_version": os.environ.get("SPK2_APP_VERSION", "v1"),
        "scheduler_polling_interval_sec": 1.0,
        "notification_listener_polling_interval_sec": 0.1,
    }
    DBOS(config=config)
    DBOS.launch()
    await DBOS.register_queue_async("writing", global_concurrency=1, polling_interval_sec=0.1)
    await DBOS.register_queue_async("readonly", global_concurrency=2, polling_interval_sec=0.1)
    if cron := os.environ.get("SPK2_SCHEDULE"):
        await DBOS.apply_schedules_async([{"schedule_name": "spk2-sched", "workflow_fn": wf_sched, "schedule": cron}])
    yield
    DBOS.destroy()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"pid": os.getpid(), "version": os.environ.get("SPK2_APP_VERSION", "v1")}


@app.post("/start/{name}")
def start(name: str, body: dict[str, Any]) -> dict[str, str]:
    with SetWorkflowID(body["id"]):
        handle = DBOS.start_workflow(WORKFLOWS[name], *body.get("args", []))
    return {"id": handle.workflow_id}


@app.post("/enqueue/{queue}/{name}")
def enqueue(queue: str, name: str, body: dict[str, Any]) -> dict[str, str]:
    fn = {"wf_lane": wf_lane, "wf_writer_waits": wf_writer_waits, "wf_writer_delegates": wf_writer_delegates,
          "wf_writer_waits_on_writer": wf_writer_waits_on_writer}[name]
    q = DBOS.retrieve_queue(queue)
    assert q is not None
    with SetWorkflowID(body["id"]):
        handle = q.enqueue(fn, *body.get("args", []))
    return {"id": handle.workflow_id}


@app.post("/bench/{n}")
def bench(n: int) -> dict[str, float]:
    """Experiment 9: n short workflows of two PostgreSQL-writing steps, run in turn, against a plain baseline."""
    t0 = time.perf_counter()
    for i in range(n):
        _write(f"plain-{i}", i)
        _write(f"plain-{i}", i + 1)
    plain = time.perf_counter() - t0
    t0 = time.perf_counter()
    for i in range(n):
        with SetWorkflowID(f"bench-{i}"):
            wf_short(i)
    dbos = time.perf_counter() - t0
    return {"n": n, "plain_s": plain, "dbos_s": dbos}
