"""Match viewer and live fragment web routes."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.deps import DbSession, get_current_user
from app.games import get as get_game_module
from app.games.base import GameError
from app.models.match import Match
from app.models.player import Player
from app.read_models.matches import load_resolved_turn_rows
from app.routes.web_support import _game_theme, _is_admin, _match_url, _redirect_to_match
from app.templating import templates

router = APIRouter(tags=["web"])

def _move_effect_for(game_type: str, action: str) -> tuple[int, int | None]:
    """Nominal per-move effect for the watch feed, split into (actor_delta, target_delta).

    Delegates to the game module so the viewer carries no game-specific scoring.
    This is what the move is *worth* by that game's rules (e.g. PD: HOARD +2 to
    self, HELP +4 to the target, HURT -4 to the target) — shown per-move so
    viewers see who each move lands on. It is deliberately NOT the player's net
    change for the turn (which folds in others' moves, bonuses, and the floor);
    the running scoreboard reflects those actual totals. An unknown game type
    falls back to no displayed delta rather than crashing the viewer.
    """
    try:
        return get_game_module(game_type).move_effect(action)
    except GameError:
        return 0, None


def _feed_sort_key(a: dict) -> tuple[int, int, str]:
    """Highlights-first ordering for one turn's actions in the feed.

    Tiers, top first: betrayals, mutual pacts, then the rest grouped by action
    type (hurt, help, hoard), with missed/defaulted turns last. Within a tier
    the biggest score swing comes first; ties break by agent id so the order is
    stable and testable.
    """
    if a.get("betrayal"):
        tier = 0
    elif a.get("mutual"):
        tier = 1
    elif a.get("was_defaulted"):
        tier = 5
    elif a["action"] == "HURT":
        tier = 2
    elif a["action"] == "HELP":
        tier = 3
    else:  # HOARD
        tier = 4
    delta = a.get("display_delta") or 0
    return (tier, -abs(delta), a["agent_id"])


def _turn_summary(actions: list[dict]) -> dict[str, int]:
    """Per-turn action counts for the feed's at-a-glance summary line.

    `mutual` is a subset of `help` and `betrayal` a subset of `hurt`; the
    template shows help/hurt/hoard as the base counts and surfaces betrayal /
    mutual as extra markers when present.
    """
    counts = {"help": 0, "hurt": 0, "hoard": 0, "betrayal": 0, "mutual": 0, "missed": 0}
    for a in actions:
        act = a["action"].lower()
        if act in ("help", "hurt", "hoard"):
            counts[act] += 1
        if a.get("betrayal"):
            counts["betrayal"] += 1
        if a.get("mutual"):
            counts["mutual"] += 1
        if a.get("was_defaulted"):
            counts["missed"] += 1
    return counts


def _turn_groups(actions: list[dict]) -> list[dict]:
    """Group a turn's actions by type for the Compact view.

    The repetitive moves — above all the hoards, where the `+2` is identical for
    every bot — collapse to one line per type with the delta stated once. Order
    leads with conflict (hurts, betrayals first) and ends with the quiet hoard
    list. Returns only non-empty groups.
    """
    hurts: list[dict] = []
    helps: list[dict] = []
    hoards: list[dict] = []
    pacts: list[dict] = []
    seen_pacts: set[frozenset[str]] = set()
    for a in actions:
        if a.get("mutual") and a["target_id"]:
            pair = frozenset((a["agent_id"], a["target_id"]))
            if pair not in seen_pacts:
                seen_pacts.add(pair)
                x, y = sorted(pair)
                pacts.append({"a": x, "b": y})
        elif a["action"] == "HURT" and a["target_id"]:
            hurts.append({"a": a["agent_id"], "b": a["target_id"], "betrayal": bool(a.get("betrayal"))})
        elif a["action"] == "HELP" and a["target_id"]:
            helps.append({"a": a["agent_id"], "b": a["target_id"]})
        else:  # HOARD, including a defaulted/missed turn
            hoards.append({"a": a["agent_id"]})

    hurts.sort(key=lambda h: (not h["betrayal"], h["a"]))

    groups: list[dict] = []
    if hurts:
        groups.append({"kind": "hurt", "delta": "-4", "members": hurts})
    if pacts:
        groups.append({"kind": "pact", "delta": "+8", "members": pacts})
    if helps:
        groups.append({"kind": "help", "delta": "+4", "members": helps})
    if hoards:
        groups.append({"kind": "hoard", "delta": "+2", "members": hoards})
    return groups


# Phrase banks for the deterministic play-by-play headline. Variety comes from
# rotating through each bank by a stable index (turn ordinal + beat position),
# so the text differs turn to turn but is fully reproducible — no LLM, no
# randomness. Add entries to a bank to widen variety without touching logic.
_HEADLINE_PHRASES: dict[str, tuple[str, ...]] = {
    "betray": (
        "{a} turns on former ally {b}",
        "{a} breaks faith with {b}",
        "{a} stabs {b} in the back",
        "{a} abandons the pact with {b}",
    ),
    "pact": (
        "{a} and {b} lock in a pact (+8 each)",
        "{a} and {b} shake hands — +8 apiece",
        "a fresh alliance forms: {a} and {b} (+8 each)",
    ),
    "gangup": (
        "{n} bots pile on {t}",
        "the table turns on {t} — {n} strikes land",
        "{n} bots gang up on {t}",
    ),
    "revenge": (
        "{n} bots round on {t} — payback for the betrayal",
        "{t} pays for the betrayal as {n} pile in",
    ),
    "lead": (
        "that hands {a} the lead",
        "{a} seizes first place",
        "that vaults {a} to the top",
    ),
    "swing": (
        "{a} clobbers {b} ({d})",
        "{a}'s strike sends {b} reeling ({d})",
    ),
    "residual": (
        "the other {n} just hoard",
        "{n} more keep their heads down",
        "the remaining {n} bank quietly",
    ),
    "quiet": (
        "a quiet turn — most of the table just hoards",
        "a calm turn; almost everyone banks a coin",
    ),
}

_NUM_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
)


def _num_word(n: int) -> str:
    return _NUM_WORDS[n] if 0 <= n < len(_NUM_WORDS) else str(n)


def _mutual_pairs(actions: list[dict]) -> set[frozenset[str]]:
    return {
        frozenset((a["agent_id"], a["target_id"]))
        for a in actions
        if a.get("mutual") and a["target_id"]
    }


def _turn_headline(
    actions: list[dict],
    prev_actions: list[dict],
    leader: str | None,
    prev_leader: str | None,
    ordinal: int,
) -> str:
    """A deterministic one-line play-by-play for a turn.

    Pure function of its inputs — the same turn always produces the same text,
    so it is replay-stable and unit-testable. It ranks "beats" (betrayal, lead
    change, new pact, gang-up/revenge, big swing), narrates the top one or two,
    and adds a residual clause for the quiet majority.
    """
    def phrase(kind: str, idx: int) -> str:
        bank = _HEADLINE_PHRASES[kind]
        return bank[idx % len(bank)]

    beats: list[tuple[int, dict]] = []

    for a in actions:
        if a.get("betrayal"):
            beats.append(
                (100 + abs(a.get("display_delta") or 0),
                 {"kind": "betray", "a": a["agent_id"], "b": a["target_id"]})
            )

    prev_pairs = _mutual_pairs(prev_actions)
    for pair in _mutual_pairs(actions):
        if pair not in prev_pairs:
            x, y = sorted(pair)
            beats.append((70, {"kind": "pact", "a": x, "b": y}))

    hits: dict[str, list[str]] = {}
    for a in actions:
        if a["action"] == "HURT" and a["target_id"]:
            hits.setdefault(a["target_id"], []).append(a["agent_id"])
    prev_betrayers = {a["agent_id"] for a in prev_actions if a.get("betrayal")}
    for target, hitters in hits.items():
        if len(hitters) >= 2:
            kind = "revenge" if target in prev_betrayers else "gangup"
            beats.append((75 + len(hitters), {"kind": kind, "t": target, "n": len(hitters)}))

    # A "swing" is a notable strike — only HURTs qualify (the verbs are violent);
    # a big HELP is cooperation, surfaced via the pact beat, not a swing.
    swing = max(
        (a for a in actions
         if a["action"] == "HURT" and a["target_id"] and not a.get("betrayal")),
        key=lambda a: abs(a.get("display_delta") or 0),
        default=None,
    )
    if swing is not None and abs(swing.get("display_delta") or 0) >= 4:
        d = swing.get("display_delta") or 0
        beats.append(
            (60, {"kind": "swing", "a": swing["agent_id"], "b": swing["target_id"], "d": str(d)})
        )

    if leader and prev_leader and leader != prev_leader:
        beats.append((90, {"kind": "lead", "a": leader}))

    beats.sort(key=lambda b: -b[0])

    used: set[str] = set()
    chosen: list[dict] = []
    lead_beat: dict | None = None
    for _prio, b in beats:
        if b["kind"] == "lead":
            lead_beat = lead_beat or b
            continue
        actors = [b[k] for k in ("a", "b", "t") if b.get(k)]
        if any(x in used for x in actors):
            continue
        used.update(actors)
        chosen.append(b)
        if len(chosen) == 2:
            break

    def render(kind: str, idx: int, b: dict) -> str:
        s = phrase(kind, idx).format(
            a=b.get("a"), b=b.get("b"), t=b.get("t"), n=_num_word(b.get("n", 0)), d=b.get("d", ""),
        )
        return s[0].upper() + s[1:]

    sentences: list[str] = []
    for i, b in enumerate(chosen):
        s = render(b["kind"], ordinal + i, b)
        # Fold a lead change onto the top beat as a clause when a different bot took over.
        if i == 0 and lead_beat is not None and lead_beat["a"] != b.get("a"):
            s += " — " + phrase("lead", ordinal).format(a=lead_beat["a"])
            lead_beat = None
        sentences.append(s + ".")
    if lead_beat is not None:
        sentences.append(render("lead", ordinal, lead_beat) + ".")

    if not sentences:
        return render("quiet", ordinal, {}) + "."

    hoards = sum(1 for a in actions if a["action"] == "HOARD")
    if hoards >= len(actions) / 2:
        sentences.append(render("residual", ordinal, {"n": hoards}) + ".")
    return " ".join(sentences)


def _build_rc_data(scoreboard: list[dict], history: list[dict]) -> str:
    """Serialize game history as the robot-circle viewer JSON format."""
    agents = [r["agent_id"] for r in scoreboard]

    turns = []
    for h in history:
        rc_actions = []
        for a in h["actions"]:
            rc_actions.append({
                "agent": a["agent_id"],
                "action": a["action"],
                "target": a["target_id"],
                "delta": a["display_delta"],
                "mutual": a["mutual"],
                "betrayal": a["betrayal"],
                "missed": a["was_defaulted"],
                "msg": (a.get("message") or "").strip(),
            })

        spot: set[str] = set()
        for a in rc_actions:
            spot.add(a["agent"])
            if a["target"]:
                spot.add(a["target"])

        betrayals = [a for a in rc_actions if a["betrayal"]]
        mutuals   = [a for a in rc_actions if a["mutual"]]
        hurts     = [a for a in rc_actions if a["action"] == "HURT" and a["target"]]
        helps     = [a for a in rc_actions if a["action"] == "HELP" and not a["mutual"] and a["target"]]
        missed    = [a for a in rc_actions if a["missed"]]

        if betrayals:
            b = betrayals[0]
            badge, cap = "Betrayal", f"{b['agent']} turns on former ally {b['target']}."
        elif mutuals:
            pair = sorted({a["agent"] for a in mutuals} | {a["target"] for a in mutuals})
            if len(pair) == 2:
                badge, cap = "The Pact", f"{pair[0]} and {pair[1]} lock in a mutual pact — +8 each."
            else:
                badge, cap = "The Pact", "Mutual pacts lock in — +8 each."
        elif hurts:
            h0 = hurts[0]
            badge, cap = "Strike", f"{h0['agent']} strikes {h0['target']}."
        elif helps:
            badge = "Help"
            cap = (f"{helps[0]['agent']} helps {helps[0]['target']}." if len(helps) == 1
                   else "Gifts change hands — one-way help around the circle.")
        elif missed and len(missed) == len(rc_actions):
            badge, cap = "No-show", f"{missed[0]['agent']} missed its turn — defaulted to Hoard."
        else:
            badge, cap = "Hoard", "A quiet turn — everyone banks a coin."

        talk = [
            {"agent": m["agent_id"], "text": m["text"].strip()}
            for m in h["messages"]
            if m["text"].strip()
        ]

        turns.append({
            "round": h["round"],
            "turn": h["turn"],
            "badge": badge,
            "cap": cap,
            "spotlight": sorted(spot),
            "actions": rc_actions,
            "talk": talk,
        })

    return json.dumps({
        "agents": agents,
        "turns": turns,
        "max_round": max((t["round"] for t in turns), default=0),
        "sample": False,
    }, ensure_ascii=False)


async def _game_view_context(request: Request, db, match_id: str) -> dict:
    """Build the shared context for the game viewer page and its live fragment."""
    user = await get_current_user(request, db)
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    turn_rows = await load_resolved_turn_rows(db, match_id)
    players = turn_rows.players
    players_by_id = turn_rows.players_by_id

    scoreboard: list[dict[str, Any]] = sorted(
        (
            {
                "agent_id": p.agent_id,
                "round_score": p.current_round_score,
                "round_wins": p.total_round_wins,
            }
            for p in players
        ),
        key=lambda r: (-r["round_wins"], -r["round_score"]),
    )
    for i, row in enumerate(scoreboard, start=1):
        row["rank"] = i

    turns = turn_rows.turns
    history: list[dict[str, Any]] = []
    messages_by_turn = turn_rows.messages_by_turn
    subs_by_turn = turn_rows.submissions_by_turn

    # Per-turn pact/betrayal signals for the replay. A "pact" is a mutual HELP in
    # the same turn; a "betrayal" is a HURT aimed at last turn's pact partner.
    prev_mutual: set[frozenset[str]] = set()
    # Carried across turns to narrate a deterministic play-by-play headline.
    prev_actions: list[dict[str, Any]] = []
    prev_leader: str | None = None
    inround: dict[str, int] = {}
    inround_round: int | None = None
    for seq, t in enumerate(turns, start=1):
        subs = subs_by_turn.get(t.id, [])
        turn_messages = messages_by_turn.get(t.id, [])
        if turn_messages:
            messages: list[dict[str, Any]] = [
                {
                    "agent_id": players_by_id[msg.player_id].agent_id,
                    "text": msg.text,
                    "thinking": msg.thinking,
                    "was_defaulted": msg.was_defaulted,
                }
                for msg in turn_messages
                if msg.player_id in players_by_id
            ]
        else:
            messages = [
                {
                    "agent_id": players_by_id[s.player_id].agent_id,
                    "text": s.message,
                    "thinking": "",
                    "was_defaulted": s.was_defaulted,
                }
                for s in subs
                if s.player_id in players_by_id
            ]
        actions: list[dict[str, Any]] = []
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if not actor:
                continue
            actor_delta, target_delta = _move_effect_for(g.game, s.action)
            actions.append(
                {
                    "agent_id": actor.agent_id,
                    "action": s.action,
                    "target_id": target.agent_id if target else None,
                    # Nominal per-move effect, attributed to who it lands on.
                    "actor_delta": actor_delta,
                    "target_delta": target_delta,
                    "thinking": s.thinking,
                    "was_defaulted": s.was_defaulted,
                    "mutual": False,
                    "betrayal": False,
                }
            )

        # Tag this turn's pacts (mutual HELP) and betrayals (HURT on last turn's
        # pact partner), so the feed can mark them without re-deriving in JS.
        helps = {
            a["agent_id"]: a["target_id"]
            for a in actions
            if a["action"] == "HELP" and a["target_id"]
        }
        this_mutual: set[frozenset[str]] = set()
        for a in actions:
            tgt = a["target_id"]
            if not tgt:
                continue
            pair = frozenset((a["agent_id"], tgt))
            if a["action"] == "HELP" and helps.get(tgt) == a["agent_id"]:
                a["mutual"] = True
                this_mutual.add(pair)
            elif a["action"] == "HURT" and pair in prev_mutual:
                a["betrayal"] = True
        prev_mutual = this_mutual

        messages_by_agent = {m["agent_id"]: m for m in messages}
        for a in actions:
            paired_message = messages_by_agent.get(a["agent_id"])
            if paired_message is not None:
                a["message"] = paired_message["text"]
                a["message_thinking"] = paired_message["thinking"]
                a["message_was_defaulted"] = paired_message["was_defaulted"]
            else:
                a["message"] = ""
                a["message_thinking"] = ""
                a["message_was_defaulted"] = True

            if a["action"] == "HOARD":
                a["display_action"] = "Hoard"
                a["display_delta"] = a["actor_delta"]
            elif a["action"] == "HELP":
                a["display_action"] = "Help"
                a["display_delta"] = 8 if a["mutual"] else a["target_delta"]
            else:
                a["display_action"] = "HURT"
                a["display_delta"] = a["target_delta"]

        # Running in-round score (resets each round) → who leads, for the
        # play-by-play "lead change" beat.
        if t.round != inround_round:
            inround_round = t.round
            inround = {p.agent_id: 0 for p in players}
        for a in actions:
            if a["action"] == "HOARD":
                inround[a["agent_id"]] = inround.get(a["agent_id"], 0) + 2
            elif a["action"] == "HELP" and a["mutual"]:
                inround[a["agent_id"]] = inround.get(a["agent_id"], 0) + 8
            elif a["action"] == "HELP" and a["target_id"]:
                inround[a["target_id"]] = inround.get(a["target_id"], 0) + 4
            elif a["action"] == "HURT" and a["target_id"]:
                inround[a["target_id"]] = max(0, inround.get(a["target_id"], 0) - 4)
        # Highest score, ties broken alphabetically — deterministic.
        leader = min(inround, key=lambda k: (-inround[k], k)) if inround else None
        headline = _turn_headline(actions, prev_actions, leader, prev_leader, seq)
        prev_leader = leader
        prev_actions = actions

        history.append(
            {
                "seq": seq,
                "round": t.round,
                "turn": t.turn,
                "messages": messages,
                "actions": actions,
                # `actions` stays in submission order for the animation; the feed
                # renders `feed_actions` (highlights first) and `summary` (counts).
                "feed_actions": sorted(actions, key=_feed_sort_key),
                "summary": _turn_summary(actions),
                "groups": _turn_groups(actions),
                "headline": headline,
            }
        )

    # Keep the server data in chronological order. The template reverses it for
    # the newest-first feed while round navigation can still reason about order.
    rounds: list[dict] = []
    for h in history:
        if not rounds or rounds[-1]["round"] != h["round"]:
            rounds.append({"round": h["round"], "turns": []})
        rounds[-1]["turns"].append(h)
    max_played_round = rounds[-1]["round"] if rounds else 0

    winner_agent_id = None
    if g.winner_player_id:
        winner = (
            await db.execute(select(Player).where(Player.id == g.winner_player_id))
        ).scalar_one_or_none()
        winner_agent_id = winner.agent_id if winner else None

    return {
        "user": user,
        "is_admin": _is_admin(user),
        "game": g,
        "game_theme": _game_theme(g),
        "scoreboard": scoreboard,
        "history": history,
        "rounds": rounds,
        "max_played_round": max_played_round,
        "winner_agent_id": winner_agent_id,
    }


@router.get("/games/{game}/matches/{match_id}", response_class=HTMLResponse)
async def game_viewer(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    ctx = await _game_view_context(request, db, match_id)
    if ctx["game"].game != game:
        return RedirectResponse(
            url=_match_url(ctx["game"]), status_code=status.HTTP_301_MOVED_PERMANENTLY
        )
    ctx["rc_data"] = _build_rc_data(ctx["scoreboard"], ctx["history"])
    return templates.TemplateResponse(request, "game.html", ctx)


@router.get("/games/{game}/matches/{match_id}/live", response_class=HTMLResponse)
async def game_live_fragment(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    request: Request,
    db: DbSession,
):
    """Server-rendered live region. SSE events trigger the page to re-fetch this."""
    ctx = await _game_view_context(request, db, match_id)
    if ctx["game"].game != game:
        return RedirectResponse(
            url=_match_url(ctx["game"], "/live"),
            status_code=status.HTTP_301_MOVED_PERMANENTLY,
        )
    return templates.TemplateResponse(request, "fragments/live_region.html", ctx)


@router.get("/games/{match_id}/live", include_in_schema=False)
async def legacy_game_live_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/live")
