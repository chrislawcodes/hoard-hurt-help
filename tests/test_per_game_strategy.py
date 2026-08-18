"""Per-game strategy at entry (preset or free text); the profile library is gone."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.games import get as get_game_module
from app.models import Agent
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.agent import AgentStatus
from tests.factories import make_connection, make_user
from tests.conftest import signed_in_cookies as _signed_in_cookies


@pytest.fixture(autouse=True)
async def reset_db(reset_db: async_sessionmaker) -> async_sessionmaker:
    """Autouse override of tests/conftest.py's reset_db: every test here touches the DB."""
    return reset_db


async def _seed_game_user_agent(
    reset_db: async_sessionmaker,
) -> tuple[int, int]:
    """Create a signed-in user with one active connection."""
    async with reset_db() as db:
        user = await make_user(db)
        await db.flush()
        connection, _ = await make_connection(db, user)
        connection.first_connected_at = datetime.now(timezone.utc)
        connection.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        return user.id, connection.id


async def _latest_strategy(reset_db: async_sessionmaker, agent_id: int) -> str:
    async with reset_db() as db:
        prompt = (
            await db.execute(
                select(AgentVersion.strategy_text)
                .where(AgentVersion.agent_id == agent_id)
                .order_by(AgentVersion.version_no.desc())
            )
        ).scalar_one()
        return prompt


def test_pd_module_exposes_presets_and_default() -> None:
    module = get_game_module("hoard-hurt-help")
    presets = module.strategy_presets()
    assert len(presets) >= 1
    for p in presets:
        assert p.id and p.name and p.prompt
    assert module.default_strategy().strip()


def test_default_strategies_do_not_repeat_base_instructions() -> None:
    module = get_game_module("hoard-hurt-help")
    strategies = [module.default_strategy(), *(preset.prompt for preset in module.strategy_presets())]
    repeated_base_phrases = (
        "You are playing Hoard-Hurt-Help",
        "Read the full rules",
        "full raw record",
        "read the chat",
        "TALK PHASE",
        "target_id",
    )
    for strategy in strategies:
        assert "Prioritize round wins" in strategy
        for phrase in repeated_base_phrases:
            assert phrase not in strategy


def test_agent_base_prompt_contains_shared_instructions_not_strategy() -> None:
    module = get_game_module("hoard-hurt-help")
    prompt = module.agent_base_prompt(
        your_agent_id="Alpha",
        all_agent_ids=["Alpha", "Beta"],
    )
    assert 'as agent "Alpha"' in prompt
    assert "The chat is part of the game" in prompt
    assert "HISTORY" not in prompt
    assert "max 200 chars" in prompt
    assert 'Agents you may target: ["Beta"]' in prompt
    assert prompt.index("Agents you may target") < prompt.index("RESPONSE FORMAT:")
    assert prompt.endswith("counts as a missed move.")
    assert "Prioritize round wins" not in prompt


async def test_join_with_custom_strategy_seeds_it(client, reset_db) -> None:
    user_id, _connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "CUSTOM: always cooperate.",
        },
        cookies=_signed_in_cookies(user_id),
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user_id, Agent.name == "Atlas")
            )
        ).scalar_one()
    assert await _latest_strategy(reset_db, agent_id) == "CUSTOM: always cooperate."


async def test_join_without_strategy_uses_module_default(client, reset_db) -> None:
    user_id, _connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
        },
        cookies=_signed_in_cookies(user_id),
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user_id, Agent.name == "Atlas")
            )
        ).scalar_one()
    seeded = await _latest_strategy(reset_db, agent_id)
    assert seeded == get_game_module("hoard-hurt-help").default_strategy()


async def test_join_with_preset_strategy_seeds_preset_prompt(client, reset_db) -> None:
    user_id, _connection_id = await _seed_game_user_agent(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_preset": "tit_for_tat",
        },
        cookies=_signed_in_cookies(user_id),
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user_id, Agent.name == "Atlas")
            )
        ).scalar_one()
    expected = next(
        preset.prompt
        for preset in get_game_module("hoard-hurt-help").strategy_presets()
        if preset.id == "tit_for_tat"
    )
    assert await _latest_strategy(reset_db, agent_id) == expected


async def test_create_form_has_no_model_picker_but_keeps_presets(
    client, reset_db
) -> None:
    # Agents are decoupled from a model/provider — the create form has no model or
    # provider picker. It still offers strategy presets and a free-text strategy.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    r = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert 'name="provider"' not in r.text
    assert 'name="model"' not in r.text
    assert "<optgroup" not in r.text
    assert 'name="strategy_preset"' not in r.text
    assert 'name="strategy_text"' in r.text
    assert 'href="/games/hoard-hurt-help/agent-instructions"' in r.text
    assert 'data-preset-id="tit_for_tat"' in r.text
    assert 'data-preset-id="custom"' in r.text
    assert r.text.index('data-preset-id="tit_for_tat"') < r.text.index('data-preset-id="custom"')
    tit_snippet = r.text[r.text.index('data-preset-id="tit_for_tat"') : r.text.index('data-preset-id="tit_for_tat"') + 220]
    assert 'aria-pressed="true"' in tit_snippet


