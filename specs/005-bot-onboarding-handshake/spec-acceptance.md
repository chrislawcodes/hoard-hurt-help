# Acceptance Criteria: Live Connection Handshake for Bot Onboarding

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | Confirm the bot connected, live | P1 |
| US-2 | Guided from connected to playing | P1 |
| US-3 | See the first move (the win) | P1 |
| US-4 | Failed connection is caught | P2 |
| US-5 | Don't lose the key | P2 |
| US-6 | Don't slow the returning operator | P3 |

## Acceptance Scenarios

### US-1: Confirm the bot connected, live
- Given a freshly created bot whose detail page is open, When nothing has connected yet, Then the page shows a calm "Waiting for your bot to connect… keep your AI running" state.
- Given that page is open, When the bot makes its first authenticated agent call, Then the page updates in place to a "✓ Connected" state within a few seconds, no reload.
- Given the bot has connected before, When the operator opens the detail page later, Then it shows the connected state directly (durable, not a one-time animation).

### US-2: Guided from connected to playing
- Given a connected bot in no games, When I view the panel, Then it says the last step is to get the bot into a game and shows a primary "Join a game →" action.
- Given a connected bot in no games, When I look at the Games section, Then its empty state reads "Connected but not in a game yet — that's the last step. Join a game →".
- Given I follow the "Join a game" action, When I land on the join path, Then I can enter this bot into an open game.

### US-3: See the first move (the win)
- Given a connected bot now in a game that has not yet moved, When I view the panel, Then it shows "✓ In '[game name]'. Waiting for its first move…".
- Given that bot, When it submits its first action, Then the panel updates in place to "✓ [bot name] just made its first move. Watch it live →" linking to the game viewer.
- Given the bot has already moved in a past game, When I open the detail page, Then the panel reflects the playing/established state rather than re-running the first-move celebration.

### US-4: Failed connection is caught
- Given a bot detail page open, When the connection is taking too long / a bad code is suspected, Then the panel surfaces a recovery nudge to reissue and paste again (passive — see Key Constraints).
- Given the operator reissues, When they paste the new message and the bot connects, Then the panel proceeds to the connected state normally.

### US-5: Don't lose the key
- Given the fresh-key setup message is shown, When I read near it, Then a quiet line notes the code won't be shown again and points to reissue if it's lost.
- Given I lost the code, When I reissue, Then a new setup message is shown and the old code stops working.

### US-6: Don't slow the returning operator
- Given a bot that has connected and played before, When I open its detail page, Then the onboarding panel is absent or collapsed to a quiet status line, not a large block.

## Success Criteria
- SC-001: A first-time operator who pastes the setup message and leaves their AI running sees on-page confirmation that the bot connected, without reloading or navigating away.
- SC-002: From the setup page, a connected operator can always tell what to do next; a connected-but-idle bot never presents as a dead end or a finished state.
- SC-003: The operator can get from "just connected" to "watching my bot's first move" without leaving the setup page to work out the steps.
- SC-004: A failed paste (bad/stale code) results in a clear on-page recovery path rather than indefinite waiting.
- SC-005: A returning operator opening an established bot is not shown the first-run waiting/celebration block.
- SC-006: No regression to credential security: the connection code remains unrecoverable after its one-time display.

## Key Constraints
- **Bad-key path is passive** — a wrong/stale code resolves to no bot, so it can't be attributed to this bot's channel; US-4 is a timed "reissue?" nudge + the AI-reported `invalid key`, not a server-detected live event. *Why: honesty over a signal we can't faithfully produce.*
- **First paint must be correct without events** — a reload always shows the true state; SSE only re-fetches and adds a one-shot flourish. *Why: FR-004 + US-3.3 (no re-run celebration on reload).*
- **Owner-only** — status/stream are private to the bot's owner; never on public pages. *Why: FR-010.*
- **Paste-once preserved** — the key is never re-rendered. *Why: FR-011 / security.*
- **Additive, backfill-free migration** — `NULL` = never connected; established bots resolve via play history. *Why: data-critical rule.*
