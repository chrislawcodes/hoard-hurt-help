"""Mutual-help decay switch (feature decay-switch).

ON keeps the sliding +8/+7/…→+2 per-pair decay; OFF pays a flat +8 to each side
on every mutual help — no decay, no floor. Every test that exercises the OFF path
farms the pair PAST the first mutual help (k≥1), where ON diverges from 8, so an
accidentally-ON match cannot make an OFF test pass by luck.

Note ON is no longer what a new match gets — the default is
DEFAULT_MUTUAL_HELP_MODE, which is flat_6. Tests here name the mode they mean
rather than leaning on that default, so moving it again breaks nothing but the
handful of tests that are genuinely about the default.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.bots import (
    BotContext,
    BotProfile,
    choose_bot_action_decision,
    choose_bot_talk_decision,
    compute_trust_map,
)
from app.engine.bots import runtime as bot_runtime
from app.engine.game_records import ActionRecord
from app.engine.match_creation import create_match
from app.games import get as get_game_module
from app.games.hoard_hurt_help.game import HoardHurtHelp
from app.games.hoard_hurt_help.rules import (
    DEFAULT_MUTUAL_HELP_MODE,
    MutualHelpMode,
    mutual_help_value,
)
from app.games.hoard_hurt_help.scoring import current_pact_values, resolve_turn
from app.models import GameState, Match, Player, Turn, TurnSubmission, User
from app.models.user import UserRole
from tests.conftest import signed_in_cookies as _form_cookies
from tests.factories import make_bot, make_user


# --- DB fixtures (mirror tests/test_resolver.py, parametrized by the switch) ---


async def _make_mutual_help_match(
    db: AsyncSession, n: int, *, mutual_help_mode: str, match_id: str = "G_SW"
) -> tuple[Match, list[Player]]:
    now = datetime.now(timezone.utc)
    game = Match(
        id=match_id,
        name="test",
        state=GameState.ACTIVE,
        scheduled_start=now,
        started_at=now,
        per_turn_deadline_seconds=60,
        mutual_help_mode=mutual_help_mode,
    )
    db.add(game)
    await db.flush()
    players = []
    for i in range(n):
        u = User(google_sub=f"sub-{match_id}-{i}", email=f"u{match_id}{i}@test.com", name=f"u{i}")
        db.add(u)
        await db.flush()
        agent, _ = await make_bot(db, u, name=f"AI_{i}")
        p = Player(match_id=game.id, user_id=u.id, agent_id=agent.id, seat_name=f"AI_{i}")
        db.add(p)
        await db.flush()
        players.append(p)
    await db.commit()
    return game, players


async def _open_turn(db: AsyncSession, game: Match, turn_num: int) -> Turn:
    now = datetime.now(timezone.utc)
    t = Turn(
        match_id=game.id,
        round=1,
        turn=turn_num,
        turn_token=f"tk_{game.id}_{turn_num}",
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _submit(db: AsyncSession, turn: Turn, player: Player, action: str, target: Player | None = None) -> None:
    db.add(
        TurnSubmission(
            turn_id=turn.id,
            player_id=player.id,
            action=action,
            target_player_id=target.id if target else None,
            message="",
            submitted_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


# --- AC2: scoring through the real resolve_turn path ---


async def test_off_mutual_help_stays_flat_8(db):
    """OFF: a pair mutually helping 8 turns scores +8 each EVERY time (turns 2-8),
    not the decaying 8,7,6,… an ON match would show."""
    game, [a, b] = await _make_mutual_help_match(db, 2, mutual_help_mode="flat_8")
    prev = 0
    per_turn = []
    for i in range(8):
        turn = await _open_turn(db, game, i + 1)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await resolve_turn(db, turn)
        await db.refresh(a)
        per_turn.append(a.current_round_score - prev)
        prev = a.current_round_score
    # Flat +8 on every turn, including the farmed turns 2-8 where ON would decay.
    assert per_turn == [8, 8, 8, 8, 8, 8, 8, 8]


async def test_on_mutual_help_still_decays(db):
    """ON (default): the same 8-turn sequence still decays 8,7,6,5,4,3,2,2."""
    game, [a, b] = await _make_mutual_help_match(db, 2, mutual_help_mode="decay")
    prev = 0
    per_turn = []
    for i in range(8):
        turn = await _open_turn(db, game, i + 1)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await resolve_turn(db, turn)
        await db.refresh(a)
        per_turn.append(a.current_round_score - prev)
        prev = a.current_round_score
    assert per_turn == [8, 7, 6, 5, 4, 3, 2, 2]


async def test_current_pact_values_off_is_flat_8_while_on_decays(db):
    """After ≥1 farmed mutual help, OFF's live pact value stays 8 while ON drops to 7."""
    off_game, [oa, ob] = await _make_mutual_help_match(db, 2, mutual_help_mode="flat_8", match_id="G_OFF")
    t = await _open_turn(db, off_game, 1)
    await _submit(db, t, oa, "HELP", target=ob)
    await _submit(db, t, ob, "HELP", target=oa)
    await resolve_turn(db, t)
    assert await current_pact_values(
        db, off_game.id, oa.id, [ob.id], mode="flat_8"
    ) == {ob.id: 8}

    on_game, [na, nb] = await _make_mutual_help_match(db, 2, mutual_help_mode="decay", match_id="G_ON")
    t2 = await _open_turn(db, on_game, 1)
    await _submit(db, t2, na, "HELP", target=nb)
    await _submit(db, t2, nb, "HELP", target=na)
    await resolve_turn(db, t2)
    assert await current_pact_values(
        db, on_game.id, na.id, [nb.id], mode="decay"
    ) == {nb.id: 7}


