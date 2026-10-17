# SPK-4 run 20261017T151254Z

- **Setup: ok.** App `grindstone-dev-rootnode`; installation token minted; installation sees ['ex1tium/grindstone-testbed']; `allow_auto_merge`=True, merge methods squash=True.
- **1 App status satisfies the check: FAIL.** no status: `blocked`; pending: `blocked`; App success (HTTP 201, creator `grindstone-dev-rootnode[bot]` type `Bot`): `unstable`; merge with head SHA: HTTP 200 Pull Request successfully merged. Limits {'description_140': 201, 'description_141': 422, 'context_255': 201, 'context_256': 422}.
- **2 Forged status: pass.** forged statuses on the head: [{'state': 'success', 'creator': 'ex1tium', 'type': 'User'}]; PR state `blocked`; App merge attempt: HTTP 405 Repository rule violations found

Required status check "grindstone/verification" was not set by the expected GitHub app.

.
- **3 Stale head: pass.** success on first head: `clean`; after a new commit: `blocked`; merge with old SHA: HTTP 409 Head branch was modified. Review and try the merge again.; merge with new SHA, no status: HTTP 405 Repository rule violations found

Required status check "grindstone/verification" is expected.

; status on new head: `clean`. Closed unmerged.
- **4 Behind the base, strict (Q-7): pass.** status success while behind: `behind`, merge HTTP 405 Repository rule violations found

Required status check "grindstone/verification" is expected.

; after update-branch (new head, no status): `blocked`; status on new head: `clean`; merge HTTP 200 Pull Request successfully merged.
- **5 Auto-merge (Q-7): pass.** armed: {"enablePullRequestAutoMerge": {"pullRequest": {"autoMergeRequest": {"enabledAt": "2026-10-17T15:24:20Z", "enabledBy": {"login": "grindstone-dev-rootnode"}}}}}; disarmed: {"node": {"autoMergeRequest": null, "state": "OPEN"}}; rearmed, success on behind head, 30 s later: merged=False state=`behind`; after update-branch, no status on new head, 30 s later: merged=False; status on new head: merged=True by `grindstone-dev-rootnode[bot]`.
- **6 Lookup by branch: pass.** `GET /pulls?head=owner:branch&state=all` as the App: {'e1': [(2, 'closed', True)], 'e3': [(4, 'closed', False)], 'e5': [(7, 'closed', True)], 'missing': []} (number, state, merged).
- **7 Polling budget: recorded.** installation core limit 5000/hour; one poll = 6 calls; ETag on all: True; 304s free of the limit: True. Details in e7-poll.json.
- **8 Push without hooks (QS-06): pass.** planted failing hooks ['.git/pre-commit', '.git/commit-msg', '.git/post-commit', '.git/pre-push', '.git/reference-transaction', 'mirror.git/pre-receive', 'mirror.git/update', 'mirror.git/post-receive', 'mirror.git/reference-transaction', 'mirror.git/pre-push']; pushed clone -> mirror -> GitHub; branch head on GitHub matches: True; hooks that ran: none.
- **9 Detecting the delivery mode (ADR-40): pass.** public: rules HTTP 200, enforced=True; private: rules HTTP 403, enforced=False, ruleset enforcement field still {'message': 'Upgrade to GitHub Pro or make this repository public to enable this feature.', 'documentation_url': 'https://docs.github.com/rest/repos/rules#get-all-repository-rulesets', 'status': '403'}. Raw responses in e9-*.json.
- **10 Merging with the verified head (ADR-40): pass.** merge with verified SHA after a new push: HTTP 409 Head branch was modified. Review and try the merge again.; main unchanged: True. Control, current head with no status: HTTP 200 Pull Request successfully merged (200 confirms the ruleset is not enforced while private).
- **11 Detecting a bypass (ADR-40): pass.** UI merge seen: merged_by `ex1tium`, merge commit parents 2; commits on main with no pull request: ['ed73c5d']; activity API HTTP 200: [{'type': 'push', 'actor': 'ex1tium', 'after': 'ed73c5d', 'at': '2026-10-17T15:43:42Z'}, {'type': 'pr_merge', 'actor': 'ex1tium', 'after': '8560d2c', 'at': '2026-10-17T15:43:17Z'}, {'type': 'pr_merge', 'actor': 'grindstone-dev-rootnode[bot]', 'after': '6db54fe', 'at': '2026-10-17T15:42:55Z'}, {'type': 'pr_merge', 'actor': 'grindstone-dev-rootnode[bot]', 'after': '6b4cb15', 'at': '2026-10-17T15:25:53Z'}].
- **Restore: ok.** visibility `public`, probe says enforced=True.
