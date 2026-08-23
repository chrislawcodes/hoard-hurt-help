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

    for pid in ("tit_for_tat", "headhunter", "turncoat", "dealmaker",
                "underdogs_champion", "sandbagger", "hoarder", "no_playbook"):
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
    # Each preset leads on a different engine; if two ever say the same thing we
    # are back to the collapse this roster exists to fix. Every line below moved
    # in the v7 rewrite, when HOARD became a contested pot and the presets were
    # cut to bare instructions with no payoff numbers in them.
    assert "move on" in presets["tit_for_tat"].prompt
    # Headhunter replaced Loyal Partner. Its distinctness is the TARGET RULE: it
    # is the only preset that goes after third parties by what they are doing,
    # rather than answering something done to it. Note what was given up — Loyal
    # Partner was the only preset at 0% HURT across ten matches, so the roster no
    # longer carries a pacifist control.
    assert "HELP someone else, and HURT them" in presets["headhunter"].prompt
    assert "never betrayed" in presets["turncoat"].prompt
    assert "offer a trade" in presets["dealmaker"].prompt
    assert "bottom half of the standings" in presets["underdogs_champion"].prompt
    # Sandbagger is the only preset that forbids HURT for an opening stretch, and
    # the only one whose behaviour turns on the match clock at all.
    assert "Until halfway through the match" in presets["sandbagger"].prompt
    # Hoarder (renamed from Salvager) is the only preset whose default is the pot.
    assert "HOARD by default" in presets["hoarder"].prompt
    # No Playbook is the CONTROL, not an eighth strategy: it is handed no plan at
    # all and must derive one. It is what tells us whether the seven authored
    # strategies beat what the model works out on its own — so the one thing it
    # must never do is name a move.
    assert "given no strategy" in presets["no_playbook"].prompt
    for verb in ("HOARD", "HELP", "HURT"):
        assert verb not in presets["no_playbook"].prompt, (
            f"No Playbook names {verb} — it must prescribe nothing"
        )

    bodies = [presets[p].prompt for p in
              ("tit_for_tat", "headhunter", "turncoat", "dealmaker",
               "underdogs_champion", "sandbagger", "hoarder", "no_playbook")]
    assert len(set(bodies)) == 8
    # Kingslayer is RETIRED, not renamed. It courted the current leader and
    # betrayed them, which needs the one player in the field with the least
    # reason to help you. It struck 0.3 times per 35-move match and scored by
    # hoarding instead. Raising BETRAYAL_BONUS 6 -> 14 left it on 0.60 round
    # wins either way, and the best rebuild scored 0.07 against a plain
    # cooperator's 0.49. Turncoat covers "befriend then betray" properly,
    # because it may partner anyone.
    assert "kingslayer" not in presets


def test_presets_do_not_carry_the_measured_bugs() -> None:
    """Prompts that were measured telling their agent the wrong thing.

    Each entry here cost real matches to find. The list grew at v7, when the
    guard clause below was measured dead and cut from every preset that had it.
    """
    presets = {p.id: p.prompt for p in get_game_module("hoard-hurt-help").strategy_presets()}

    # 1. THE GUARD CLAUSE — the big one. Four presets carried a version of "only
    #    HURT when it wins you the round". Measured across twelve matches (nine
    #    v5 + three v6) the HURT rate never rose above 2% and the analyzer called
    #    HURT a DEAD ACTION in every single one, because once anyone edges ahead
    #    the clause never evaluates true and every guarded agent stands down
    #    forever — which quietly hands the leader the match. It is gone from all
    #    eight presets and must not come back without new evidence.
    for pid, body in presets.items():
        assert "wins you the round" not in body, pid
        assert "still leaves you short" not in body, pid

    # 2. Kingslayer is gone at v8 (see the roster test for the measurements).
    #    No preset may pick up its rule, because the rule is what failed: a
    #    profitable HURT needs the victim to be HELPing you that turn, and the
    #    leader is the player least likely to. Aiming a betrayal at the leader
    #    BY RANK is the shape to keep out; Sandbagger may still partner the
    #    leader, but it spends a whole match earning that first.
    for pid, body in presets.items():
        assert "target whoever leads now" not in body, pid

    # 3. Turncoat (then Buzzer-Beater) was once told to strike "every round". That was the wrong
    #    half of a self-contradiction to keep: it struck up to 8 times, half of
    #    them at players who were not helping it (so they paid nothing), and took
    #    ZERO round wins across 21 rounds in M_6547 / M_6556 / M_6557. The ban on
    #    attacking a non-helper is what that fix actually bought, and it survives
    #    the v8 rewrite below.
    assert "every round" not in presets["turncoat"]
    assert "isn't HELPing you" in presets["turncoat"]

    #    Its calendar timing is GONE, and must not come back. "Never HURT before a
    #    round's last turn" was measured meaningless at v8: a round score is
    #    clipped at zero and nothing else, so the damage a HURT does is identical
    #    on turns two through five — waiting bought nothing and cost the preset
    #    every earlier chance to strike. The replacement times the strike off the
    #    victim's score (strike once the hit will not drop them to zero), which is
    #    the only timing the scoring actually rewards.
    assert "round's last turn" not in presets["turncoat"]

    # 4. The Champion's ban was meant to cover third parties only but read as a
    #    blanket "never attack", which stood it down completely. It now simply
    #    never betrays its own recruit, which is the behaviour that ban protected.
    assert "Never attack anyone else" not in presets["underdogs_champion"]
    assert "never betray them" in presets["underdogs_champion"]


def test_presets_state_behaviour_not_payoff_numbers() -> None:
    """No preset may quote a payoff figure. The rules text owns the numbers.

    Presets used to spell out payouts ("pays you 10", "banks 6", "the gap closes
    eighteen points") so the model could reason with them. That made the same
    rule live in two places, and the riskiest copy of all: a stale legend
    misinforms a spectator, a stale preset misinforms the player choosing the
    move. Every one of those figures moved twice in a single day — at v6 when
    BETRAYAL_BONUS and HURT_POINTS changed, and again at v7 when HOARD became a
    pot whose value is not even fixed within one turn.

    The v7 rewrite removed them all. The agent still gets every number, because
    the rules text it is handed states the full payoff table and is interpolated
    straight from the constants. The presets say what to DO. This keeps it that
    way: tune a payoff and no preset can be left describing the old game.
    """
    import re

    presets = {p.id: p.prompt for p in get_game_module("hoard-hurt-help").strategy_presets()}
    spelled = re.compile(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|eighteen|twenty)\b"
        r"\s+(points?|each)\b",
        re.I,
    )
    for pid, body in presets.items():
        assert not re.search(r"\d", body), f"{pid} quotes a digit: {body!r}"
        assert not spelled.search(body), f"{pid} spells out a payoff: {body!r}"