# --- AC3: rules text differs by setting (all three AI-facing surfaces) ---

_DECAY_PHRASES = ("Mutual-help decays", "decays", "down to a floor of", "resets to +")


def _off_match() -> Match:
    # In-memory: set the round counts explicitly (SQLAlchemy defaults apply only at
    # flush, and make_game_rules_text would crash on None counts).
    return Match(mutual_help_mode="flat_8", total_rounds=5, turns_per_round=7)


def _on_match() -> Match:
    return Match(mutual_help_mode="decay", total_rounds=5, turns_per_round=7)


def test_semantic_rules_off_drops_decay_language():
    module = HoardHurtHelp()
    off = module.semantic_rules_text_for_match(_off_match())
    on = module.semantic_rules_text_for_match(_on_match())
    for phrase in _DECAY_PHRASES:
        assert phrase not in off, phrase
    assert "every time" in off
    assert "Mutual-help decays" in on  # the ON text still describes decay
    # The unrelated "Score floor" (round clip) rule stays in BOTH — scoping check.
    assert "## Score floor" in off and "## Score floor" in on


def test_count_based_rules_text_off_drops_decay():
    """The no-match surface: the mode is passed straight in, with no Match row.

    This is a different code path from the `*_for_match` test above — it is what
    the public /agent-instructions page renders, and it used to be checked through
    `rules_text_for_match`, a second builder that has since been removed.
    """
    module = HoardHurtHelp()
    off = module.semantic_rules_text(5, 7, mutual_help_mode="flat_8")
    for phrase in _DECAY_PHRASES:
        assert phrase not in off, phrase
    assert "every time" in off
    assert "Mutual-help decays" in module.semantic_rules_text(
        5, 7, mutual_help_mode="decay"
    )


def test_agent_base_prompt_off_drops_decay():
    """The `base_prompt` surface — the biggest AI-facing prompt, embeds the rules."""
    module = HoardHurtHelp()
    off = module.agent_base_prompt_for_match(
        _off_match(), your_agent_id="AI_0", all_agent_ids=["AI_0", "AI_1"]
    )
    for phrase in _DECAY_PHRASES:
        assert phrase not in off, phrase
    assert "every time" in off
    on = module.agent_base_prompt_for_match(
        _on_match(), your_agent_id="AI_0", all_agent_ids=["AI_0", "AI_1"]
    )
    assert "Mutual-help decays" in on


def test_liars_dice_rules_for_match_unchanged():
    """FR9: Liar's Dice inherits the BaseGameModule `*_for_match` default and returns
    its own real rules — proof the shared seam left other games untouched."""
    ld = get_game_module("liars-dice")
    match = Match(game="liars-dice", mutual_help_mode="flat_8", total_rounds=5, turns_per_round=7)
    text = ld.semantic_rules_text_for_match(match)
    # It is Liar's Dice's rules, not PD's, and carries no PD mutual-help language.
    assert "Mutual-help" not in text
    assert text == ld.semantic_rules_text(5, 7)


