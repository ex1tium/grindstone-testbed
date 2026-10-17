"""SPK-4 experiments 6, 7 and the public half of 9: read-only REST calls."""

import gh

R = f"/repos/{gh.REPO}"
T = "x"  # the session proxy substitutes its own credentials; see the report
BR = "claude/youthful-franklin-oq4j5j"

print("## 6 lookup by head branch")
for state in ("open", "all"):
    s, _, b = gh.raw("GET", f"{R}/pulls?head=ex1tium:{BR}&state={state}", T)
    print(state, s, [(p["number"], p["state"], p["head"]["sha"][:7], p["merged_at"]) for p in b])
s, _, b = gh.raw("GET", f"{R}/pulls?head=ex1tium:no-such-branch&state=all", T)
print("missing branch", s, b)
s, _, b = gh.raw("GET", f"{R}/commits/{BR}/pulls", T)
print("commit->pulls", s, [p["number"] for p in b])

print("## 7 one poll")
gh.CALLS.clear()
poll = [
    f"{R}/pulls/1",                        # state, head sha, mergeable_state
    f"{R}/pulls/1/comments?per_page=100",   # review comments
    f"{R}/pulls/1/reviews?per_page=100",
    f"{R}/issues/1/comments?per_page=100",  # conversation comments
]
_, _, pr = gh.raw("GET", poll[0], T)
sha = pr["head"]["sha"]
poll += [f"{R}/commits/{sha}/status", f"{R}/commits/{sha}/check-runs"]
etags = {}
for p in poll[1:]:
    gh.raw("GET", p, T)
for m, p, s, h in gh.CALLS:
    etags[p] = next((v for k, v in h.items() if k.lower() == "etag"), None)
    print(s, p.removeprefix(R), "etag" if etags[p] else "no-etag", h.get("X-RateLimit-Used") or h.get("x-ratelimit-used"))
print("mergeable_state", pr["mergeable_state"], "mergeable", pr["mergeable"])
print("## 7 conditional requests")
for p, e in etags.items():
    if not e:
        continue
    before = gh.raw("GET", "/rate_limit", T)[2]["resources"]["core"]["used"]
    s, h, _ = gh.raw("GET", p, T, headers={"If-None-Match": e})
    after = gh.raw("GET", "/rate_limit", T)[2]["resources"]["core"]["used"]
    print(s, p.removeprefix(R), "core used delta", after - before)
s, _, rl = gh.raw("GET", "/rate_limit", T)
print("core limit", rl["resources"]["core"]["limit"])

print("## 9 public: rules in force vs rulesets")
_, _, rules = gh.raw("GET", f"{R}/rules/branches/main", T)
req = [r for r in rules if r["type"] == "required_status_checks"]
print("required checks in force", [c for r in req for c in r["parameters"]["required_status_checks"]])
_, _, rs = gh.raw("GET", f"{R}/rulesets?includes_parents=true", T)
print("rulesets", [(r["id"], r["enforcement"]) for r in rs])
