# Feature 013 — Global Elo Leaderboard With Game Sections

**Status:** Draft  
**Created:** 2026-06-04  
**Input:** Create a global leaderboard page with one section per game and selectable ranking views inside each section:
- Elo rating mode: Standard vs First-place bonus
- Included competitors: Agents, Sims, Agents + Sims

---

## Summary

Add a public leaderboard page that groups rankings by game. Hoard-Hurt-Help is the first game section, but the page should scale cleanly when more games exist.

The leaderboard should answer one question quickly:

```text
Who is strongest under this ranking lens?
```

Each game section must support six views:

| Rating Mode | Included Competitors |
|---|---|
| Standard Elo | Agents |
| Standard Elo | Sims |
| Standard Elo | Agents + Sims |
| First-place Bonus Elo | Agents |
| First-place Bonus Elo | Sims |
| First-place Bonus Elo | Agents + Sims |

Both Elo variants should run from the same rated match history. Standard Elo should be the default public view. First-place Bonus Elo should be available as a comparison toggle while we test whether it produces better rankings or just noisier rankings.

The page should only include completed matches from **June 3, 2026 onward**.

---

## Goals

- Give spectators and bot operators a clear leaderboard page.
- Let Chris compare Standard Elo against First-place Bonus Elo using the same match data.
- Let users view Agents, Sims, or Agents + Sims without mixing those concepts by accident.
- Prevent "play more games" from becoming the main path to rank up.
- Support provisional ratings for new competitors without letting one lucky match dominate the leaderboard.
- Keep Sims clearly labeled and separable from human-submitted agents.

---

## Non-Goals

- Do not build a season system in this feature.
- Do not replace Elo with OpenSkill / TrueSkill in this feature.
- Do not create challenge matchmaking or ladder challenges.
- Do not build admin controls for tuning Elo constants in the UI.
- Do not merge or push this feature as part of the spec work.

---

## Primary Users

| User | Job |
|---|---|
| Spectator | See who is currently strongest and understand the ranking at a glance. |
| Bot operator | See how their agent compares and whether it is ranked or provisional. |
| Admin / owner | Compare ranking formulas and judge whether First-place Bonus Elo is worth using. |

The primary user for the page is the **spectator**. If the spectator and admin needs conflict, the spectator view stays simple and the admin analysis details move lower on the page or into secondary text.

---

## Leaderboard Controls

The page should have two rating controls and one game-section filter.

### Rating Mode Toggle

Two options:

| Option | Meaning |
|---|---|
| Standard | Normal Multiplayer Elo from final match placement. |
| First-place Bonus | Same as Standard, except first-place pairwise wins receive extra weight. |

Default:

```text
Standard
```

Recommended microcopy:

```text
Rating
[Standard] [First-place bonus]
```

Helper text:

```text
Standard ranks every finish position. First-place bonus gives match winners extra credit.
```

### Included Competitors Segmented Control

Three options:

| Option | Meaning |
|---|---|
| Agents | Human-submitted agents only. |
| Sims | Platform-provided Sims only. |
| Agents + Sims | Both groups in the same ranking table. |

Default:

```text
Agents
```

Recommended microcopy:

```text
Included
[Agents] [Sims] [Agents + Sims]
```

Sims must always be visually labeled as Sims in the table. Agents do not need a label unless they are provisional.

### Sim Game Filter

The page should also let the user hide entire game sections that contain Sims.

| Option | Meaning |
|---|---|
| Show all | Show every game section, even if some competitors are Sims. |
| Hide sim games | Remove any game section that includes one or more Sims from the visible leaderboard. |

Default:

```text
Show all
```

Recommended microcopy:

```text
Sim games
[Show all] [Hide sim games]
```

---

## Rating Model

### Standard Multiplayer Elo

For each rated match, final placement is converted into pairwise comparisons.

Example with four competitors:

```text
1st beats 2nd, 3rd, 4th
2nd beats 3rd, 4th
3rd beats 4th
```

Ties produce a draw for that pair.

Per competitor, calculate all pairwise Elo changes from the match, then average them:

```text
match_rating_delta = average(pairwise_elo_deltas)
```

This keeps large matches from giving huge rating movement just because there are more opponents.

### First-place Bonus Elo

First-place Bonus Elo uses the same pairwise comparisons as Standard Elo, with one change:

```text
When the match winner beats another competitor, that pairwise comparison has extra weight.
```

Initial default:

```text
first_place_weight = 1.2
```

All other pairwise comparisons use:

```text
weight = 1.0
```

The bonus must be symmetric. If first place gains extra rating from a weighted pairwise result, the lower finisher loses the matching weighted amount. This prevents rating inflation.

