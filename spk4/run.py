# /// script
# requires-python = ">=3.12"
# dependencies = ["pyjwt==2.15.1", "cryptography==50.0.2"]
# ///
"""SPK-4 GitHub mechanics, run as the Grindstone App from the operator's machine.

Spike code, never merged. Usage (from the testbed checkout):

    uv run spk4/run.py                 # every experiment, in order, pausing for UI steps
    uv run spk4/run.py --only e1 e3    # selected experiments
    uv run spk4/run.py --public-only   # stop before the private half (e9 to e11)

Reads GRINDSTONE_GITHUB_APP_ID, GRINDSTONE_GITHUB_APP_INSTALLATION_ID and either
GRINDSTONE_GITHUB_APP_PRIVATE_KEY_FILE (a .pem path) or GRINDSTONE_GITHUB_APP_PRIVATE_KEY_B64.
Tokens are never printed or written; results go to spk4/results/<run>/.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import jwt

OWNER, NAME = "ex1tium", "grindstone-testbed"
REPO = f"{OWNER}/{NAME}"
R = f"/repos/{REPO}"
API = "https://api.github.com"
CONTEXT = "grindstone/verification"
RUN = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
OUT = Path(__file__).resolve().parent / "results" / RUN

APP_ID = os.environ["GRINDSTONE_GITHUB_APP_ID"].strip()
INSTALLATION_ID = os.environ["GRINDSTONE_GITHUB_APP_INSTALLATION_ID"].strip()

_token: tuple[str, float] | None = None
visibility_changed = False
summary: list[str] = []


# ---------------------------------------------------------------- recording


def log(kind: str, **fields: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "kind": kind, **fields}) + "\n")


def save(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def say(text: str) -> None:
    print(text, flush=True)
    log("note", text=text)


def verdict(exp: str, result: str, evidence: str) -> None:
    line = f"- **{exp}: {result}.** {evidence}"
    summary.append(line)
    say(line)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.md").write_text(f"# SPK-4 run {RUN}\n\n" + "\n".join(summary) + "\n", encoding="utf-8")


def ask(text: str) -> None:
    log("operator_step", text=text)
    print(f"\n>>> OPERATOR STEP\n{text}\n>>> Press Enter when done.", flush=True)
    input()


# ---------------------------------------------------------------- GitHub API


def private_key() -> bytes:
    path = os.environ.get("GRINDSTONE_GITHUB_APP_PRIVATE_KEY_FILE")
    if path:
        return Path(path).expanduser().read_bytes()
    return base64.b64decode(os.environ["GRINDSTONE_GITHUB_APP_PRIVATE_KEY_B64"])


def app_jwt() -> str:
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": APP_ID}, private_key(), algorithm="RS256")


def request(method: str, path: str, token: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, hdrs, raw = r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")
    msg = payload.get("message") if isinstance(payload, dict) else None
    log("api", method=method, path=path, status=status, message=msg,
        ratelimit={k: v for k, v in hdrs.items() if k.startswith("x-ratelimit")}, etag=hdrs.get("etag"))
    return status, hdrs, payload


def token() -> str:
    global _token
    if _token and _token[1] > time.time() + 300:
        return _token[0]
    status, _, body = request("POST", f"/app/installations/{INSTALLATION_ID}/access_tokens", app_jwt())
    if status != 201:
        sys.exit(f"Could not mint an installation token: HTTP {status} {body}")
    _token = (body["token"], time.time() + 3300)
    return _token[0]


def gh(method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
    return request(method, path, token(), body, headers)


def ok(method: str, path: str, body: Any = None) -> Any:
    status, _, payload = gh(method, path, body)
    if status >= 300:
        sys.exit(f"{method} {path}: HTTP {status} {payload}")
    return payload


def graphql(query: str, **variables: Any) -> Any:
    return ok("POST", "/graphql", {"query": query, "variables": variables})


def wait_for(fetch: Callable[[], Any], done: Callable[[Any], bool], timeout: float = 90, every: float = 3) -> Any:
    deadline = time.time() + timeout
    while True:
        value = fetch()
        if done(value) or time.time() > deadline:
            return value
        time.sleep(every)


def pr(n: int) -> dict[str, Any]:
    return ok("GET", f"{R}/pulls/{n}")


def pr_state(n: int, expect: str | None = None, timeout: float = 90) -> dict[str, Any]:
    """Wait until GitHub has computed mergeability (and reached `expect`, if given)."""
    def done(p: dict[str, Any]) -> bool:
        if p["mergeable_state"] == "unknown":
            return False
        return expect is None or p["mergeable_state"] == expect
    p = wait_for(lambda: pr(n), done, timeout)
    log("pr_state", pr=n, head=p["head"]["sha"], mergeable=p["mergeable"], mergeable_state=p["mergeable_state"])
    return p


def set_status(sha: str, state: str, description: str = "SPK-4", context: str = CONTEXT) -> tuple[int, Any]:
    status, _, body = gh("POST", f"{R}/statuses/{sha}", {"state": state, "context": context, "description": description})
    return status, body


def merge(n: int, sha: str | None = None, method: str = "squash") -> tuple[int, str]:
    body: dict[str, Any] = {"merge_method": method}
    if sha:
        body["sha"] = sha
    status, _, payload = gh("PUT", f"{R}/pulls/{n}/merge", body)
    return status, str(payload.get("message") if isinstance(payload, dict) else payload)


def main_sha() -> str:
    return ok("GET", f"{R}/branches/main")["commit"]["sha"]


def open_pr(branch: str, title: str) -> dict[str, Any]:
    p = ok("POST", f"{R}/pulls", {"title": title, "head": branch, "base": "main",
                                   "body": f"SPK-4 run {RUN}, spike pull request opened by the App. Not a real change."})
    say(f"opened #{p['number']} {branch}")
    return p


# ---------------------------------------------------------------- git, hooks disabled


class Git:
    """A bare mirror of the testbed plus a private clone of it, both with hooks disabled.

    The installation token reaches git only through an environment-scoped extra header,
    never through a remote URL, argv or a credential helper.
    """

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="spk4-"))
        self.no_hooks = self.root / "no-hooks"
        self.no_hooks.mkdir()
        self.mirror = self.root / "mirror.git"
        self.work = self.root / "work"
        self.markers = self.root / "markers"
        self.markers.mkdir()
        self.url = f"https://github.com/{REPO}.git"
        self.run(None, "clone", "--quiet", "--bare", self.url, str(self.mirror))
        self.run(None, "clone", "--quiet", str(self.mirror), str(self.work))
        self.run(self.work, "remote", "add", "gh", self.url)

    def env(self) -> dict[str, str]:
        basic = base64.b64encode(f"x-access-token:{token()}".encode()).decode()
        pairs = [
            ("credential.helper", ""),
            ("core.hooksPath", self.no_hooks.as_posix()),
            ("http.https://github.com/.extraHeader", f"AUTHORIZATION: basic {basic}"),
            ("user.name", "grindstone-spk4"),
            ("user.email", "spk4@grindstone.invalid"),
        ]
        env = {k: v for k, v in os.environ.items() if not k.startswith("GRINDSTONE_")}
        # System config stays on: Git for Windows keeps its TLS settings there.
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": str(len(pairs))})
        for i, (k, v) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
        return env

    def run(self, cwd: Path | None, *args: str) -> str:
        res = subprocess.run(["git", *args], cwd=cwd, env=self.env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
        log("git", args=list(args), code=res.returncode, stderr=res.stderr.strip()[-500:])
        if res.returncode:
            sys.exit(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout.strip()

    def branch_from_main(self, branch: str) -> None:
        self.run(self.work, "fetch", "--quiet", "gh", "main")
        self.run(self.work, "checkout", "--quiet", "-B", branch, "FETCH_HEAD")

    def commit_and_push(self, branch: str, note: str) -> str:
        """Commit a change to spk4/<run>.txt on `branch`, push it to GitHub, return the SHA."""
        f = self.work / "spk4" / f"run-{RUN}-{branch.rsplit('/', 1)[-1]}.txt"
        f.parent.mkdir(exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{note} {time.time()}\n")
        self.run(self.work, "add", str(f))
        self.run(self.work, "commit", "--quiet", "-m", f"test: SPK-4 {note}")
        self.run(self.work, "push", "--quiet", "gh", f"HEAD:refs/heads/{branch}")
        return self.run(self.work, "rev-parse", "HEAD")

    def new_pr(self, suffix: str, title: str) -> tuple[dict[str, Any], str]:
        branch = f"spk4/{RUN}-{suffix}"
        self.branch_from_main(branch)
        sha = self.commit_and_push(branch, f"{suffix} commit 1")
        return open_pr(branch, title), sha

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------- experiments


def detect(label: str) -> dict[str, Any]:
    """Experiment 9's probe: is grindstone/verification required from the App, strict, on main?"""
    repo = ok("GET", R)
    s1, _, rules = gh("GET", f"{R}/rules/branches/main")
    s2, _, rulesets = gh("GET", f"{R}/rulesets")
    checks = [c for r in (rules if isinstance(rules, list) else [])
              if r.get("type") == "required_status_checks"
              for c in r["parameters"]["required_status_checks"]
              if r["parameters"].get("strict_required_status_checks_policy")]
    enforced = any(c.get("context") == CONTEXT and str(c.get("integration_id")) == APP_ID for c in checks)
    record = {"visibility": repo["visibility"], "rules_status": s1, "rules": rules,
              "rulesets_status": s2, "rulesets": rulesets, "verdict_enforced": enforced}
    save(f"e9-{label}.json", record)
    say(f"e9 [{label}] visibility={repo['visibility']} rules HTTP {s1}, rulesets HTTP {s2}, probe says enforced={enforced}")
    return record