async def test_agent_instructions_page_shows_canonical_base_prompt(client, reset_db) -> None:
    r = await client.get("/games/hoard-hurt-help/agent-instructions")
    assert r.status_code == 200
    assert "Base instructions" in r.text
    assert "Your editable strategy is added separately" in r.text
    assert "The chat is part of the game" in r.text
    assert "max 200 chars" in r.text
    assert "X-Agent-Key" not in r.text
    assert "Prioritize round wins" not in r.text


async def test_create_agent_page_without_any_connection_shows_full_form(
    client, reset_db
) -> None:
    # No provider connected at all: the create-agent page still renders the full
    # design form so the player can name the agent and save a strategy before
    # doing the technical setup. There is no model/provider picker.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    r = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Connect an AI client first" not in r.text
    assert 'name="model"' not in r.text
    assert "<optgroup" not in r.text
    assert 'name="strategy_text"' in r.text


async def test_create_agent_without_live_connection_still_creates_agent(
    client, reset_db
) -> None:
    # Creating an agent always works (name + strategy). Post-create lands on the
    # lobby, where joining a game walks the user through connecting an AI.
    async with reset_db() as db:
        user = await make_user(db)
        await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()

    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "strategy_text": "CUSTOM: always cooperate.",
        },
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/games/hoard-hurt-help"

    async with reset_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.user_id == user.id, Agent.name == "Atlas"))
        ).scalar_one()
    assert agent.provider is None
    assert agent.status == AgentStatus.ACTIVE