# --- AC4: agent "current worth" note matches the setting ---


async def test_pact_note_off_has_no_decay_words(db):
    """private_state_for on an OFF match (farmed pair): flat-8 pact_values and a note
    with no decay/floor language; the ON match keeps both."""
    module = get_game_module("hoard-hurt-help")

    off_game, [oa, ob] = await _make_mutual_help_match(db, 2, mutual_help_mode="flat_8", match_id="G_NOFF")
    t = await _open_turn(db, off_game, 1)
    await _submit(db, t, oa, "HELP", target=ob)
    await _submit(db, t, ob, "HELP", target=oa)
    await resolve_turn(db, t)
    state = await module.private_state_for(db, off_game, oa)
    assert state["pact_values"] == {ob.seat_name: 8}
    note = state["pact_values_note"].lower()
    assert "decay" not in note and "floor" not in note
    assert "every time" in note

    on_game, [na, nb] = await _make_mutual_help_match(db, 2, mutual_help_mode="decay", match_id="G_NON")
    t2 = await _open_turn(db, on_game, 1)
    await _submit(db, t2, na, "HELP", target=nb)
    await _submit(db, t2, nb, "HELP", target=na)
    await resolve_turn(db, t2)
    on_state = await module.private_state_for(db, on_game, na)
    assert on_state["pact_values"] == {nb.seat_name: 7}
    assert "decays" in on_state["pact_values_note"].lower()


async def test_pact_note_matches_the_payout_for_every_flat_mode(db):
    """The AC4 note's number must equal mutual_help_value(mode, 0) — not just
    coincidentally under flat_8 (where the old hardcoded 8 happened to be
    right), but under every flat mode, including today's default flat_6."""
    module = get_game_module("hoard-hurt-help")

    for i, mode in enumerate((MutualHelpMode.FLAT_6, MutualHelpMode.FLAT_7)):
        want = mutual_help_value(mode, 0)
        game, [a, b] = await _make_mutual_help_match(
            db, 2, mutual_help_mode=mode.value, match_id=f"G_FLATNOTE_{i}"
        )
        t = await _open_turn(db, game, 1)
        await _submit(db, t, a, "HELP", target=b)
        await _submit(db, t, b, "HELP", target=a)
        await resolve_turn(db, t)
        state = await module.private_state_for(db, game, a)
        assert state["pact_values"] == {b.seat_name: want}
        note = state["pact_values_note"]
        assert f"+{want}" in note, (mode, note)
        # Guard against the old hardcoded 8 surviving for a mode that isn't 8.
        if want != 8:
            assert f"+{8}" not in note, (mode, note)


# --- AC3/AC4: bot logic parity (partner fatigue mirrors the scoring decay) ---


def _rec(round_: int, turn: int, actor: str, action: str, target: str | None) -> ActionRecord:
    return ActionRecord(
        round=round_, turn=turn, actor_id=actor, action=action, target_id=target,
        message="", points_delta=0, round_score_after=0, was_defaulted=False,
    )


def _farmed_history(a: str, b: str, turns: int = 7) -> list[ActionRecord]:
    out: list[ActionRecord] = []
    for t in range(1, turns + 1):
        out += [_rec(1, t, a, "HELP", b), _rec(1, t, b, "HELP", a)]
    return out


def test_bot_partner_fatigue_off_not_applied():
    """A heavily farmed partner: ON erodes its trust to 0 (rotate away); OFF leaves
    it high (no decay to rotate from). Both arms asserted so neither is vacuous."""
    history = _farmed_history("AI_1", "AI_2")
    ids = ["AI_1", "AI_2", "AI_3"]
    on = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[],
        trust_model="even", mutual_help_mode="decay",
    )["AI_2"]
    off = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[],
        trust_model="even", mutual_help_mode="flat_8",
    )["AI_2"]
    assert on == 0  # farmed → fatigued to neutral under decay
    assert off >= 20  # under OFF the partner stays a valid ally
    assert off > on