def e0(g: Git) -> None:
    status, _, body = request("GET", "/app", app_jwt())
    save("e0-app.json", {"status": status, "slug": body.get("slug"), "permissions": body.get("permissions")})
    repos = ok("GET", "/installation/repositories")
    names = [r["full_name"] for r in repos["repositories"]]
    repo = ok("GET", R)
    verdict("Setup", "ok" if names == [REPO] else "check",
            f"App `{body.get('slug')}`; installation token minted; installation sees {names}; "
            f"`allow_auto_merge`={repo.get('allow_auto_merge')}, merge methods squash={repo.get('allow_squash_merge')}.")
    detect("public-start")


def e1(g: Git) -> None:
    p, sha = g.new_pr("e1", "SPK-4 e1: App status satisfies the required check")
    n = p["number"]
    before = pr_state(n)["mergeable_state"]
    set_status(sha, "pending")
    pending = pr_state(n)["mergeable_state"]
    # Record limits: description 140 vs 141 characters, context 255 vs 256, on a throwaway context.
    lim = {
        "description_140": set_status(sha, "pending", "d" * 140, "spk4/limits")[0],
        "description_141": set_status(sha, "pending", "d" * 141, "spk4/limits")[0],
        "context_255": set_status(sha, "pending", "x", "c" * 255)[0],
        "context_256": set_status(sha, "pending", "x", "c" * 256)[0],
    }
    save("e1-limits.json", lim)
    # Settle the throwaway contexts: a pending optional status reads as `unstable`, not `clean`.
    set_status(sha, "success", "x", "spk4/limits")
    set_status(sha, "success", "x", "c" * 255)
    s, body = set_status(sha, "success", "SPK-4 e1: verified by the App")
    creator = body.get("creator", {}) if isinstance(body, dict) else {}
    after = pr_state(n, "clean")["mergeable_state"]
    code, msg = merge(n, sha)
    verdict("1 App status satisfies the check", "pass" if (after == "clean" and code == 200) else "FAIL",
            f"no status: `{before}`; pending: `{pending}`; App success (HTTP {s}, creator `{creator.get('login')}` "
            f"type `{creator.get('type')}`): `{after}`; merge with head SHA: HTTP {code} {msg}. Limits {lim}.")


