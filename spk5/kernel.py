"""SPK-5 stub kernel MCP server: `ask` held open under a ceiling with progress keepalives, single-flight consults
with a cache (ADR-38), and `load_skill` that returns instructions inline and adds a tool for this session (ADR-39).

Per token (POST /admin/tokens): session, capability, delay_s (stub consult), progress_s (0 = none), ceiling_s,
consult_url (optional: a real consult answered by POST {question} -> {answer, ...}).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
import urllib.request
from typing import Any

from fastapi import FastAPI
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

TOKENS: dict[str, dict[str, Any]] = {}
CONSULTS: dict[str, dict[str, Any]] = {}
LOG: list[dict[str, Any]] = []
OPEN: dict[str, int] = {}  # session -> open kernel tool calls (for the liveness check)
T0 = time.time()


def log(kind: str, **kw: Any) -> None:
    LOG.append({"t": round(time.time() - T0, 1), "kind": kind, **kw})


def who() -> dict[str, Any]:
    auth = get_http_headers(include={"authorization"}).get("authorization", "")
    return TOKENS[auth.removeprefix("Bearer ")]


mcp = FastMCP("grindstone-kernel-stub")


async def run_consult(consult: dict[str, Any], question: str, me: dict[str, Any]) -> None:
    t = time.time()
    if me.get("consult_url"):
        body = json.dumps({"question": question}).encode()
        req = urllib.request.Request(me["consult_url"], body, {"Content-Type": "application/json"})
        result = await asyncio.to_thread(lambda: json.loads(urllib.request.urlopen(req, timeout=3600).read()))
        consult["answer"], consult["cost"] = result["answer"], result.get("cost")
    else:
        await asyncio.sleep(me["delay_s"])
        consult["answer"] = f"The consult's answer to {question!r} is 42."
    consult["done_at"] = time.time()
    log("consult_done", consult=consult["id"], seconds=round(time.time() - t, 1))
    consult["event"].set()


@mcp.tool
async def ask(question: str, ctx: Context) -> dict[str, Any]:
    """Ask a consult a question. The call stays open until the consult answers; it may take a long time."""
    me = who()
    key = hashlib.sha256(json.dumps([me["capability"], question, []]).encode()).hexdigest()[:16]
    rc = ctx.request_context
    has_progress_token = bool(rc and rc._srctx.meta and rc._srctx.meta.get("progress_token") is not None)
    consult = CONSULTS.get(key)
    if consult and consult.get("answer") is not None:
        consult["delivered"] += 1
        log("cache_hit", session=me["session"], consult=consult["id"])
        return {"status": "answered", "answer": consult["answer"], "cached": True}
    if consult:
        log("join", session=me["session"], consult=consult["id"])
    else:
        consult = {"id": f"c-{len(CONSULTS) + 1}", "key": key, "answer": None, "event": asyncio.Event(), "delivered": 0, "runs": 1,
                   "started": time.time()}
        CONSULTS[key] = consult
        log("consult_start", session=me["session"], consult=consult["id"], progress_token=has_progress_token)
        asyncio.get_running_loop().create_task(run_consult(consult, question, me))
    OPEN[me["session"]] = OPEN.get(me["session"], 0) + 1
    start = time.time()
    progress_n = 0
    try:
        while not consult["event"].is_set():
            elapsed = time.time() - start
            if elapsed >= me["ceiling_s"]:
                log("ceiling", session=me["session"], consult=consult["id"], after_s=round(elapsed))
                return {"status": "consult_ceiling", "consult": consult["id"], "message": "The consult is still working; this call returns so the run can wait."}
            step = min(x for x in (me["progress_s"] or 1e9, me["ceiling_s"] - elapsed) if x > 0)
            try:
                await asyncio.wait_for(consult["event"].wait(), timeout=step)
            except TimeoutError:
                if me["progress_s"]:
                    progress_n += 1
                    await ctx.report_progress(progress_n, None, f"consult {consult['id']} still working")
        consult["delivered"] += 1
        log("delivered", session=me["session"], consult=consult["id"], held_s=round(time.time() - start), progress_sent=progress_n)
        return {"status": "answered", "answer": consult["answer"], "cached": False}
    except asyncio.CancelledError:
        log("call_cancelled", session=me["session"], consult=consult["id"], held_s=round(time.time() - start))
        raise
    finally:
        OPEN[me["session"]] -= 1


SKILL = """Review skill, loaded. Instructions: call the tool `review_codeword` of this server now, then reply with
exactly the codeword it returns, prefixed with CODEWORD: ."""


@mcp.tool
async def load_skill(name: str, ctx: Context) -> str:
    """Load a skill by name: returns its instructions and makes its tools available."""
    me = who()
    await ctx.enable_components(names={"review_codeword"})
    log("load_skill", session=me["session"], name=name)
    return SKILL


@mcp.tool
def review_codeword() -> str:
    """The review skill's tool."""
    log("review_codeword", session=who()["session"])
    return "KIWI-3-TOOL"


mcp.disable(names={"review_codeword"})


class ListLog(Middleware):
    """Records each tools/list a session makes and which of the skill's tools it was given (experiment 6)."""

    async def on_list_tools(self, context: Any, call_next: Any) -> Any:
        tools = await call_next(context)
        try:
            session = who()["session"]
        except Exception:
            session = "?"
        log("tools_list", session=session, has_review_codeword=any(getattr(t, "name", "") == "review_codeword" for t in tools))
        return tools


mcp.add_middleware(ListLog())


class Bearer:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            token = dict(scope["headers"]).get(b"authorization", b"").decode().removeprefix("Bearer ")
            if token not in TOKENS:
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)


mcp_app = mcp.http_app(path="/mcp")
app = FastAPI(lifespan=mcp_app.lifespan)
app.mount("/kernel", Bearer(mcp_app))


@app.post("/admin/tokens")
def issue(body: dict[str, Any]) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    TOKENS[token] = {"delay_s": 60, "progress_s": 30, "ceiling_s": 7200, "capability": "implement", **body}
    return {"token": token}


@app.get("/admin/state")
def state() -> dict[str, Any]:
    return {"log": LOG, "open": OPEN,
            "consults": [{k: v for k, v in c.items() if k != "event"} for c in CONSULTS.values()]}
