"""SPK-2 harness: runs each experiment against app.py, killing it with SIGKILL and restarting it.

Usage: uv run python harness.py [e1 e2 ...]     (default: all; results in results/<run>/)
Needs PostgreSQL at SPK2_DB (default postgresql://spk2:spk2@127.0.0.1:5433/spk2).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from dbos import DBOSClient

HERE = Path(__file__).resolve().parent
DB = os.environ.get("SPK2_DB", "postgresql://spk2:spk2@127.0.0.1:5433/spk2")
FAKE_PORT, APP_PORT = 8701, 8702
FAKE, APP = f"http://127.0.0.1:{FAKE_PORT}", f"http://127.0.0.1:{APP_PORT}"
RUN = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
OUT = HERE / "results" / RUN
summary: list[str] = []


def http(method: str, url: str, body: Any = None, timeout: float = 30) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def verdict(exp: str, ok: bool | str, evidence: str, data: Any = None) -> None:
    result = ok if isinstance(ok, str) else ("pass" if ok else "FAIL")
    line = f"- **{exp}: {result}.** {evidence}"
    summary.append(line)
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.md").write_text(f"# SPK-2 run {RUN}\n\n" + "\n".join(summary) + "\n")
    if data is not None:
        (OUT / f"{exp.split()[0]}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")


def wait_until(check: Callable[[], Any], timeout: float = 60, every: float = 0.2) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(every)
    raise TimeoutError(getattr(check, "__name__", "condition"))


class App:
    """One uvicorn worker running app.py; started, SIGKILLed and restarted by the experiments."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None
        self.starts = 0

    def start(self, *, kill_at: str = "", version: str = "v1", schedule: str = "") -> None:
        assert self.proc is None
        self.starts += 1
        env = {**os.environ, "SPK2_DB": DB, "SPK2_FAKE": FAKE, "SPK2_RUN_DIR": str(OUT),
               "SPK2_KILL_AT": kill_at, "SPK2_APP_VERSION": version, "SPK2_SCHEDULE": schedule}
        log = (OUT / "app.log").open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--port", str(APP_PORT), "--workers", "1", "--log-level", "warning"],
            cwd=HERE, env=env, stdout=log, stderr=log)
        wait_until(self._healthy, 60)

    def _healthy(self) -> bool:
        assert self.proc is not None
        if self.proc.poll() is not None:
            raise RuntimeError(f"app exited with {self.proc.returncode}; see app.log")
        try:
            return http("GET", APP + "/health", timeout=2)["pid"] == self.proc.pid
        except OSError:
            return False

    def kill(self) -> None:
        """SIGKILL: no shutdown hooks, no DBOS.destroy()."""
        assert self.proc is not None
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait()
        self.proc = None

    def died(self, timeout: float = 60) -> int:
        """Wait for the app to kill itself at its kill point."""
        assert self.proc is not None
        code = self.proc.wait(timeout)
        self.proc = None
        return code

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None


app = App()
_client: DBOSClient | None = None


def client() -> DBOSClient:
    global _client
    if _client is None:
        _client = DBOSClient(system_database_url=DB, application_name="spk2")
    return _client


def reset() -> None:
    """A clean system database and fake service for each experiment."""
    global _client
    app.stop()
    if _client is not None:
        _client.destroy()
        _client = None
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute("drop schema if exists dbos cascade")
        c.execute("drop table if exists spk2_rows")
    http("POST", FAKE + "/reset", {})


def fake_state() -> dict[str, Any]:
    return http("GET", FAKE + "/state")


def status(wid: str) -> str:
    return client().retrieve_workflow(wid).get_status().status


def result(wid: str, timeout: float = 60) -> Any:
    wait_until(lambda: status(wid) in ("SUCCESS", "ERROR"), timeout)
    return client().retrieve_workflow(wid).get_result()


def start(name: str, wid: str, *args: Any) -> None:
    http("POST", f"{APP}/start/{name}", {"id": wid, "args": list(args)})


def counts(prefix: str) -> dict[str, tuple[int, int]]:
    """For each key with this prefix: (requests the service saw, effects it recorded)."""
    s = fake_state()
    return {k: (n, int(k in s["effects"])) for k, n in s["requests"].items() if k.startswith(prefix)}


def workflows(**filters: Any) -> list[Any]:
    return client().list_workflows(**filters)


# ---------------------------------------------------------------- experiments


def e1() -> None:
    """Kill between the external call and the step's completion record."""
    app.start(kill_at="after_call:step")
    start("wf_step", "e1")
    code = app.died()
    app.start()
    res = result("e1")
    c = counts("e1:")
    steps = client().list_workflow_steps("e1")
    ok = res == "done" and c == {"e1:effect": (2, 1)}
    verdict("e1 Kill during a step", ok,
            f"app died with {code} after the call, before the step recorded; after restart: result `{res}`, "
            f"service saw (requests, effects) {c}; steps recorded: {[s['function_name'] for s in steps]}.")