def e2(g: Git) -> None:
    p, sha = g.new_pr("e2", "SPK-4 e2: forged status must not satisfy the ruleset")
    n = p["number"]
    ask(f"Forge the status on {sha}, in either or both ways:\n"
        f"  a) Actions (needs Actions enabled):  gh workflow run forge-status.yml -R {REPO} -f sha={sha}\n"
        f"     or the Actions tab -> 'Forge verification status' -> Run workflow, sha = {sha}\n"
        f"  b) Your own account:  gh api repos/{REPO}/statuses/{sha} -f state=success "
        f"-f context={CONTEXT} -f description='SPK-4 e2: forged by the operator'\n"
        f"Wait for the workflow run to finish (if you used a).")
    statuses = ok("GET", f"{R}/commits/{sha}/statuses?per_page=100")
    forged = [{"state": s["state"], "creator": s["creator"]["login"], "type": s["creator"]["type"]}
              for s in statuses if s["context"] == CONTEXT]
    save("e2-statuses.json", forged)
    state = pr_state(n)["mergeable_state"]
    code, msg = merge(n, sha)
    blocked = code != 200
    verdict("2 Forged status", "pass" if (blocked and any(f["state"] == "success" for f in forged)) else
            ("FAIL: a forged status merged; stop and raise a decision record" if not blocked else "inconclusive: no forged success seen"),
            f"forged statuses on the head: {forged}; PR state `{state}`; App merge attempt: HTTP {code} {msg}.")
    ok("PATCH", f"{R}/pulls/{n}", {"state": "closed"})


