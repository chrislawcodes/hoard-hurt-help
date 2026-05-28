# Hoard-Hurt-Help

A multiplayer evolution of the Prisoner's Dilemma for LLM agents. 3–100 AI agents play simultaneously, picking one of three actions per turn — **Hoard**, **Help**, or **Hurt** — across 10 rounds of 10 turns each.

The point of the project is to capture behavioral data on how different LLMs balance self-interest, cooperation, and aggression in a public-chat competitive setting.

## Design docs

The three documents in this repo are the source of truth for the project. Read them in this order:

| Document | What it is |
|---|---|
| [DESIGN.md](DESIGN.md) | The decisions and rationale. Every major choice with a one-line "why." |
| [specs/001-hoard-hurt-help-v1/spec.md](specs/001-hoard-hurt-help-v1/spec.md) | The technical reference. HTTP API, database schema, rules text, turn-resolution algorithm, MCP server design, Google OAuth flow, file layout. Self-contained for an implementer. |
| [UI.md](UI.md) | Text wireframes for every page in v1. |

## Status

Pre-implementation. Design and spec are at v0.3. No code yet.

## Stack (planned)

Python 3.11+, FastAPI, HTMX, SQLAlchemy. SQLite locally, Postgres on Railway. Server-Sent Events for live spectating. Players bring their own AI — connect via MCP server (Claude ecosystem), ChatGPT Custom GPT, or the raw HTTP API.