def _bot_context(*, mutual_help_mode: str) -> BotContext:
    from app.schemas.agent import ScoreboardRow

    return BotContext(
        game_id="G_1",
        game_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        round=1,
        turn=2,
        phase="act",
        your_agent_id="AI_1",
        all_agent_ids=["AI_1", "AI_2", "AI_3"],
        history=_farmed_history("AI_1", "AI_2"),
        scoreboard=[
            ScoreboardRow(agent_id="AI_1", round_score=4, round_wins=0.0),
            ScoreboardRow(agent_id="AI_2", round_score=6, round_wins=0.0),
            ScoreboardRow(agent_id="AI_3", round_score=6, round_wins=0.0),
        ],
        current_talk_messages=[],
        turns_per_round=5,
        mutual_help_mode=mutual_help_mode,
    )


def test_bot_context_seed_basis_ignores_decay():
    """The decay flag must NOT enter the deterministic seed — else it would perturb
    every bot's tie-breaks and reintroduce the talk→act target-drift bug."""
    on = _bot_context(mutual_help_mode="decay")
    off = _bot_context(mutual_help_mode="flat_8")
    assert on.seed_basis() == off.seed_basis()


def test_bot_runtime_threads_decay_flag(monkeypatch):
    """service→context→runtime→trust wiring: both the act and talk paths pass the
    context's mutual_help_mode into compute_trust_map (not just the leaf)."""
    captured: list[bool] = []
    real = bot_runtime.compute_trust_map

    def spy(**kwargs):
        captured.append(kwargs["mutual_help_mode"])
        return real(**kwargs)

    monkeypatch.setattr(bot_runtime, "compute_trust_map", spy)
    ctx = _bot_context(mutual_help_mode="flat_8")
    profile = BotProfile(strategy="coalition_seeker", truthfulness=100, trust_model="even", seed=17, version="v1")
    choose_bot_action_decision(ctx, profile)
    choose_bot_talk_decision(ctx, profile)
    assert captured, "compute_trust_map was never called"
    assert all(v == "flat_8" for v in captured)  # every path threaded the mode


# --- AC4: viewer per-move value (what the replay/watch view + JS show) ---


def _mutual_timeline():
    from app.read_models.matches import TimelineAction, TimelineTurn

    def act(agent: str, target: str, delta: int, score_after: int) -> TimelineAction:
        return TimelineAction(
            agent_id=agent, action="HELP", target_id=target, quantity=None, face=None,
            message="", thinking="", points_delta=delta, round_score_after=score_after,
            submitted_at=datetime.now(timezone.utc), was_defaulted=False,
        )

    # Two turns of A↔B mutual help (the pair farms its pact).
    return [
        TimelineTurn(round=1, turn=1, messages=[], actions=[act("AI_0", "AI_1", 8, 8), act("AI_1", "AI_0", 8, 8)]),
        TimelineTurn(round=1, turn=2, messages=[], actions=[act("AI_0", "AI_1", 8, 16), act("AI_1", "AI_0", 8, 16)]),
    ]


async def _replay_delta_of_second_pact(*, mutual_help_mode: str) -> int:
    from app.games.hoard_hurt_help.viewer import build_pd_replay_view

    view = await build_pd_replay_view(
        db=None,
        match=Match(id="G_V", game="hoard-hurt-help", turns_per_round=7, mutual_help_mode=mutual_help_mode),
        players=[Player(seat_name="AI_0"), Player(seat_name="AI_1")],
        scoreboard=[
            {"agent_id": "AI_0", "round_score": 16, "round_wins": 0, "provider": None},
            {"agent_id": "AI_1", "round_score": 16, "round_wins": 0, "provider": None},
        ],
        timeline=_mutual_timeline(),
        viewer_seat="AI_0",
    )
    # The RC action `delta` is exactly what the animation JS credits.
    rc = json.loads(view["rc_data"])
    second_turn = rc["turns"][1]["actions"]
    return next(a["delta"] for a in second_turn if a["agent"] == "AI_0")


async def test_viewer_off_pact_value_flat_8():
    """The 2nd pact's RC delta (what the JS reads) is a flat 8 under OFF, 7 under ON."""
    assert await _replay_delta_of_second_pact(mutual_help_mode="flat_8") == 8
    assert await _replay_delta_of_second_pact(mutual_help_mode="decay") == 7


# --- AC1: the setting persists through create_match + a real DB round-trip ---