def e3(g: Git) -> None:
    p, old = g.new_pr("e3", "SPK-4 e3: stale head")
    n, branch = p["number"], p["head"]["ref"]
    set_status(old, "success", "SPK-4 e3: first head")
    first = pr_state(n, "clean")["mergeable_state"]
    new = g.commit_and_push(branch, "e3 commit 2")
    st = wait_for(lambda: pr_state(n), lambda q: q["head"]["sha"] == new)
    c_old, m_old = merge(n, old)
    c_new, m_new = merge(n, new)
    set_status(new, "success", "SPK-4 e3: new head")
    last = pr_state(n, "clean")["mergeable_state"]
    good = first == "clean" and st["mergeable_state"] != "clean" and c_old != 200 and c_new != 200 and last == "clean"
    verdict("3 Stale head", "pass" if good else "FAIL",
            f"success on first head: `{first}`; after a new commit: `{st['mergeable_state']}`; merge with old SHA: HTTP {c_old} {m_old}; "
            f"merge with new SHA, no status: HTTP {c_new} {m_new}; status on new head: `{last}`. Closed unmerged.")
    ok("PATCH", f"{R}/pulls/{n}", {"state": "closed"})


AUTO_ON = """mutation($id: ID!) { enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: SQUASH}) {
  pullRequest { autoMergeRequest { enabledAt enabledBy { login } } } } }"""
AUTO_OFF = """mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) {
  pullRequest { autoMergeRequest { enabledAt } } } }"""
AUTO_Q = """query($id: ID!) { node(id: $id) { ... on PullRequest { autoMergeRequest { enabledAt enabledBy { login } } state } } }"""


def update_branch(n: int, head: str) -> str:
    status, _, body = gh("PUT", f"{R}/pulls/{n}/update-branch", {"expected_head_sha": head})
    log("update_branch", pr=n, status=status, body=body)
    q = wait_for(lambda: pr(n), lambda p: p["head"]["sha"] != head, timeout=60)
    return q["head"]["sha"]