def e2() -> None:
    """Kill during recv (message sent while down) and during sleep."""
    app.start()
    start("wf_recv", "e2-recv")
    wait_until(lambda: client().get_event("e2-recv", "waiting", 1))
    app.kill()
    client().send("e2-recv", "hello", "go")  # sent while the app is down
    app.start()
    res = result("e2-recv")
    c_recv = counts("e2-recv:")

    start("wf_sleep", "e2-sleep", 6.0)
    wait_until(lambda: client().get_event("e2-sleep", "sleeping", 1))
    t_kill = time.time()
    time.sleep(2)
    app.kill()
    time.sleep(2)
    app.start()
    t_up = time.time()
    slept = result("e2-sleep")
    wall, after_up = time.time() - t_kill, time.time() - t_up
    c_sleep = counts("e2-sleep:")
    ok = after_up < 6 and res == "hello" and c_recv == {"e2-recv:hello": (1, 1)} and c_sleep == {"e2-sleep:woke": (1, 1)}
    verdict("e2 Kill during recv and sleep", ok,
            f"recv: message sent while down, delivered after restart, result `{res}`, service {c_recv}. "
            f"sleep 6 s: killed 2 s in, down 2 s; finished {wall:.1f} s after the kill and {after_up:.1f} s after the restart, "
            f"sleeping {slept:.1f} s in the recovered run (the deadline is durable: a restarted sleep would take 6 s after "
            f"the restart); service {c_sleep}.")


def e3() -> None:
    """Kill in the child, and in the parent while it waits on the child."""
    app.start(kill_at="after_call:child")
    start("wf_parent", "e3a", 1.0)
    code = app.died()
    app.start()
    res_a = result("e3a")
    kids_a = [w.workflow_id for w in workflows(parent_workflow_id="e3a")]
    c_a = counts("e3a")

    start("wf_parent", "e3b", 4.0)
    child = wait_until(lambda: client().get_event("e3b", "child_id", 1))
    time.sleep(1.5)  # the child is in its sleep, the parent in get_result
    app.kill()
    app.start()
    res_b = result("e3b")
    kids_b = [w.workflow_id for w in workflows(parent_workflow_id="e3b")]
    c_b = counts("e3b")
    ok = (len(kids_a) == 1 and len(kids_b) == 1 and kids_b == [child]
          and all(v[1] == 1 for v in {**c_a, **c_b}.values())
          and sum(1 for k in c_a if k.endswith("child")) == 1 and sum(1 for k in c_b if k.endswith("child")) == 1
          and sum(1 for k in c_a if ":parent:" in k) == 1 and sum(1 for k in c_b if ":parent:" in k) == 1)
    verdict("e3 Kill during a child workflow", ok,
            f"(a) killed in the child after its call (exit {code}): parent result `{res_a}`, children {kids_a}, "
            f"service {c_a}. (b) killed while the parent waited on the child: result `{res_b}`, children {kids_b} "
            f"(the child ID is deterministic: `{child}`), service {c_b}.")


def e4() -> None:
    """Kill during a scheduled run; every 3 seconds."""
    cron = "*/3 * * * * *"
    app.start(schedule=cron, kill_at="after_call:sched")
    code = app.died(30)
    t_down = time.time()
    time.sleep(7)
    app.start(schedule=cron)
    t_up = time.time()
    time.sleep(12)
    client().pause_schedule("spk2-sched")
    time.sleep(3)  # let a run in flight finish before reading statuses
    c = counts("sched:")
    runs = workflows(name="wf_sched")
    app.stop()
    ids = [w.workflow_id for w in runs]
    dup_effects = {k: v for k, v in c.items() if v[1] != 1}
    retried = {k: v for k, v in c.items() if v[0] > 1}
    ok = len(ids) == len(set(ids)) and not dup_effects and all(w.status == "SUCCESS" for w in runs)
    verdict("e4 Kill during a scheduled run", ok,
            f"killed in a run (exit {code}), down {t_up - t_down:.0f} s, up 12 s, then paused: {len(ids)} runs, one workflow ID "
            f"per period (`sched-<name>-<time>`), all SUCCESS: {all(w.status == 'SUCCESS' for w in runs)}; "
            f"keys called more than once (the killed run, re-run once): {retried}; periods while down were skipped "
            f"(no automatic backfill).", {"counts": c, "ids": ids})