async def test_create_agent_with_next_returns_to_next_target(
    client, reset_db
) -> None:
    # When ?next is present AND the agent's provider is already set up, creation
    # returns straight there. (Claude is set up here, so it's a direct hop.)
    join_url = "/games/hoard-hurt-help/matches/G_001/join"
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    r = await client.post(
        "/me/agents/new",
        data={
            "name": "Atlas",
            "model": "claude-haiku-4-5",
            "strategy_text": "CUSTOM: always cooperate.",
            "next": join_url,
        },
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    loc = r.headers["location"]
    assert loc == join_url


async def test_strategy_profiles_route_removed(client, reset_db) -> None:
    user_id, _ = await _seed_game_user_agent(reset_db)
    r = await client.get(
        "/me/strategy-profiles", cookies=_signed_in_cookies(user_id)
    )
    assert r.status_code == 404


def test_core_presets_pick_one_target_and_stay_distinct() -> None:
    """The four presets rewritten from the M_6442 analysis must survive.

    Each was collapsing into "help everybody" because it was written for a
    two-player game. The shared framing now states the one-action limit, and
    each of the four names a different way to get clear of the pack — so this
    pins both the roster and the thing that makes them different.
    """
    module = get_game_module("hoard-hurt-help")
    presets = {p.id: p for p in module.strategy_presets()}

    for pid in ("tit_for_tat", "loyal_partner", "buzzer_beater", "dealmaker",
                "underdogs_champion", "kingslayer", "sandbagger", "salvager"):
        assert pid in presets, f"{pid} preset is missing"

    # The join UI selects the first preset by default, and a test elsewhere
    # asserts it is Tit-for-Tat, so the order is load-bearing.
    assert module.strategy_presets()[0].id == "tit_for_tat"

    # The shared framing carries ONE fact now: a clean pact leaves both sides
    # level, so winning takes asymmetry. It is the only one the rules do not
    # already hand the agent — they state the payoffs, this is derived from them.
    #
    # This used to pin "ONE action against ONE player per turn". That bullet came
    # from #681 after M_6442, but the SAME commit removed the impossible "mirror
    # each opponent" instruction from the preset bodies that M_6442 actually
    # blamed. The two fixes were never separated, so nothing showed the bullet
    # doing work of its own, and the rules already say "choose exactly one
    # action". What it guarded is pinned where it belongs — each preset naming a
    # target it can really pick, asserted just below.
    # The shared framing is one line now: the objective, and nothing else. It
    # previously also asserted "level is not a win" — part of an "even swaps
    # leave you level" claim that only held under the FLAT mutual-help modes.
    # Under `decay` a pair's fifth swap pays 4 against a fresh pair's 8, and
    # `decay` is LEGACY_MUTUAL_HELP_MODE, so every pre-switch match broke it.
    # A line shared by all eight presets has to be true in every mode.
    framing = module.default_strategy()
    assert "Prioritize round wins" in framing
    assert "level is not a win" not in framing

    # Each of the four leads on a different engine; if two ever say the same
    # thing we are back to the collapse this rewrite exists to fix.
    assert "answer that ONE player" in presets["tit_for_tat"].prompt
    # Loyal Partner is the only preset that never HURTs anyone, which is what
    # keeps it distinct from Tit-for-Tat (same loyalty, but it retaliates).
    # This used to pin "without exception" — from when the preset kept HELPing
    # a player who had HURT it. It no longer does: betrayal ends the pact and
    # it finds a new partner, so the old wording described a strategy that is
    # gone. Renamed from always_cooperate for the same reason.
    assert "Never HURT anyone" in presets["loyal_partner"].prompt
    assert "LAST turn of a round" in presets["buzzer_beater"].prompt
    assert "helped more than anyone else" in presets["dealmaker"].prompt
    assert "Recruit the freshly abandoned" in presets["underdogs_champion"].prompt
    assert "whoever is winning" in presets["kingslayer"].prompt
    # Was "credibility can only be spent once" — that line was trimmed. The
    # distinguishing behaviour is the DELAY, so pin that instead: Sandbagger is
    # the only preset that forbids HURT for a fixed opening stretch of the match.
    assert "first two thirds" in presets["sandbagger"].prompt
    assert "mathematically out of it" in presets["salvager"].prompt

    bodies = [presets[p].prompt for p in
              ("tit_for_tat", "loyal_partner", "buzzer_beater", "dealmaker",
               "underdogs_champion", "kingslayer", "sandbagger", "salvager")]
    assert len(set(bodies)) == 8


def test_presets_do_not_carry_the_three_measured_bugs() -> None:
    """Three prompts told their agent the wrong thing. Pin the corrections.

    Kingslayer and the Champion were measured in M_6484, where both under-fired:
    Kingslayer struck twice and came 6th, the Champion never struck at all.
    Buzzer-Beater's entry is different — see the comment on it below. Its first
    correction was itself wrong, and this test pinned that error until three
    matches (M_6547 / M_6556 / M_6557) showed it scoring zero round wins in 21.
    """
    presets = {p.id: p for p in get_game_module("hoard-hurt-help").strategy_presets()}

    # 1. Kingslayer's reach was understated. Betraying the leader swings the gap
    #    by ~12, not "a few points" — the old text stood it down when it should
    #    have struck. The earlier arithmetic gave the leader a full mutual bonus
    #    while they were HELPing the Kingslayer, which one action per turn makes
    #    impossible.
    king = presets["kingslayer"].prompt
    assert "a lead of a few points" not in king
    assert "twelve points" in king

    # 2. Buzzer-Beater. This assertion previously required the prompt to say
    #    "every round" — it pinned a MISTAKE, and three matches proved it.
    #
    #    The original prompt contradicted itself: strike every round, but also
    #    wait for the final round. The contradiction was real; resolving it
    #    toward "every round" was the wrong half to keep. Measured: with the old
    #    self-contradicting text it struck twice and finished 3rd of 8 (M_6484).
    #    Told to strike every round it struck up to 8 times, half of them at
    #    players who were not helping it (so they paid nothing), bled helpers
    #    from 0.80/turn to 0.33, and took ZERO round wins across 21 rounds in
    #    M_6547 / M_6556 / M_6557.
    #
    #    So the timing is pinned in both directions now: late in the round AND
    #    late in the match, and only when the arithmetic actually wins it.
    buzz = presets["buzzer_beater"].prompt
    assert "LAST turn of a round, never earlier" in buzz
    assert "late in the MATCH" in buzz
    # Was "Only strike when it actually wins you the round". Same rule, said in
    # the action names the agent answers with — a vocabulary change, not a
    # behaviour one. The measured correction it guards (do not HURT unless the
    # round is actually won by it) is unchanged.
    assert "Only HURT when it wins you the round" in buzz
    assert "every round" not in buzz
    # It also arrived under-helped BEFORE striking (0.47/turn vs ~1.0 for the
    # field), because it only ever fed one partner. It must court help too.
    # Was "Do not settle for one loyal partner" — reworded because "Loyal Partner"
    # is now a preset name, so the old phrasing read as a reference to it. Same
    # instruction: court a second helper, do not rely on a single one.
    assert "Do not settle for one helper" in buzz

    # 3. The Champion's ban was meant to cover third parties only, but read as a
    #    blanket "never attack" and suppressed its own conditional strike.
    champ = presets["underdogs_champion"].prompt
    assert "Never attack anyone else" not in champ
    assert "THIRD PARTY" in champ
    # The reassurance clause ("not a ban on striking at all") is gone — owner
    # call. What it protected is pinned directly instead, which is a stronger
    # test: the preset must still carry a conditional HURT on its OWN partner.
    # In M_6484 a blanket-sounding ban suppressed exactly that and the Champion
    # HURT zero times all match. The ban is now scoped by naming THIRD PARTY,
    # with no general "never attack" wording for a model to over-read.
    #
    # UNMEASURED: #686 fixed this with BOTH the scoping and the clause, and only
    # the pair was ever played. If the Champion goes quiet again, the dropped
    # clause is the first thing to put back.
    assert "HURT them only on a round's last turn" in champ
