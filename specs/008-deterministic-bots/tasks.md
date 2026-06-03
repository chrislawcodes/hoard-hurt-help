# Sims Tasks

**Status:** in progress
**Created:** 2026-06-03

## Build Order

| Step | Status | Task | Why |
|---|---|---|---|
| 1 | Done | Add Sim fields to `bots` and cap Hoard-Hurt-Help at 20 players | The schema and defaults need to exist before the rest of the feature can land |
| 2 | Done | Add the migration for the new bot fields and cap default | Keeps SQLite/Postgres schema creation aligned |
| 3 | Done | Add the pure Sim engine modules | Lets us test deterministic behavior without scheduler noise |
| 4 | Done | Wire scheduler talk/action hooks for Sim bots | Makes Sims actually play turns |
| 5 | Done | Add the normal bot/game flow for selecting Sim packs | Lets hosts create Sims without a separate admin screen |
| 6 | Done | Add tests for determinism, cap enforcement, and fallback behavior | Locks in the mechanics |

## Immediate Subtasks

- [x] Update the `Bot` model with Sim metadata fields
- [x] Update the `Game` model default for the 20-player cap
- [x] Update the Hoard-Hurt-Help game module config default
- [x] Update admin validation and form defaults to 20
- [x] Add the migration for the Sim bot fields and default cap
- [x] Add tests for the new defaults and validation
- [x] Add the pure Sim engine modules
- [x] Wire scheduler talk/action hooks for Sim bots
- [x] Add an integration test for Sim auto-play in the scheduler loop
- [x] Add a normal bot creation path for Sim bots
- [x] Add a grouped Sim pack picker to the bot create form