def e5() -> None:
    """The same message twice with one idempotency key."""
    app.start()
    start("wf_recv_twice", "e5")
    wait_until(lambda: client().get_event("e5", "waiting", 1))
    client().send("e5", "once", "m", idempotency_key="k1")
    client().send("e5", "once", "m", idempotency_key="k1")
    res = result("e5", 30)
    verdict("e5 Idempotent messages", res == ["once", None],
            f"two sends with idempotency key `k1`; the workflow's two recv calls got {res}.")


def lanes(marks: list[dict[str, Any]], lane: str) -> tuple[int, list[str]]:
    """Maximum number of runs open at once on a lane, and the order they started."""
    events = sorted((m["t"], 1 if m["event"] == "start" else -1, m["id"]) for m in marks if m["lane"] == lane)
    cur = peak = 0
    for _, d, _ in events:
        cur += d
        peak = max(peak, cur)
    return peak, [i for _, d, i in events if d == 1]


def e6() -> None:
    """Writing lane: global concurrency 1, 50 enqueued from parallel requests, two projects."""
    app.start()
    ids = [f"e6-{'ab'[i % 2]}-{i}" for i in range(50)]
    threads = [threading.Thread(target=http, args=("POST", f"{APP}/enqueue/writing/wf_lane",
                                                   {"id": w, "args": ["writing", w, 0.05]})) for w in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for w in ids:
        result(w, 120)
    peak, _ = lanes(fake_state()["marks"], "writing")
    verdict("e6 Serialized writing lane", peak == 1,
            f"50 workflows from two projects enqueued from 50 parallel requests; all finished; at most {peak} running at once.")


def e7() -> None:
    """Parent on the writing lane and children on the read-only lane; then the delegate shape; then the deadlock shape."""
    app.start()
    for i in range(3):
        http("POST", f"{APP}/enqueue/writing/wf_writer_waits", {"id": f"e7-w{i}", "args": [f"e7-w{i}"]})
    for i in range(5):
        http("POST", f"{APP}/enqueue/readonly/wf_lane", {"id": f"e7-r{i}", "args": ["readonly", f"e7-r{i}", 0.5]})
    for w in [f"e7-w{i}" for i in range(3)] + [f"e7-r{i}" for i in range(5)]:
        result(w, 60)
    marks = fake_state()["marks"]
    peak_w, _ = lanes(marks, "writing")
    peak_r, _ = lanes(marks, "readonly")

    http("POST", FAKE + "/reset", {})
    for i in range(3):
        http("POST", f"{APP}/enqueue/writing/wf_writer_delegates", {"id": f"e7-d{i}", "args": [f"e7-d{i}"]})
    children = [result(f"e7-d{i}", 60) for i in range(3)]
    for c in children:
        result(c, 60)
    peak_d, order = lanes(fake_state()["marks"], "writing")

    http("POST", f"{APP}/enqueue/writing/wf_writer_waits_on_writer", {"id": "e7-x", "args": ["e7-x"]})
    time.sleep(8)
    x_status = status("e7-x")
    x_child = [(w.workflow_id, w.status) for w in workflows(parent_workflow_id="e7-x")]
    client().cancel_workflow("e7-x")
    for cid, _ in x_child:
        client().cancel_workflow(cid)
    ok = peak_w == 1 and peak_r <= 2 and peak_d == 1 and x_status == "PENDING"
    verdict("e7 Writing parent, read-only child; delegation", ok,
            f"3 writing parents each waiting on a read-only child, plus 5 read-only runs: all finished, no deadlock; "
            f"peak writing {peak_w}, peak read-only {peak_r} (limit 2). Delegation: 3 writing parents enqueue their "
            f"child on the writing lane and end; children ran after their parents, peak writing {peak_d}, start order "
            f"{order}. Control: a writing parent waiting on a writing child is still `{x_status}` after 8 s with the "
            f"child {x_child}: a deadlock, as expected; cancelled.")


def e8() -> None:
    """Application-version bump with a parked workflow."""
    app.start(version="v1")
    for w in ("e8-a", "e8-b", "e8-c"):
        start("wf_park", w)
        wait_until(lambda w=w: client().get_event(w, "parked", 1))
    app.kill()
    app.start(version="v2")
    time.sleep(4)
    before = {w: status(w) for w in ("e8-a", "e8-b", "e8-c")}
    client().send("e8-a", "approve", "decision")
    time.sleep(4)
    a_after_send = status("e8-a")
    versions = [(v["version_name"] if isinstance(v, dict) else v.version_name) for v in client().list_application_versions()]

    # (1) fork onto v2 from the recv step: a new workflow ID, mailbox not copied.
    steps = client().list_workflow_steps("e8-a")
    # A pending recv is not a recorded step yet; only its timeout is (DBOS.sleep, the id after it).
    # wf_park's recv is the step after its setEvent.
    recv_step = next(s["function_id"] for s in steps if s["function_name"] == "DBOS.setEvent") + 1
    fork = client().fork_workflow("e8-a", recv_step, application_version="v2")
    time.sleep(3)
    fork_status = fork.get_status().status
    client().send(fork.workflow_id, "approve", "decision")
    fork_result = result(fork.workflow_id, 30)
    client().cancel_workflow("e8-a")

    # (2) cancel, then rewind onto v2 under the same ID; message sent before the rewind.
    client().send("e8-b", "approve", "decision")
    client().cancel_workflow("e8-b")
    steps_b = client().list_workflow_steps("e8-b")
    recv_b = next(s["function_id"] for s in steps_b if s["function_name"] == "DBOS.setEvent") + 1
    client().rewind_workflow("e8-b", start_step=recv_b, application_version="v2")
    try:
        rewind_result = result("e8-b", 20)
    except TimeoutError:
        rewind_result = f"still {status('e8-b')} after 20 s"
        client().send("e8-b", "approve-again", "decision")
        try:
            rewind_result += f"; after a second message: `{result('e8-b', 20)}`"
        except TimeoutError:
            rewind_result += f"; after a second message still {status('e8-b')}"

    # (3) ADR-03's design: the wait lives outside the workflow; the decision starts a continuation on v2.
    client().cancel_workflow("e8-c")
    start("wf_step", "e8-c-continuation")
    cont = result("e8-c-continuation")
    c = {**counts("e8-"), **counts(fork.workflow_id)}
    once = all(v[1] == 1 and v[0] == 1 for v in c.values())
    data = {"steps_while_parked": steps, "before": before, "versions": versions}
    ok = (all(s == "PENDING" for s in before.values()) and isinstance(fork_result, str) and "v2" in fork_result
          and once and cont == "done")
    verdict("e8 Version bump with a parked workflow", ok,
            f"3 workflows parked in recv on v1; app killed and restarted as v2: after 4 s their status {before} "
            f"(not recovered: v2 recovers only v2's workflows); a message to e8-a then left it `{a_after_send}`. "
            f"Versions known: {versions}. (1) `fork_workflow(id, recv step {recv_step}, application_version='v2')`: new "
            f"ID `{fork.workflow_id}`, `{fork_status}` until a message is sent to the new ID (the mailbox is not "
            f"copied), then `{fork_result}`. (2) cancel + `rewind_workflow(same ID, recv step, application_version='v2')` "
            f"with the message sent before: `{rewind_result}`. (3) a continuation started on v2: `{cont}`. "
            f"Service (requests, effects): {c}.", data)


def e9() -> None:
    """Throughput: 500 short workflows of two PostgreSQL-writing steps."""
    app.start()
    r = http("POST", f"{APP}/bench/500", timeout=600)
    with psycopg.connect(DB) as conn:
        size = conn.execute(
            "select coalesce(sum(pg_total_relation_size(format('%I.%I', schemaname, tablename))), 0) "
            "from pg_tables where schemaname = 'dbos'").fetchone()
        rows = conn.execute(
            "select relname, n_live_tup from pg_stat_user_tables where schemaname = 'dbos' order by n_live_tup desc limit 6").fetchall()
    per_step_ms = (r["dbos_s"] - r["plain_s"]) / (500 * 2) * 1000
    verdict("e9 Throughput", "recorded",
            f"500 workflows in turn, 2 steps each: {r['dbos_s']:.1f} s with DBOS against {r['plain_s']:.1f} s for the same "
            f"writes without it: {per_step_ms:.2f} ms DBOS overhead per step ({r['dbos_s'] / 500 * 1000:.1f} ms per "
            f"workflow). System schema after it: {size[0] / 1024 / 1024:.1f} MiB; largest tables {rows}.",
            {"bench": r, "schema_bytes": size[0], "tables": rows})


EXPERIMENTS: dict[str, Callable[[], None]] = {f.__name__: f for f in (e1, e2, e3, e4, e5, e6, e7, e8, e9)}


def main() -> None:
    names = sys.argv[1:] or list(EXPERIMENTS)
    OUT.mkdir(parents=True, exist_ok=True)
    fake = subprocess.Popen([sys.executable, str(HERE / "fake.py"), str(FAKE_PORT)])
    try:
        wait_until(lambda: _up(FAKE + "/state"), 10)
        for name in names:
            print(f"== {name}", flush=True)
            reset()
            try:
                EXPERIMENTS[name]()
            except Exception as e:  # record and carry on with the next experiment
                verdict(f"{name}", f"ERROR {type(e).__name__}", str(e))
            finally:
                app.stop()
    finally:
        app.stop()
        fake.terminate()
    print(f"results: {OUT}")


def _up(url: str) -> bool:
    try:
        http("GET", url, timeout=1)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