def e4_e5(g: Git) -> None:
    a, a_sha = g.new_pr("e4a", "SPK-4 e4: advances the base")
    b, b_sha = g.new_pr("e4b", "SPK-4 e4: falls behind")
    c, c_sha = g.new_pr("e5", "SPK-4 e5: auto-merge while behind")
    set_status(a_sha, "success", "SPK-4 e4a")
    pr_state(a["number"], "clean")
    ca, ma = merge(a["number"], a_sha)
    say(f"merged e4a: HTTP {ca} {ma}")

    # Experiment 4: strict checks on a branch that is behind.
    nb = b["number"]
    set_status(b_sha, "success", "SPK-4 e4b: old base")
    behind = pr_state(nb, "behind")["mergeable_state"]
    cb, mb = merge(nb, b_sha)
    b_new = update_branch(nb, b_sha)
    after_update = pr_state(nb)["mergeable_state"]
    set_status(b_new, "success", "SPK-4 e4b: updated")
    final = pr_state(nb, "clean")["mergeable_state"]
    cb2, mb2 = merge(nb, b_new)
    good = behind == "behind" and cb != 200 and after_update != "clean" and final == "clean" and cb2 == 200
    verdict("4 Behind the base, strict (Q-7)", "pass" if good else "FAIL",
            f"status success while behind: `{behind}`, merge HTTP {cb} {mb}; after update-branch (new head, no status): "
            f"`{after_update}`; status on new head: `{final}`; merge HTTP {cb2} {mb2}.")

    # Experiment 5: auto-merge on a pull request that is behind.
    if not ok("GET", R).get("allow_auto_merge"):
        ask("Turn on Settings -> General -> Pull Requests -> 'Allow auto-merge' on the testbed.")
    nc, node = c["number"], c["node_id"]
    steps: list[str] = []
    r_on = graphql(AUTO_ON, id=node)
    steps.append(f"armed: {json.dumps(r_on.get('errors') or r_on['data'])}")
    r_off = graphql(AUTO_OFF, id=node)
    steps.append(f"disarmed: {json.dumps(r_off.get('errors') or graphql(AUTO_Q, id=node)['data'])}")
    graphql(AUTO_ON, id=node)
    set_status(c_sha, "success", "SPK-4 e5: old base")
    time.sleep(30)
    s1 = pr(nc)
    steps.append(f"rearmed, success on behind head, 30 s later: merged={s1['merged']} state=`{s1['mergeable_state']}`")
    c_new = update_branch(nc, c_sha)
    time.sleep(30)
    s2 = pr(nc)
    steps.append(f"after update-branch, no status on new head, 30 s later: merged={s2['merged']}")
    set_status(c_new, "success", "SPK-4 e5: updated")
    s3 = wait_for(lambda: pr(nc), lambda p: p["merged"], timeout=180, every=5)
    steps.append(f"status on new head: merged={s3['merged']} by `{(s3.get('merged_by') or {}).get('login')}`")
    good = not s1["merged"] and not s2["merged"] and s3["merged"]
    verdict("5 Auto-merge (Q-7)", "pass" if good else "check", "; ".join(steps) + ".")


def e6(g: Git) -> None:
    rows = {}
    for suffix in ("e1", "e3", "e5", "missing"):
        _, _, body = gh("GET", f"{R}/pulls?head={OWNER}:spk4/{RUN}-{suffix}&state=all")
        rows[suffix] = [(p["number"], p["state"], bool(p["merged_at"])) for p in body]
    save("e6-lookup.json", rows)
    verdict("6 Lookup by branch", "pass" if rows["missing"] == [] and all(rows[k] for k in ("e1", "e3")) else "check",
            f"`GET /pulls?head=owner:branch&state=all` as the App: {rows} (number, state, merged).")


def e7(g: Git) -> None:
    _, _, rl = gh("GET", "/rate_limit")
    prs = ok("GET", f"{R}/pulls?state=closed&per_page=1")
    n = prs[0]["number"]
    sha = prs[0]["head"]["sha"]
    poll = [f"{R}/pulls/{n}", f"{R}/pulls/{n}/comments?per_page=100", f"{R}/pulls/{n}/reviews?per_page=100",
            f"{R}/issues/{n}/comments?per_page=100", f"{R}/commits/{sha}/status", f"{R}/commits/{sha}/check-runs"]
    rows = []
    for path in poll:
        _, h1, _ = gh("GET", path)
        _, h2, _ = gh("GET", path, headers={"If-None-Match": h1.get("etag", "")})
        _, h3, _ = gh("GET", path, headers={"If-None-Match": h1.get("etag", "")})
        rows.append({"path": path.removeprefix(R), "etag": bool(h1.get("etag")),
                     "used_after_200": h1.get("x-ratelimit-used"), "used_after_304s": h3.get("x-ratelimit-used"),
                     "limit": h1.get("x-ratelimit-limit"), "resource": h1.get("x-ratelimit-resource")})
    save("e7-poll.json", {"rate_limit": rl, "poll": rows})
    core = rl["resources"]["core"]
    free = all(r["used_after_200"] == r["used_after_304s"] for r in rows)
    verdict("7 Polling budget", "recorded",
            f"installation core limit {core['limit']}/hour; one poll = {len(poll)} calls; ETag on all: "
            f"{all(r['etag'] for r in rows)}; 304s free of the limit: {free}. Details in e7-poll.json.")