async def test_create_match_persists_flag(db):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    off = await create_match(
        db, game="hoard-hurt-help", name="off", scheduled_start=future,
        min_players=2, max_players=4, per_turn_deadline_seconds=60,
        total_rounds=5, turns_per_round=7, mutual_help_mode="flat_8",
    )
    default = await create_match(
        db, game="hoard-hurt-help", name="default", scheduled_start=future,
        min_players=2, max_players=4, per_turn_deadline_seconds=60,
        total_rounds=5, turns_per_round=7,  # omitted → the platform default
    )
    # Real round-trip: capture ids, expire the identity map, re-read from the DB.
    off_id, default_id = off.id, default.id
    db.expire_all()
    reloaded_off = (await db.execute(select(Match).where(Match.id == off_id))).scalar_one()
    reloaded_default = (
        await db.execute(select(Match).where(Match.id == default_id))
    ).scalar_one()
    assert reloaded_off.mutual_help_mode == "flat_8"
    assert reloaded_default.mutual_help_mode == DEFAULT_MUTUAL_HELP_MODE.value


# --- AC4/FR7: the watch-page robot-circle legend matches the setting ---


async def _seed_viewable_match(reset_db, match_id: str, *, mutual_help_mode: str) -> None:
    async with reset_db() as db:
        u = User(google_sub=f"leg-{match_id}", email=f"{match_id}@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id=match_id, name="L", state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc), current_round=1, current_turn=1,
            mutual_help_mode=mutual_help_mode,
        )
        db.add(g)
        await db.flush()
        agent, _ = await make_bot(db, u, name="AI_0")
        db.add(Player(match_id=match_id, user_id=u.id, agent_id=agent.id, seat_name="AI_0"))
        await db.commit()


async def test_legend_off_match_shows_flat(client, reset_db):
    await _seed_viewable_match(reset_db, "G_LOFF", mutual_help_mode="flat_8")
    r = await client.get("/games/hoard-hurt-help/matches/G_LOFF")
    assert r.status_code == 200
    assert "mutual +8 each, every time" in r.text
    assert "bonus decays each round" not in r.text


async def test_legend_on_match_shows_decay(client, reset_db):
    await _seed_viewable_match(reset_db, "G_LON", mutual_help_mode="decay")
    r = await client.get("/games/hoard-hurt-help/matches/G_LON")
    assert r.status_code == 200
    assert "mutual +8 each, bonus decays each round" in r.text


async def test_legend_on_a_default_rule_match_states_the_default_payout(client, reset_db):
    """What someone actually reads on a new match's page.

    The default is what a visitor meets first, so its legend is worth pinning
    on its own: the rest of the file proves each mode renders correctly, this
    proves the mode a new match gets is the one that shows up.
    """
    await _seed_viewable_match(
        reset_db, "G_LDEF", mutual_help_mode=DEFAULT_MUTUAL_HELP_MODE.value
    )
    r = await client.get("/games/hoard-hurt-help/matches/G_LDEF")
    assert r.status_code == 200
    assert "mutual +6 each, every time" in r.text
    assert "bonus decays each round" not in r.text


def test_legend_markup_requires_the_legend_to_be_supplied():
    """Every include site must pass rc_mutual_help_legend.

    The old `| default("mutual +8 each, bonus decays each round")` fallback
    quietly lied whenever a render path forgot to wire the variable in — it
    is gone now, so a missing value must fail the render loudly instead."""
    import pytest
    from jinja2 import UndefinedError

    from app.templating import templates

    with pytest.raises(UndefinedError):
        templates.env.get_template("fragments/robot_circle/_markup.html").render()


def test_legend_markup_off_renders_flat():
    """A flat-payout showcase renders the flat legend, not the decay one."""
    from app.games.hoard_hurt_help.rules import help_legend
    from app.templating import templates

    html = templates.env.get_template("fragments/robot_circle/_markup.html").render(
        rc_help_legend=help_legend("flat_8")
    )
    assert "mutual +8 each, every time" in html
    assert "bonus decays each round" not in html


def test_move_legend_derives_from_the_default_mode():
    """The lobby's general move legend is shown above a showcase replay that may be
    a different match's own mode, and above a marquee of live games that can each
    differ — so it has no one match to read a mode from. It states what a NEW
    match plays (DEFAULT_MUTUAL_HELP_MODE), rendered through mutual_help_legend
    rather than a hand-typed number that can drift from the real default."""
    from app.games.hoard_hurt_help.rules import mutual_help_legend
    from app.templating import templates

    html = templates.env.get_template("fragments/move_legend.html").render()
    assert mutual_help_legend(DEFAULT_MUTUAL_HELP_MODE) in html
    assert "mutual +8 each" not in html