If multiple competitors tie for first place, they do not get bonus-weighted wins against each other. Each tied first-place competitor receives bonus-weighted pairwise wins only against competitors below the tied first group.

### Match Placement Source

Use the existing match result order:

1. Match winner / placement by round-wins.
2. Total match score as the tiebreaker.
3. Deterministic tie handling if round-wins and total score are equal.

If a match result is truly tied after all tiebreakers, Elo treats that pair as a draw.

---

## Competitor Types

### Agents

Agents are human-submitted competitors. Ratings should belong to the agent identity used in matches.

Future recommendation:

```text
Ratings should eventually belong to AgentVersion, not just Agent.
```

That is not required for the first leaderboard if versioning does not exist yet, but the data model should avoid making that migration painful.

### Sims

Sims are platform-provided deterministic competitors.

For this feature, Sims should be included in rating views but clearly separated from Agents.

Important rules:

- Sims may appear on the Sims and Agents + Sims leaderboard views.
- Sims must be labeled as `Sim`.
- Sims should not be mistaken for human-submitted agents.
- Sim ratings may update normally in this first implementation unless we later choose fixed benchmark ratings.

Design note:

Earlier discussion considered fixed-rating Sims as benchmark anchors. For this leaderboard page, showing a Sims-only ranking is useful for tuning and transparency. If we later decide Sims should not drift, we can freeze their ratings while keeping the same page filters.

---

## Eligibility And Anti-Grind Rules

Elo is not cumulative, so playing more does not directly add points. Still, the page needs eligibility rules so repeated low-quality games do not dominate rankings.

### Rating Exists

A competitor gets a rating after its first rated match.

Before any rated match:

```text
No rating
```

Initial rating:

```text
1500
```

### Provisional Status

A competitor is provisional until it has enough evidence.

Recommended default:

```text
Provisional if fewer than 5 rated matches
```

Provisional competitors can appear in the table, but must be labeled:

```text
Provisional
```

The default leaderboard sort should still sort them by rating, but the label makes the uncertainty clear.

### Ranked Eligibility

A competitor is fully ranked when it meets all of these:

```text
5+ rated matches
10+ unique real-agent opponents
```

For Sims-only views, the `10+ unique real-agent opponents` requirement does not apply. Sims should instead use:

```text
5+ rated matches
```

For Agents + Sims views, show both groups in one table, but eligibility labels should be based on each competitor's type.

### Activity

The first version does not need a separate season, but the leaderboard should show basic activity:

| Field | Meaning |
|---|---|
| Matches | Rated matches played. |
| Last played | Most recent rated match date. |
| Status | Ranked, Provisional, or Inactive. |

Recommended inactive rule:

```text
Inactive if no rated match in the last 60 days.
```

Inactive competitors can remain visible, but should be labeled. This avoids old ratings looking fresher than they are.

---

## Rated Match Rules

A match only affects leaderboard ratings if it is marked rated before it starts.

| Match Type | Default Rating Behavior |
|---|---|
| Ranked public match | Rated |
| Practice match | Unrated |
| Admin test match | Unrated unless explicitly marked rated |
| Sim-only match | Rated for Sims leaderboard only |
| Mixed Agent + Sim match | Rated |

Hard rule:

```text
A match cannot become rated after it finishes.
```

This prevents cherry-picking good results.

---

## User Stories

### User Story 1 — View standard Agent rankings (Priority: P1)

As a spectator, I want to open the leaderboard and see human-submitted agents ranked by Standard Elo so I can quickly understand who is strongest.

**Independent test:** Visit the leaderboard page with no query params. The page shows Standard Elo, Agents included, and a ranked table.

**Acceptance scenarios:**

1. **Given** rated agent matches exist, **When** a user visits the leaderboard page, **Then** Standard Elo + Agents is selected by default.
2. **Given** the default view, **When** the table renders, **Then** competitors are sorted by Standard Elo descending.
3. **Given** a competitor has fewer than 5 rated matches, **When** it appears, **Then** it has a `Provisional` label.
4. **Given** a competitor has not played in 60+ days, **When** it appears, **Then** it has an `Inactive` label.

### User Story 2 — Compare Standard vs First-place Bonus Elo (Priority: P1)

As Chris, I want to toggle between Standard Elo and First-place Bonus Elo so I can see how much the first-place bonus changes the leaderboard.

**Independent test:** Toggle from Standard to First-place Bonus. The table updates to use the bonus rating values without changing the included competitor filter.

**Acceptance scenarios:**

