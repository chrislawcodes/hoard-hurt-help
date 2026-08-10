# Findings — opt-in key sign-in on `/mcp` (Antigravity)

Thin path. One review fan on the whole diff: four lenses
(`regression-adversarial`, `silent-failure`, `completeness-adversarial`,
`test-honesty`) plus one blind reviewer given only the acceptance criteria and
the diff. Every finding below has a recorded verdict.

## Process note — the "flaky test" finding was an artifact of how I ran the review

Two reviewers independently reported an intermittent failure where a freshly
created connection came back with `mcp_key_signin_enabled` **true** — i.e. the
security default failing. It does not reproduce: 15 targeted runs of the four
named files and 10 full-suite runs, all clean, worktree clean throughout.

The cause was the review setup, not the code. All five reviewers ran
concurrently in the **same worktree**, and the `test-honesty` reviewer was
deliberately mutating production files to check that tests fail — including
`key_auth.py`'s opt-in check, the exact line whose removal produces exactly this
symptom. Whichever suite happened to run during a mutation window saw it.

Lesson for the next fan: give reviewers that mutate code their own worktree
(`isolation: "worktree"`), or forbid mutation and rely on inspection.

## Findings

| # | Reviewer/lens | Finding (one line) | Verdict | Reason |
|---|---------------|--------------------|---------|--------|
| 1 | test-honesty + blind | `test_key_token_carries_the_scopes_the_server_enforces` was circular — it called `verify_connection_key` directly, so a hardcoded scope list in `oauth_auth` still passed (proved by mutation) | **fix now** | This is the one bug that actually shipped and was only caught by hand. Replaced with `test_provider_dispatches_a_key_with_the_scopes_it_enforces`, which goes through the real `verify_token`. Re-ran the same mutation: the new test fails as it should. |
| 2 | blind + test-honesty | The real dispatcher `_ConnectAtSignInGoogleProvider.verify_token` had **no** test coverage on either branch; only a manual one-off e2e run exercised it | **fix now** | A refactor could silently detach the key path with every test green. Added coverage for both branches: key → key path, JWT → untouched OAuth path. |
| 3 | test-honesty | The new detail-page card (`Sign in with a key`) had zero test coverage — and it is the only render path with a real `Connection` in scope, so the only place a live key could leak into HTML | **fix now** | Added two tests: the card renders with the placeholder and never the real key (switch on and off), and an OAuth connection is not offered the switch. |
| 4 | silent-failure + blind | `key_auth.py` logged `raw_key[:11]`, which is the 8-char fixed prefix plus **3 characters of the live secret**, on every rejected key | **fix now** | Cheap, and the file's own framing is "never the plaintext". Now logs `bot_key_hint` (last 4) like the rest of the codebase. Note the same `[:11]` pattern is pre-existing at `app/deps.py:155,186` — see follow-up. |
| 5 | regression + completeness | Three "header-less OAuth: no key, no `--header`" claims (connect-guide comment + docstring, `docs/setup-mcp.md`) are now false, sitting feet from the code that adds a header | **fix now** | A future edit could "restore consistency" by deleting the credential this ships. All three now carve out the Antigravity exception explicitly. |
| 6 | completeness | `docs/platform/AGENT_LUDUM_ARCHITECTURE.md` states the invariant "**Auth: OAuth-only at `/mcp`**… the `X-Connection-Key` path is **dropped at `/mcp`**", which this change contradicts; `key_auth.py` appears nowhere in it | **fix now** | CLAUDE.md makes this doc required reading, so a false invariant there misleads every future agent. Heading corrected and a subsection added covering the exception, the opt-in gate, the claim-based resolution, and the immediate-revoke difference. |
| 7 | test-honesty | `test_paused_connection_authenticates_then_fails_downstream` asserted only `403`, so it would also pass if the wrong branch (`ACCOUNT_DISABLED`) fired | **fix now** | One-line tightening; now asserts `CONNECTION_PAUSED`. |
| 8 | completeness | The connections **list** page never shows that key sign-in is on — `detail.html` is the only place it appears | **defer** | Real gap, but not a correctness or security hole: the setting cannot be enabled except from the detail page, so nobody can have it on without having seen it. Follow-up task spawned. |
| 9 | completeness | Admins have **no** way to see the flag — not templated on `admin/user_detail.html`, and `/me/connections/{id}` 404s for non-owners so there is no click-through | **defer** | Genuine auditing gap for a security-relevant setting, but it is a new admin surface, not part of this change. Follow-up task spawned. |
| 10 | blind | No "just the key" reveal: the owner has to extract the key out of the connector's `--install …` shell command to paste it into Antigravity's header | **defer** | Real friction, and part of the wider "new user has no guided path" gap already called out. Follow-up task spawned; connect-screen changes need Chris's sign-off (it has flip-flopped three times). |
| 11 | test-honesty | The migration's `server_default` backfill for **pre-existing** rows is untested — `test_new_connections_default_to_off` only covers the ORM default via `create_all` | **defer** | Standard `nullable=False` + `server_default=false()` pattern, and I verified the applied column is `BOOLEAN NOT NULL DEFAULT '0'` against a real migrated SQLite DB by hand. Low value per unit of test machinery. |
| 12 | completeness | `docs/design/connect-screen-mcp-connection.md` also claims "no `sk_conn_` key in the paste, ever" and still shows a retired `gemini mcp add` command | **defer** | Already stale before this change for unrelated reasons, and it is not one of the two places the code's own sync comment names. Noted in the follow-up task. |
| 13 | regression | Reported flaky test (`test_new_connections_default_to_off` / `test_token_resolves_to_its_own_connection`) | **reject** | Artifact of concurrent reviewers mutating production code in a shared worktree — see the process note above. 25 clean runs after the fact, worktree clean. |

## Verified clean (no finding)

The OAuth path for Claude Code / Codex / Claude Desktop is byte-for-byte
unchanged — one reviewer traced the FastMCP MRO to confirm `super().verify_token`
resolves to the same `OAuthProvider.verify_token → load_access_token` chain as
before. No caching outlives a revoked key or a flipped-off switch
(`stateless_http=True`, and the verifier does a live DB read every request). The
raw key never reaches a response body, template, tool result, or the public
spectator JSON. `AccessToken.client_id`/`subject`/`expires_at` have no downstream
consumer that could misread the key-shaped values. Rotation, pause, delete, and
the dedupe job all leave the flag correctly untouched. The `antigravity`
substring rule collides with nothing and every other provider surface keys off
the `ConnectionProvider.GEMINI` enum, so nothing else needed updating.