def e8(g: Git) -> None:
    hook = "#!/bin/sh\ntouch '{m}/{name}'\nexit 1\n"
    planted = []
    for where, names in ((g.work / ".git" / "hooks", ("pre-commit", "commit-msg", "post-commit", "pre-push", "reference-transaction")),
                         (g.mirror / "hooks", ("pre-receive", "update", "post-receive", "reference-transaction", "pre-push"))):
        for name in names:
            path = where / name
            # LF endings and forward slashes, so Git for Windows' sh can run them too.
            path.write_text(hook.format(m=g.markers.as_posix(), name=f"{where.parent.name}-{name}"), newline="\n")
            path.chmod(0o755)
            planted.append(f"{where.parent.name}/{name}")
    branch = f"spk4/{RUN}-e8"
    g.branch_from_main(branch)
    f = g.work / "spk4" / f"run-{RUN}-e8.txt"
    f.parent.mkdir(exist_ok=True)
    f.write_text("e8: committed and pushed with hooks disabled\n")
    g.run(g.work, "add", str(f))
    g.run(g.work, "commit", "--quiet", "-m", "test: SPK-4 e8 push without hooks")
    sha = g.run(g.work, "rev-parse", "HEAD")
    # Private clone -> bare mirror: the receiving side's hooks are disabled through --receive-pack.
    g.run(g.work, "push", "--quiet", f"--receive-pack=git -c core.hooksPath={g.no_hooks.as_posix()} receive-pack",
          "origin", f"HEAD:refs/heads/{branch}")
    # Bare mirror -> GitHub with the installation token.
    g.run(g.mirror, "push", "--quiet", "https://github.com/" + REPO + ".git", f"refs/heads/{branch}:refs/heads/{branch}")
    remote = ok("GET", f"{R}/branches/{branch.replace('/', '%2F')}")["commit"]["sha"]
    fired = sorted(p.name for p in g.markers.iterdir())
    verdict("8 Push without hooks (QS-06)", "pass" if (remote == sha and not fired) else "FAIL",
            f"planted failing hooks {planted}; pushed clone -> mirror -> GitHub; branch head on GitHub matches: {remote == sha}; "
            f"hooks that ran: {fired or 'none'}.")


def e9_private(g: Git) -> None:
    global visibility_changed
    visibility_changed = True
    ask(f"Make the testbed PRIVATE: https://github.com/{REPO}/settings -> Danger Zone -> Change visibility -> private.\n"
        "It goes back to public at the end of this run.")
    pub = json.loads((OUT / "e9-public-start.json").read_text()) if (OUT / "e9-public-start.json").exists() else detect("public")
    priv = detect("private")
    good = pub["verdict_enforced"] and not priv["verdict_enforced"]
    verdict("9 Detecting the delivery mode (ADR-40)", "pass" if good else "check: probe does not separate the cases",
            f"public: rules HTTP {pub['rules_status']}, enforced={pub['verdict_enforced']}; private: rules HTTP {priv['rules_status']}, "
            f"enforced={priv['verdict_enforced']}, ruleset enforcement field still "
            f"{[r.get('enforcement') for r in priv['rulesets']] if isinstance(priv['rulesets'], list) else priv['rulesets']}. "
            "Raw responses in e9-*.json.")


def e10(g: Git) -> None:
    p, h1 = g.new_pr("e10", "SPK-4 e10: merge with the verified head (private)")
    n, branch = p["number"], p["head"]["ref"]
    set_status(h1, "success", "SPK-4 e10: verified head")
    base_before = main_sha()
    h2 = g.commit_and_push(branch, "e10 commit 2, after verification")
    wait_for(lambda: pr(n), lambda q: q["head"]["sha"] == h2)
    code, msg = merge(n, h1)
    base_after = main_sha()
    # Control: the same merge with the current head and no status at all, to show nothing else enforces.
    code2, msg2 = merge(n, h2)
    verdict("10 Merging with the verified head (ADR-40)", "pass" if (code != 200 and base_after == base_before) else "FAIL",
            f"merge with verified SHA after a new push: HTTP {code} {msg}; main unchanged: {base_after == base_before}. "
            f"Control, current head with no status: HTTP {code2} {msg2} (200 confirms the ruleset is not enforced while private).")


