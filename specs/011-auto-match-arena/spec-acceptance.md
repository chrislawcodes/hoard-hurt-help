# Acceptance Criteria: Auto-Match Arena & Operator Join Page

## User Stories

| ID | Title | Priority |
|----|-------|----------|
| US-1 | Practice Arena: always something to join | P1 |
| US-2 | Auto-scheduled matches every 30 minutes | P1 |
| US-3 | Operator join page at /play | P1 |
| US-4 | "Play now →" routes to /play | P2 |
| US-5 | Lobby shows auto-match and Practice Arena | P2 |

---

## Acceptance Scenarios

### US-1: Practice Arena

- Given there is no Practice Arena match currently open, When the server starts (or a previous Practice Arena match completes), Then the system automatically creates a new Practice Arena match with Sim bots pre-registered.
- Given a Practice Arena match is open with Sim bots registered, When a human player joins their bot to it, Then the match starts immediately — no countdown, no wait.
- Given the match just started (triggered by a human join), When the system detects this, Then a new Practice Arena match is created immediately so the next human can join.
- Given the Practice Arena starts with 1 human bot and 4 Sim bots, When the match begins, Then all 5 participants play normally under the existing game engine.
- Given a user has no bot yet, When they view the Practice Arena card on /play, Then they see a prompt to set up a bot first, not a broken join button.

### US-2: Auto-scheduled matches

- Given the system clock reaches a scheduled interval (every 30 minutes: :00 and :30), When no auto-match is currently open for that interval, Then the system creates a new auto-match in "upcoming" state.
- Given an auto-match is open, When human bots join during the window, Then they are registered normally and appear in the lobby player count.
- Given the scheduled start time arrives, When the match has fewer participants than its player limit, Then Sim bots are registered to fill remaining slots and the match starts.
- Given the scheduled start time arrives, When zero humans joined, Then Sims fill all slots and the match still starts — no human minimum.
- Given an auto-match is running, When the next 30-minute interval arrives, Then a new auto-match is created for the next window.
- Given an admin creates a manual match, When the 30-minute interval also fires, Then both matches coexist independently.

### US-3: Operator join page

- Given a user is not signed in, When they visit /play, Then they see a "Sign in to play" prompt alongside a brief explanation — not a blank or broken page.
- Given a user is signed in but has no bot, When they visit /play, Then they see a "Set up your bot" CTA linking to /me/bots.
- Given a user is signed in with a connected bot, When they visit /play, Then they see: bot connection status, Practice Arena card with "Join now →", next auto-match card with countdown and "Join →", and their active/upcoming games.
- Given a user is signed in with a bot that is not yet connected, When they visit /play, Then the Practice Arena join button is disabled with "Connect your bot first" and a link to the bot detail page.
- Given a user visits /play and joins the Practice Arena, When the join succeeds, Then they are redirected to the game viewer for that match.
- Given multiple bots exist on the account, When the user joins from /play, Then they can select which bot to play as.

### US-4: "Play now →" routing

- Given a visitor clicks "Play now →" on the homepage, When they are not signed in, Then they land on /play which shows a sign-in prompt.
- Given a visitor clicks "Play now →", When they are already signed in with a bot, Then they land on /play showing Practice Arena and next auto-match.
- Given the spectator lobby at /games/hoard-hurt-help still exists, When a user navigates to it directly, Then it behaves identically to today.

### US-5: Lobby visibility

- Given a Practice Arena match is open, When any user views the HHH lobby, Then the Practice Arena appears in the upcoming section.
- Given an auto-match is open, When any user views the HHH lobby, Then the auto-match appears with the correct start time.
- Given neither type has an admin-created match alongside them, When a user views the lobby, Then the upcoming section still shows Practice Arena and/or next auto-match rather than being empty.

---

## Success Criteria

- **SC-001**: A first-time bot operator with a connected bot can join the Practice Arena and watch their bot play within 60 seconds of arriving at /play.
- **SC-002**: At any time of day, at least one joinable match (Practice Arena or auto-match starting within 30 minutes) is visible on /play.
- **SC-003**: The /play page answers "what do I do next?" for three user states (not signed in, no bot, connected bot) without additional navigation.
- **SC-004**: Auto-matches start within 60 seconds of their scheduled boundary time whether zero or many humans joined.
- **SC-005**: No admin action is required to keep Practice Arena or auto-match schedule running; both recover automatically after a server restart.
- **SC-006**: The existing spectator lobby, game viewer, join form, and admin match creation are unaffected.

---

## Key Constraints

- **Reuse `add_sims_to_game()`**: Do not write a second Sim seating path — the existing function in `app/engine/sims/seating.py` must be called as-is. *Why: avoids drift between admin-seated Sims and auto-seated Sims; the existing function already handles all edge cases (unique bot names, seat cap).*
- **Reuse `start_game()`**: Practice Arena immediate start calls the existing `start_game()` from `app/engine/scheduler.py`. *Why: ensures the same SCHEDULED→REGISTERING→ACTIVE state machine path is followed regardless of how a match starts.*
- **Idempotent arena functions**: `ensure_practice_arena` and `ensure_auto_match` must be safe to call on every 2-second poller tick without creating duplicates. *Why: the poller has no memory between ticks; the DB is the source of truth.*
- **No new background processes**: Extend the existing `SchedulerRegistry` poller. *Why: one poller is easier to reason about and avoids asyncio task proliferation.*
- **SQLite batch mode**: The 0019 migration must use `op.batch_alter_table` for the column add. *Why: SQLite cannot ALTER TABLE in place; batch mode is required or migrations fail on dev DBs.*
- **`match_kind` defaults to `"manual"`**: All existing matches get this value with no data rewrite. *Why: backward compatibility — existing code that doesn't filter by match_kind sees no change in behavior.*