async def test_showcase_mutual_help_mode_helper(db):
    """The showcase-legend gate: the bundled sample falls back to decay, and a
    real match reports its own mode. This is what the front page feeds the legend."""
    from app.routes.showcase_replay import showcase_mutual_help_mode

    assert await showcase_mutual_help_mode(db, None) == "decay"  # sample fallback
    on_game, _ = await _make_mutual_help_match(db, 2, mutual_help_mode="decay", match_id="G_SHOW_ON")
    off_game, _ = await _make_mutual_help_match(db, 2, mutual_help_mode="flat_8", match_id="G_SHOW_OFF")
    assert await showcase_mutual_help_mode(db, on_game.id) == "decay"
    assert await showcase_mutual_help_mode(db, off_game.id) == "flat_8"
    assert await showcase_mutual_help_mode(db, "G_MISSING") == "decay"  # missing row → default


async def test_lobby_showcase_supplies_the_robot_circle_legend(client, reset_db):
    """The per-game lobby's showcase robot-circle (no live game — the front
    page's sibling render path) must pass rc_mutual_help_legend the same way
    web_front_page.py already does. This was the audit's "known gap"
    (web_lobby.py passed rc_data but not the legend) — now that the markup's
    `| default(...)` fallback is gone, a missed context key would 500 the
    whole lobby instead of quietly showing a stale number."""
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "mutual +" in r.text and "each" in r.text


# --- The modes added alongside decay/flat_8 -----------------------------------
#
# These exist to compare rules that stop a single pact being a free, repeatable
# points engine. The lever is cooperation-vs-BETRAYAL: betraying a helper pays the
# attacker 8, so while a pact also pays 8 betrayal wins only on rank. Every point
# off the pact's payout widens that gap — which is what flat_6 is for.


def test_payout_table_for_every_mode():
    """The payout each mode pays on a pair's 1st, 2nd, 3rd … mutual help."""
    from app.games.hoard_hurt_help.rules import MutualHelpMode, mutual_help_value

    table = {m: [mutual_help_value(m, k) for k in range(5)] for m in MutualHelpMode}
    assert table[MutualHelpMode.DECAY] == [8, 7, 6, 5, 4]
    # NO_REPEATS ignores the running count entirely — it only looks at whether the
    # pair also went mutual on the PREVIOUS turn, covered by its own tests below.
    assert table[MutualHelpMode.NO_REPEATS] == [8, 8, 8, 8, 8]
    assert table[MutualHelpMode.FLAT_8] == [8, 8, 8, 8, 8]
    assert table[MutualHelpMode.FLAT_7] == [7, 7, 7, 7, 7]
    assert table[MutualHelpMode.FLAT_6] == [6, 6, 6, 6, 6]