def e11(g: Git) -> None:
    p, _ = g.new_pr("e11", "SPK-4 e11: merge me in the UI")
    n = p["number"]
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ask(f"Two bypasses, as yourself in the GitHub web UI:\n"
        f"  1) Merge pull request #{n} (any method): https://github.com/{REPO}/pull/{n}\n"
        f"  2) On main, edit README.md (add a line) and 'Commit directly to the main branch'.")
    merged = pr(n)
    commit = ok("GET", f"{R}/commits/{merged.get('merge_commit_sha')}") if merged.get("merge_commit_sha") else {}
    commits = ok("GET", f"{R}/commits?sha=main&since={since}&per_page=20")
    rows = []
    for c in commits:
        prs = ok("GET", f"{R}/commits/{c['sha']}/pulls")
        rows.append({"sha": c["sha"][:7], "author": (c.get("author") or {}).get("login"),
                     "committer": (c.get("committer") or {}).get("login"), "parents": len(c["parents"]),
                     "pulls": [x["number"] for x in prs], "message": c["commit"]["message"].splitlines()[0]})
    s_act, _, activity = gh("GET", f"{R}/activity?ref=refs/heads/main&per_page=10")
    acts = [{"type": a["activity_type"], "actor": (a.get("actor") or {}).get("login"), "after": a["after"][:7], "at": a["timestamp"]}
            for a in (activity if isinstance(activity, list) else [])]
    save("e11-bypass.json", {"pr": {"merged": merged["merged"], "merged_by": (merged.get("merged_by") or {}).get("login"),
                                    "merge_commit_parents": len(commit.get("parents", []))},
                             "commits_on_main": rows, "activity_status": s_act, "activity": acts})
    direct = [r for r in rows if not r["pulls"]]
    verdict("11 Detecting a bypass (ADR-40)", "pass" if (merged["merged"] and direct) else "check",
            f"UI merge seen: merged_by `{(merged.get('merged_by') or {}).get('login')}`, merge commit parents "
            f"{len(commit.get('parents', []))}; commits on main with no pull request: {[r['sha'] for r in direct]}; "
            f"activity API HTTP {s_act}: {acts[:4]}.")


def e9_restore(g: Git) -> None:
    global visibility_changed
    visibility_changed = False
    ask(f"Make the testbed PUBLIC again: https://github.com/{REPO}/settings -> Danger Zone -> Change visibility -> public.")
    back = detect("public-end")
    verdict("Restore", "ok" if back["verdict_enforced"] and back["visibility"] == "public" else "CHECK THE TESTBED SETTINGS",
            f"visibility `{back['visibility']}`, probe says enforced={back['verdict_enforced']}.")


EXPERIMENTS: dict[str, Callable[[Git], None]] = {
    "e0": e0, "e1": e1, "e2": e2, "e3": e3, "e4": e4_e5, "e6": e6, "e7": e7, "e8": e8,
    "e9": e9_private, "e10": e10, "e11": e11, "restore": e9_restore,
}
PRIVATE = ("e9", "e10", "e11", "restore")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(EXPERIMENTS), help="run these (e4 also runs e5)")
    ap.add_argument("--public-only", action="store_true", help="skip e9, e10, e11 and restore")
    ap.add_argument("--merge", type=int, metavar="PR",
                    help="set the App's success on that pull request's head and merge it (squash), then stop")
    args = ap.parse_args()
    if args.merge:
        head = pr(args.merge)["head"]["sha"]
        set_status(head, "success", "SPK-4: merged by the operator's request")
        state = pr_state(args.merge, "clean")["mergeable_state"]
        code, msg = merge(args.merge, head)
        say(f"#{args.merge} at {head[:7]}: `{state}`, merge HTTP {code} {msg}")
        return
    names = args.only or [k for k in EXPERIMENTS if not (args.public_only and k in PRIVATE)]
    say(f"SPK-4 run {RUN}: {names}; results in {OUT}")
    g = Git()
    try:
        for name in names:
            say(f"\n== {name}")
            EXPERIMENTS[name](g)
    finally:
        g.cleanup()
        if visibility_changed:
            e9_restore(g)
        say(f"\nDone. Summary: {OUT / 'summary.md'}")


if __name__ == "__main__":
    main()
