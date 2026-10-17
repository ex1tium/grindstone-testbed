"""Experiment 3: a tool call with a revoked token fails mid-session."""

import asyncio
import json
import subprocess
import sys
import time
import urllib.request

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

BASE = "http://127.0.0.1:8711"


def post(path: str, body: object = None) -> dict:
    req = urllib.request.Request(BASE + path, json.dumps(body or {}).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


async def main() -> None:
    token = post("/admin/tokens", {"session": "s1", "task": "T-1", "run": "R-1", "capability": "implement"})["token"]
    transport = StreamableHttpTransport(f"{BASE}/kernel/mcp", headers={"Authorization": f"Bearer {token}"})
    out: dict = {}
    async with Client(transport) as client:
        out["tools"] = [t.name for t in await client.list_tools()]
        out["before"] = (await client.call_tool("whoami")).structured_content
        post(f"/admin/revoke/{token}")
        try:
            await client.call_tool("whoami")
            out["after_revoke"] = "ACCEPTED"
        except Exception as e:  # the same session, same connection object
            out["after_revoke"] = f"refused: {type(e).__name__}: {str(e)[:160]}"
    bad = StreamableHttpTransport(f"{BASE}/kernel/mcp", headers={"Authorization": "Bearer forged"})
    try:
        async with Client(bad) as client:
            await client.list_tools()
        out["forged"] = "ACCEPTED"
    except Exception as e:
        out["forged"] = f"refused: {type(e).__name__}"
    out["server_log"] = json.loads(urllib.request.urlopen(BASE + "/admin/calls").read())
    out["pass"] = out["after_revoke"].startswith("refused") and out["forged"].startswith("refused") and out["before"]["session"] == "s1"
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", "8711", "--log-level", "warning"])
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/admin/calls")
                break
            except OSError:
                time.sleep(0.2)
        asyncio.run(main())
    finally:
        server.terminate()
