"""SPK-1 experiment 3: a stub kernel MCP server. FastMCP mounted in FastAPI; the bearer token is checked
on every HTTP request (not only at connect), so a revoked token fails mid-session (ADR-16, Q-6).

Admin routes (spike only): POST /admin/tokens {session, task, run, capability} -> {token};
POST /admin/revoke/{token}; GET /admin/calls. MCP at /kernel/mcp.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

TOKENS: dict[str, dict[str, Any]] = {}
CALLS: list[dict[str, Any]] = []
REFUSED: list[dict[str, Any]] = []

mcp = FastMCP("grindstone-kernel-stub")


def identity() -> dict[str, Any]:
    auth = get_http_headers(include={"authorization"}).get("authorization", "")
    return TOKENS[auth.removeprefix("Bearer ")]


@mcp.tool
def whoami() -> dict[str, Any]:
    """Report which Grindstone session, task, run and capability this call belongs to."""
    who = identity()
    CALLS.append({"tool": "whoami", "session": who["session"], "t": time.time()})
    return {k: who[k] for k in ("session", "task", "run", "capability")}


@mcp.tool
def report(text: str) -> str:
    """Send a progress report to Grindstone."""
    who = identity()
    CALLS.append({"tool": "report", "session": who["session"], "text": text, "t": time.time()})
    return "recorded"


class BearerPerRequest:
    """Pure ASGI middleware: every HTTP request to the MCP app needs a live token."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            token = headers.get(b"authorization", b"").decode().removeprefix("Bearer ")
            entry = TOKENS.get(token)
            if entry is None or entry["revoked"]:
                REFUSED.append({"method": scope["method"], "known": entry is not None, "t": time.time()})
                body = json.dumps({"error": "invalid or revoked token"}).encode()
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


mcp_app = mcp.http_app(path="/mcp")
app = FastAPI(lifespan=mcp_app.lifespan)
app.mount("/kernel", BearerPerRequest(mcp_app))


@app.post("/admin/tokens")
def issue(body: dict[str, str]) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    TOKENS[token] = {**body, "revoked": False}
    return {"token": token}


@app.post("/admin/revoke/{token}")
def revoke(token: str) -> dict[str, bool]:
    TOKENS[token]["revoked"] = True
    return {"revoked": True}


@app.get("/admin/calls")
def calls() -> dict[str, Any]:
    return {"calls": CALLS, "refused": REFUSED}
