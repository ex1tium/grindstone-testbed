"""SPK-4 spike client: mints App installation tokens and calls the REST API.

Spike code, never merged. Secrets come from GRINDSTONE_* variables and are never printed.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import jwt

API = "https://api.github.com"
REPO = "ex1tium/grindstone-testbed"
_CTX = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE", "/root/.ccr/ca-bundle.crt"))
_token: tuple[str, float] | None = None
CALLS: list[tuple[str, str, int, dict[str, str]]] = []


def app_jwt() -> str:
    key = base64.b64decode(os.environ["GRINDSTONE_GITHUB_APP_PRIVATE_KEY_B64"])
    now = int(time.time())
    claims = {"iat": now - 60, "exp": now + 540, "iss": os.environ["GRINDSTONE_GITHUB_APP_ID"].strip()}
    return jwt.encode(claims, key, algorithm="RS256")


def raw(method: str, path: str, token: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=60) as r:
            status, hdrs, payload = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, payload = e.code, dict(e.headers), e.read()
    keep = {k: v for k, v in hdrs.items() if k.lower().startswith(("x-ratelimit", "etag", "link"))}
    CALLS.append((method, path, status, keep))
    try:
        parsed = json.loads(payload) if payload else None
    except ValueError:
        parsed = payload.decode(errors="replace")
    return status, hdrs, parsed


def installation_token() -> str:
    global _token
    if _token and _token[1] > time.time() + 120:
        return _token[0]
    inst = os.environ["GRINDSTONE_GITHUB_APP_INSTALLATION_ID"].strip()
    status, _, body = raw("POST", f"/app/installations/{inst}/access_tokens", app_jwt())
    if status != 201:
        raise SystemExit(f"installation token: HTTP {status} {body.get('message') if isinstance(body, dict) else ''}")
    _token = (body["token"], time.time() + 3300)
    return _token[0]


def call(method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
    return raw(method, path, installation_token(), body, headers)


def show(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2)
    print()