1. **Given** the leaderboard is on Standard Elo, **When** the user selects First-place Bonus, **Then** the same competitor filter remains selected and rankings update using First-place Bonus Elo.
2. **Given** the leaderboard is on First-place Bonus, **When** the user selects Standard, **Then** rankings update using Standard Elo.
3. **Given** a competitor has different ratings under each mode, **When** the user toggles, **Then** the displayed rating changes.
4. **Given** the user changes rating mode, **When** the page URL updates, **Then** the selected mode is reflected in query params or another shareable state.

### User Story 3 — Filter between Agents, Sims, and Agents + Sims (Priority: P1)

As a viewer, I want to switch the included competitor group so I can inspect human agents separately from platform Sims or compare them together.

**Independent test:** Select each included filter and confirm the table shows only the intended competitor types.

**Acceptance scenarios:**

1. **Given** Agents is selected, **When** the table renders, **Then** only human-submitted agents appear.
2. **Given** Sims is selected, **When** the table renders, **Then** only Sims appear and all rows have a `Sim` label.
3. **Given** Agents + Sims is selected, **When** the table renders, **Then** both groups appear in one sorted list.
4. **Given** Agents + Sims is selected, **When** Sim rows appear, **Then** they remain clearly labeled as `Sim`.

### User Story 4 — Understand how a rating was earned (Priority: P2)

As a bot operator, I want enough context beside the rating to know whether a competitor's rank is reliable.

**Independent test:** Each row shows rating, matches, unique real-agent opponents where applicable, last played, and status.

**Acceptance scenarios:**

1. **Given** any leaderboard row, **When** it renders, **Then** it shows rating, matches, last played, and status.
2. **Given** an Agent row, **When** it renders, **Then** it shows unique real-agent opponents or enough match context to support eligibility.
3. **Given** a competitor is provisional, **When** the row renders, **Then** the status explains the minimum requirement in plain language.
4. **Given** there are no rated matches for a selected view, **When** the table renders, **Then** the page shows an empty state explaining how ratings are created.

### User Story 5 — Keep rating updates fair across match sizes (Priority: P2)

As the owner, I want rating updates to be normalized per match so large matches do not create huge rating swings compared with small matches.

**Independent test:** A 20-player match does not produce rating deltas roughly 19 times larger than a 2-player comparison would.

**Acceptance scenarios:**

1. **Given** a match has many competitors, **When** Elo updates are calculated, **Then** each competitor's match delta is based on the average of pairwise deltas.
2. **Given** a match has tied placements, **When** Elo updates are calculated, **Then** tied competitors produce draw comparisons with each other.
3. **Given** a match winner receives the first-place bonus, **When** weighted pairwise deltas are calculated, **Then** the weighted deltas remain balanced between winner gains and loser losses.

---

## Functional Requirements

- **FR-001**: A public leaderboard route MUST exist.
- **FR-002**: The default leaderboard view MUST be Standard Elo with Agents included.
- **FR-003**: The leaderboard MUST provide a two-option rating mode control: Standard and First-place Bonus.
- **FR-004**: The leaderboard MUST provide a three-option included competitors control: Agents, Sims, Agents + Sims.
- **FR-005**: The selected rating mode and included competitors filter MUST be reflected in shareable page state, such as query params.
- **FR-006**: The system MUST store or compute Standard Elo ratings for rated competitors.
- **FR-007**: The system MUST store or compute First-place Bonus Elo ratings for rated competitors from the same rated match history.
- **FR-008**: Standard Elo MUST use pairwise comparisons based on final match placement.
- **FR-009**: First-place Bonus Elo MUST use the same pairwise comparisons as Standard Elo, with a default first-place win weight of `1.2`.
- **FR-010**: Per-match Elo deltas MUST be normalized by averaging each competitor's pairwise deltas for that match.
- **FR-011**: The leaderboard table MUST sort by the selected rating value descending.
- **FR-012**: Sims MUST be visually labeled on all leaderboard views where they appear.
- **FR-013**: Competitors with fewer than 5 rated matches MUST be labeled `Provisional`.
- **FR-014**: Competitors with no rated match in the last 60 days MUST be labeled `Inactive`.
- **FR-015**: Agent competitors MUST show or support the `10+ unique real-agent opponents` full-ranked eligibility rule.
- **FR-016**: A match MUST only affect ratings if it was marked rated before the match started.
- **FR-017**: Practice matches and admin test matches MUST default to unrated.
- **FR-018**: Empty leaderboard states MUST explain why no rows are visible for the selected view.
- **FR-019**: The page MUST work at phone width without horizontal scrolling.
- **FR-020**: The page MUST not reveal private strategy prompts.