def test_decay_never_pays_below_the_floor():
    """However often a pair repeats, decay stops at MUTUAL_HELP_FLOOR.

    Otherwise a long-running pact would eventually pay LESS than hoarding, making
    a mutual help actively self-harming rather than merely unrewarding.

    The floor used to be stated as "the HOARD value", back when HOARD was a flat
    2. HOARD is a contested pot now, so there is no single hoard number to equal —
    the floor matches what the pot pays once it is split enough ways, which the
    second assertion pins so the two cannot silently drift apart.
    """
    from app.games.hoard_hurt_help.rules import (
        HOARD_POT_POINTS,
        MUTUAL_HELP_FLOOR,
        MutualHelpMode,
        hoard_share,
        mutual_help_value,
    )

    floor = min(mutual_help_value(MutualHelpMode.DECAY, k) for k in range(50))
    assert floor == MUTUAL_HELP_FLOOR
    # A pot split this many ways pays the same as a fully decayed pact.
    assert hoard_share(HOARD_POT_POINTS // MUTUAL_HELP_FLOOR) == floor


def test_flat_6_makes_betrayal_out_pay_the_pact():
    """Betrayal must out-pay the pact — at v6 it beats EVERY mode, not just flat_6.

    Under the v5 payoffs betrayal paid 8, which merely tied the modes that pay a
    pact 8 (flat_8, and decay's first hit), so there it won only on rank. The v6
    bonus lifts betrayal to 10, clearing every pact rate outright. This pins that
    reversal: if a future mode pays 10 or more, the knife goes dead again.
    """
    from app.games.hoard_hurt_help.rules import (
        BETRAYAL_BONUS,
        HELP_POINTS,
        MutualHelpMode,
        mutual_help_value,
    )

    betrayal = HELP_POINTS + BETRAYAL_BONUS
    for mode in MutualHelpMode:
        assert mutual_help_value(mode, 0) < betrayal, mode


async def test_no_repeats_withholds_the_bonus_on_back_to_back_turns(db):
    """Same pair, two turns running: the second turn pays the plain HELP value."""
    from app.games.hoard_hurt_help.scoring import resolve_turn

    game, [a, b] = await _make_mutual_help_match(
        db, 2, mutual_help_mode="no_repeats", match_id="G_NR1"
    )
    first = await _open_turn(db, game, 1)
    await _submit(db, first, a, "HELP", target=b)
    await _submit(db, first, b, "HELP", target=a)
    await resolve_turn(db, first)
    assert a.current_round_score == 8

    second = await _open_turn(db, game, 2)
    await _submit(db, second, a, "HELP", target=b)
    await _submit(db, second, b, "HELP", target=a)
    await resolve_turn(db, second)
    assert a.current_round_score == 8 + 4  # back-to-back: no bonus


async def test_no_repeats_restores_the_bonus_after_a_skipped_turn(db):
    """This is the whole point of the rule: it is a cooldown, not a lifetime cap.

    Skipping one turn with that partner makes the full bonus available again — so
    a pair that alternates keeps earning it, which the 'once ever' reading would
    have denied.
    """
    from app.games.hoard_hurt_help.rules import hoard_share
    from app.games.hoard_hurt_help.scoring import resolve_turn

    game, [a, b] = await _make_mutual_help_match(
        db, 2, mutual_help_mode="no_repeats", match_id="G_NR2"
    )
    first = await _open_turn(db, game, 1)
    await _submit(db, first, a, "HELP", target=b)
    await _submit(db, first, b, "HELP", target=a)
    await resolve_turn(db, first)
    assert a.current_round_score == 8

    # Turn 2: both hoard, breaking the streak.
    second = await _open_turn(db, game, 2)
    await _submit(db, second, a, "HOARD")
    await _submit(db, second, b, "HOARD")
    await resolve_turn(db, second)
    # Both hoarded, so they split the pot two ways.
    assert a.current_round_score == 8 + hoard_share(2)

    # Turn 3: mutual again — the pair did NOT go mutual last turn, so full bonus.
    third = await _open_turn(db, game, 3)
    await _submit(db, third, a, "HELP", target=b)
    await _submit(db, third, b, "HELP", target=a)
    await resolve_turn(db, third)
    assert a.current_round_score == 8 + hoard_share(2) + 8


async def test_flat_6_pays_six_every_time(db):
    """A flat mode ignores history — the third mutual help pays what the first did."""
    from app.games.hoard_hurt_help.scoring import resolve_turn

    game, [a, b] = await _make_mutual_help_match(db, 2, mutual_help_mode="flat_6", match_id="G_F6")
    for turn_no in (1, 2, 3):
        t = await _open_turn(db, game, turn_no)
        await _submit(db, t, a, "HELP", target=b)
        await _submit(db, t, b, "HELP", target=a)
        await resolve_turn(db, t)
    assert a.current_round_score == 18  # 6 + 6 + 6


async def test_rules_text_matches_payout_for_every_mode():
    """The rules a player is shown must state the number the resolver pays.

    A legend or rules paragraph promising a different figure than the engine pays
    would be invisible until someone checked the arithmetic by hand.
    """
    from app.games.hoard_hurt_help.rules import (
        MutualHelpMode,
        make_game_rules_text,
        mutual_help_legend,
        mutual_help_value,
    )

    for mode in MutualHelpMode:
        first = mutual_help_value(mode, 0)
        assert f"+{first}" in mutual_help_legend(mode), mode
        assert f"+{first}" in make_game_rules_text(mode=mode), mode

    # Only the decaying mode may talk about decaying.
    for mode in MutualHelpMode:
        text = make_game_rules_text(mode=mode)
        assert ("decays" in text) == (mode is MutualHelpMode.DECAY), mode


def test_unknown_mode_is_rejected_not_defaulted():
    """A typo must raise, not silently become the default.

    Quietly defaulting would mislabel which rule a match was played under — an
    experiment result that looks fine and means something else.
    """
    import pytest

    from app.games.hoard_hurt_help.rules import mutual_help_value

    with pytest.raises(ValueError):
        mutual_help_value("flat_5", 0)


# --- The rule a NEW match gets -------------------------------------------------
#
# There are four create paths (the shared helper, its state-seeding wrapper, the
# admin JSON API, and the human form) plus the column's own backstop. They each
# carry their own default, so "the default moved" is only true when all of them
# moved. These pin that, and pin the value itself so the site's rule can't drift
# without a test saying so.


async def _seed_form_user(reset_db, *, i: int, role: UserRole = UserRole.USER) -> User:
    async with reset_db() as db:
        user = await make_user(db, i)
        user.role = role
        await db.commit()
        await db.refresh(user)
        return user


def test_new_matches_default_to_flat_6():
    """The platform-wide default rule. Change this test when the rule changes."""
    assert DEFAULT_MUTUAL_HELP_MODE is MutualHelpMode.FLAT_6


def test_every_create_default_is_the_platform_default():
    """No create path may quietly hand out a different rule than the others."""
    import inspect

    from app.engine.match_creation import create_match, create_match_with_state
    from app.models.match import Match
    from app.schemas.admin import CreateGameRequest

    want = DEFAULT_MUTUAL_HELP_MODE.value

    for fn in (create_match, create_match_with_state):
        param = inspect.signature(fn).parameters["mutual_help_mode"]
        assert param.default == want, fn.__name__

    assert CreateGameRequest(name="x", scheduled_start=datetime.now(timezone.utc)) \
        .mutual_help_mode == want

    # The column's default is written out as a literal because the model layer
    # cannot import a game module (the games package imports the engine, which
    # imports the model). This is the pin that keeps the two together.
    column = Match.__table__.c.mutual_help_mode
    assert column.default.arg == want
    assert column.server_default.arg == want


async def test_form_creates_a_default_mode_match_when_no_mode_is_posted(client, reset_db):
    """A plain player's match — the form shows them no rule control at all."""
    user = await _seed_form_user(reset_db, i=91)
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    r = await client.post(
        "/games/hoard-hurt-help/matches/new",
        data={"name": "Default Rule Match", "scheduled_start": when},
        cookies=_form_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        match = (
            await db.execute(select(Match).where(Match.name == "Default Rule Match"))
        ).scalar_one()
        assert match.mutual_help_mode == DEFAULT_MUTUAL_HELP_MODE.value


async def test_admin_form_preselects_the_default_mode(client, reset_db):
    """The admin picker opens on the same rule every other create path uses.

    A form quietly offering a different rule than the API and the poller would
    mislabel matches with nothing to notice.
    """
    admin = await _seed_form_user(reset_db, i=92, role=UserRole.ADMIN)
    r = await client.get(
        "/games/hoard-hurt-help/matches/new",
        cookies=_form_cookies(admin.id),
    )
    assert r.status_code == 200

    selected = [
        mode.value
        for mode in MutualHelpMode
        if f'value="{mode.value}" selected' in r.text
    ]
    assert selected == [DEFAULT_MUTUAL_HELP_MODE.value]


async def test_admin_form_offers_every_mode(client, reset_db):
    """The picker's options come from iterating MutualHelpMode (matches_user.py
    `_mutual_help_choices`), not a hand-typed list — so every current member is
    offered, and a future member would appear with no template edit. The
    preselect test above only checks that exactly one option is selected; this
    checks the full option set is actually there to select from."""
    admin = await _seed_form_user(reset_db, i=93, role=UserRole.ADMIN)
    r = await client.get(
        "/games/hoard-hurt-help/matches/new",
        cookies=_form_cookies(admin.id),
    )
    assert r.status_code == 200

    for mode in MutualHelpMode:
        assert f'value="{mode.value}"' in r.text, mode
        # Each option's label states its own real payout (mutual_help_legend),
        # not a hand-typed number that could drift from it.
        assert f"+{mutual_help_value(mode, 0)}" in r.text, mode