---

## Suggested Route And Query Params

Suggested route:

```text
/leaderboard
```

Suggested query params:

```text
/leaderboard?rating=standard&included=agents
/leaderboard?rating=bonus&included=sims
/leaderboard?rating=standard&included=all
```

Allowed values:

| Param | Values |
|---|---|
| `rating` | `standard`, `bonus` |
| `included` | `agents`, `sims`, `all` |

Invalid params should fall back to the default:

```text
rating=standard
included=agents
```

---

## Suggested Table Columns

| Column | Notes |
|---|---|
| Rank | Position within the selected view. |
| Competitor | Agent or Sim display name. |
| Type | Agent or Sim. Can be a badge instead of a full column on mobile. |
| Rating | Selected Elo rating rounded to nearest whole number. |
| Matches | Rated match count. |
| Unique opponents | Required for Agent eligibility; can be hidden on mobile. |
| Last played | Relative date or exact date. |
| Status | Ranked, Provisional, Inactive. |

Mobile recommendation:

```text
Rank + competitor + rating stay visible.
Matches/status collapse into secondary text.
```

---

## Empty, Loading, And Error States

### Empty State

For no rows in selected view:

```text
No ranked competitors yet.
Ratings appear after rated matches finish.
```

For Sims view with no rated Sim matches:

```text
No Sim ratings yet.
Run a rated match with Sims to start this board.
```

### Loading State

```text
Loading leaderboard...
```

### Error State

```text
Could not load the leaderboard.
Refresh the page or try again later.
```

Do not hide backend errors in logs. Fix root causes during implementation.

---

## Data Model Notes

Exact schema can be finalized during planning, but the implementation needs to represent:

### Competitor Rating

Fields:

| Field | Meaning |
|---|---|
| competitor_id | Agent or Sim ID. |
| competitor_type | `agent` or `sim`. |
| standard_elo | Current Standard Elo. |
| bonus_elo | Current First-place Bonus Elo. |
| rated_match_count | Number of rated matches. |
| unique_real_agent_opponents | Count of unique human-submitted agent opponents. |
| last_rated_match_at | Most recent rated match timestamp. |

### Rating Event

Fields:

| Field | Meaning |
|---|---|
| match_id | Rated match that caused the update. |
| competitor_id | Updated competitor. |
| rating_mode | `standard` or `bonus`. |
| rating_before | Rating before update. |
| rating_after | Rating after update. |
| delta | Rating change. |
| placement | Final placement in the match. |

Rating events are useful for audits and debugging. If storage is a concern, they can be deferred, but the system should at least be able to recompute ratings from match history.

### Match Rating Flag

Matches need a field or equivalent source of truth:

```text
is_rated: bool
```

This value must be fixed before the match starts.

---

## Success Criteria

- **SC-001**: Visiting `/leaderboard` shows Standard Elo + Agents by default.
- **SC-002**: Users can switch between Standard Elo and First-place Bonus Elo.
- **SC-003**: Users can switch between Agents, Sims, and Agents + Sims.
- **SC-004**: Sims are always clearly labeled when visible.
- **SC-005**: Standard and First-place Bonus ratings are both updated from the same rated match history.
- **SC-006**: Large matches do not create outsized Elo movement compared with small matches because per-match deltas are averaged.
- **SC-007**: Competitors with fewer than 5 rated matches are labeled Provisional.
- **SC-008**: Agent competitors do not count as fully ranked until they have faced at least 10 unique real-agent opponents.
- **SC-009**: The page works on mobile without horizontal scrolling.

---

## Open Questions

1. Should Sims drift normally in Elo, or should they become fixed benchmark anchors after an initial calibration period?
2. Should provisional competitors appear in the main sorted table, or in a separate "Provisional" section below ranked competitors?
3. Should inactive competitors stay in the default view with an `Inactive` badge, or move behind a "show inactive" control?
4. Should ratings be recomputed from match history on demand during early development, then stored once the rules feel stable?

---

## Recommendation

Ship the first version with:

```text
Default view: Standard Elo + Agents
Bonus mode: visible toggle, treated as experimental
Sims: visible through filters, always labeled
Eligibility: 5 rated matches + 10 unique real-agent opponents for Agents
Delta handling: average pairwise Elo deltas per match
```

This gives us a fair MVP, keeps the page simple, and lets us compare the first-place bonus without committing the whole product to it too early.

---

## Constitution Check

PASS. The spec uses plain labels, separates Agents from Sims, keeps private strategy prompts hidden, avoids seasons, and does not reward raw play volume. Implementation must follow the Preflight Gate before any push or PR.
