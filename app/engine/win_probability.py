"""Win-probability predictions using pre-trained scikit-learn models.

Two public functions, both pure (no DB calls):

    score_match_win(players, actions, current_round, current_turn, total_rounds, turns_per_round)
    score_round_win(players, actions, current_round, current_turn, turns_per_round)

Both return {agent_id: float} win-probability estimates.
Returns an empty dict if the model file is not found.

Models live at data/win_prob_model.pkl and data/round_win_prob_model.pkl,
trained by scripts/train_win_prob.py and scripts/train_round_win_prob.py.

Call after a turn resolves — the current_turn_actions (all players' moves for
that turn) are included in the feature computation for the social-dynamics
features, matching the training data's feature semantics.
"""

from __future__ import annotations

import logging
import math
import pickle
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.engine.action_vocab import action_counts, pd_action_names
from app.engine.game_records import ActionRecord, PlayerRecord
from app.engine.win_prob_features import (
    MATCH_FEATURE_NAMES,
    ROUND_FEATURE_NAMES,
    feature_vector,
)

_MODEL_DIR = Path(__file__).resolve().parents[2] / "data"
_MATCH_MODEL_PATH = _MODEL_DIR / "win_prob_model.pkl"
_ROUND_MODEL_PATH = _MODEL_DIR / "round_win_prob_model.pkl"

# Lazy-loaded model cache. Absence of a key = not yet attempted.
_model_cache: dict[str, Any] = {}

logger = logging.getLogger(__name__)


def _load(name: str, path: Path) -> Any | None:
    if name not in _model_cache:
        if path.exists():
            try:
                with open(path, "rb") as fh:
                    _model_cache[name] = pickle.load(fh)["model"]
            except (
                ImportError,
                ModuleNotFoundError,
                OSError,
                pickle.UnpicklingError,
                KeyError,
            ) as exc:
                # fail-open: advisory only. Win-probability is an optional overlay,
                # so a model that can't load degrades the feature to empty dicts
                # rather than failing the turn. We only swallow the realistic load
                # failures — a missing dep (sklearn et al. not installed),
                # unreadable/corrupt file, or an unexpected pickle shape (no
                # "model" key). Anything else propagates so genuine bugs stay loud.
                logger.warning("Win-probability model %r unavailable: %s", name, exc)
                _model_cache[name] = None
        else:
            _model_cache[name] = None
    return _model_cache[name]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def _score_before(
    agent_id: str,
    actions: Sequence[ActionRecord],
    current_round: int,
    current_turn: int,
) -> float:
    """Round score entering current_turn (= round_score_after from the previous turn)."""
    if current_turn <= 1:
        return 0.0
    for a in actions:
        if a.actor_id == agent_id and a.round == current_round and a.turn == current_turn - 1:
            return float(a.round_score_after)
    return 0.0


# ---------------------------------------------------------------------------
# Feature context — pre-computed indexes, then per-player extraction
# ---------------------------------------------------------------------------


class _Ctx:
    """Pre-computed indexes for one feature-extraction pass."""

    def __init__(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        current_round: int,
        current_turn: int,
        total_rounds: int,
        turns_per_round: int,
    ) -> None:
        self.players = players
        self.rnd = current_round
        self.turn = current_turn
        self.total_rounds = total_rounds
        self.turns_per_round = turns_per_round
        self.n = len(players)

        # Actions strictly before the current (round, turn)
        self.prior = [
            a for a in actions
            if (a.round, a.turn) < (current_round, current_turn)
        ]
        # All players' actions FOR the current turn (post-resolution)
        self.cur = [
            a for a in actions
            if a.round == current_round and a.turn == current_turn
        ]

        self._round_winners = self._build_winners(actions, current_round)
        self._rw_before = self._build_rw_before()

    def _build_winners(
        self, actions: Sequence[ActionRecord], up_to_round: int
    ) -> dict[int, set[str]]:
        """agent_ids that won each completed round (strictly before current_round)."""
        last_t: dict[int, int] = {}
        for a in actions:
            if a.round < up_to_round:
                last_t[a.round] = max(last_t.get(a.round, 0), a.turn)

        final: dict[int, dict[str, int]] = {}
        for a in actions:
            lt = last_t.get(a.round)
            if lt is not None and a.turn == lt and a.round < up_to_round:
                final.setdefault(a.round, {})[a.actor_id] = a.round_score_after

        out: dict[int, set[str]] = {}
        for rnd, scores in final.items():
            best = max(scores.values())
            out[rnd] = {p for p, s in scores.items() if s == best}
        return out

    def _build_rw_before(self) -> dict[tuple[int, str], float]:
        """Cumulative round wins entering each round (1-indexed) for each player."""
        result: dict[tuple[int, str], float] = {}
        agent_ids = {p.agent_id for p in self.players}
        running: dict[str, float] = defaultdict(float)
        for r in range(1, self.rnd + 1):
            for pid in agent_ids:
                result[(r, pid)] = running[pid]
            winners = self._round_winners.get(r, set())
            if winners:
                share = 1.0 / len(winners)
                for w in winners:
                    running[w] += share
        return result

    def _leader_at(self, r: int) -> str | None:
        best = -1.0
        leaders: list[str] = []
        for p in self.players:
            w = self._rw_before.get((r, p.agent_id), 0.0)
            if w > best:
                best = w
                leaders = [p.agent_id]
            elif w == best:
                leaders.append(p.agent_id)
        return leaders[0] if len(leaders) == 1 else None

    def _rounds_same_leader(self) -> int:
        cur = self._leader_at(self.rnd)
        if cur is None:
            return 0
        count = 0
        for r in range(self.rnd - 1, 0, -1):
            if self._leader_at(r) == cur:
                count += 1
            else:
                break
        return count

    def _consec_rw(self, agent_id: str) -> int:
        count = 0
        for r in range(self.rnd - 1, 0, -1):
            if agent_id in self._round_winners.get(r, set()):
                count += 1
            else:
                break
        return count

    def _behavior(self, agent_id: str) -> tuple[int, int, int, int]:
        """(help, hurt, hoard, times_targeted) from prior actions."""
        # These names must match the strings the trained model's features were
        # built from; for PD they resolve to HOARD/HELP/HURT (unchanged).
        hoard_action, help_action, hurt_action = pd_action_names()
        my = [a for a in self.prior if a.actor_id == agent_id]
        counts = action_counts(my)
        return (
            counts[help_action],
            counts[hurt_action],
            counts[hoard_action],
            sum(1 for a in self.prior if a.action == hurt_action and a.target_id == agent_id),
        )

    def _table(self, agent_id: str) -> tuple[int, int, int, int, int, int]:
        """(help_cnt, hurt_cnt, hoard_cnt, was_piled, pile_max, mutual) for current turn."""
        hoard_action, help_action, hurt_action = pd_action_names()
        counts = action_counts(self.cur)
        tbl_help = counts[help_action]
        tbl_hurt = counts[hurt_action]
        tbl_hoard = counts[hoard_action]

        hurt_on: dict[str, int] = defaultdict(int)
        for a in self.cur:
            if a.action == hurt_action and a.target_id:
                hurt_on[a.target_id] += 1
        was_piled = 1 if hurt_on.get(agent_id, 0) >= 2 else 0
        pile_max = max(hurt_on.values()) if hurt_on else 0

        helped: dict[str, str] = {}
        for a in self.cur:
            if a.action == help_action and a.target_id:
                helped[a.actor_id] = a.target_id
        mutual = (
            1 if agent_id in helped and helped.get(helped[agent_id]) == agent_id else 0
        )
        return tbl_help, tbl_hurt, tbl_hoard, was_piled, pile_max, mutual

    def _match_rates(self) -> tuple[float, float]:
        total = len(self.prior)
        if total == 0:
            return 0.0, 0.0
        _, help_action, hurt_action = pd_action_names()
        counts = action_counts(self.prior)
        return (
            counts[help_action] / total,
            counts[hurt_action] / total,
        )

    # --- public feature builders ---

    def match_features_named(self, agent_id: str) -> dict[str, float]:
        """Named match-win features; order-free — MATCH_FEATURE_NAMES owns the order."""
        p = next(pl for pl in self.players if pl.agent_id == agent_id)

        scores = [float(pl.round_score) for pl in self.players]
        sb = _score_before(agent_id, self.prior + self.cur, self.rnd, self.turn)
        score_leader = max(scores)
        score_rank = sum(1 for s in scores if s > sb) + 1

        rw_vals = [float(pl.round_wins) for pl in self.players]
        rw = float(p.round_wins)
        rw_leader = max(rw_vals)
        rw_rank = sum(1 for w in rw_vals if w > rw) + 1

        hc, htc, hdc, tgt = self._behavior(agent_id)
        tbl_h, tbl_ht, tbl_hd, piled, pmax, mut = self._table(agent_id)
        consec = self._consec_rw(agent_id)
        my_prior = [a for a in self.prior if a.actor_id == agent_id]
        last_pts = float(my_prior[-1].points_delta) if my_prior else 0.0
        mhr, mhrt = self._match_rates()

        clinch = self.total_rounds / 2
        self_clinch = 1 if rw + 1 > clinch else 0
        leader_clinch = 1 if rw_rank > 1 and rw_leader + 1 > clinch else 0
        rsl = self._rounds_same_leader()

        total_r = max(self.total_rounds - 1, 1)
        total_t = max(self.turns_per_round - 1, 1)

        return {
            "round_frac": (self.rnd - 1) / total_r,
            "turn_frac": (self.turn - 1) / total_t,
            "score_before": sb,
            "round_wins_before": rw,
            "score_rank": float(score_rank),
            "score_gap_to_leader": score_leader - sb,
            "score_mean": _mean(scores),
            "score_std": _std(scores),
            "round_wins_rank": float(rw_rank),
            "round_wins_leader": rw_leader,
            "round_wins_mean": _mean(rw_vals),
            "n_players": float(self.n),
            "help_count": float(hc),
            "hurt_count": float(htc),
            "hoard_count": float(hdc),
            "times_targeted": float(tgt),
            "table_help_count": float(tbl_h),
            "table_hurt_count": float(tbl_ht),
            "table_hoard_count": float(tbl_hd),
            "was_piled_on": float(piled),
            "pile_on_max": float(pmax),
            "got_mutual_help": float(mut),
            "consecutive_round_wins": float(consec),
            "last_points_delta": last_pts,
            "match_help_rate": mhr,
            "match_hurt_rate": mhrt,
            "self_can_clinch": float(self_clinch),
            "leader_can_clinch": float(leader_clinch),
            "rounds_same_leader": float(rsl),
        }

    def match_features(self, agent_id: str) -> list[float]:
        """Feature vector in MATCH_FEATURE_NAMES order (data/win_prob_model.pkl input)."""
        return feature_vector(self.match_features_named(agent_id), MATCH_FEATURE_NAMES)

    def round_features_named(self, agent_id: str) -> dict[str, float]:
        """Named round-win features; order-free — ROUND_FEATURE_NAMES owns the order."""
        scores = [float(pl.round_score) for pl in self.players]
        sb = _score_before(agent_id, self.prior + self.cur, self.rnd, self.turn)
        score_leader = max(scores)
        score_rank = sum(1 for s in scores if s > sb) + 1

        hc, htc, hdc, tgt = self._behavior(agent_id)
        tbl_h, tbl_ht, tbl_hd, piled, pmax, mut = self._table(agent_id)
        my_prior = [a for a in self.prior if a.actor_id == agent_id]
        last_pts = float(my_prior[-1].points_delta) if my_prior else 0.0
        mhr, mhrt = self._match_rates()

        total_t = max(self.turns_per_round - 1, 1)

        return {
            "turn_frac": (self.turn - 1) / total_t,
            "score_before": sb,
            "score_rank": float(score_rank),
            "score_gap_to_leader": score_leader - sb,
            "score_mean": _mean(scores),
            "score_std": _std(scores),
            "n_players": float(self.n),
            "help_count": float(hc),
            "hurt_count": float(htc),
            "hoard_count": float(hdc),
            "times_targeted": float(tgt),
            "table_help_count": float(tbl_h),
            "table_hurt_count": float(tbl_ht),
            "table_hoard_count": float(tbl_hd),
            "was_piled_on": float(piled),
            "pile_on_max": float(pmax),
            "got_mutual_help": float(mut),
            "last_points_delta": last_pts,
            "match_help_rate": mhr,
            "match_hurt_rate": mhrt,
        }

    def round_features(self, agent_id: str) -> list[float]:
        """Feature vector in ROUND_FEATURE_NAMES order (data/round_win_prob_model.pkl input)."""
        return feature_vector(self.round_features_named(agent_id), ROUND_FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_match_win(
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    current_round: int,
    current_turn: int,
    total_rounds: int,
    turns_per_round: int,
) -> dict[str, float]:
    """Return {agent_id: match-win probability} for each player.

    Pass the full action list including the current turn's resolved submissions.
    Returns {} if the model file is absent or players is empty.
    """
    model = _load("match", _MATCH_MODEL_PATH)
    if model is None or not players:
        return {}
    ctx = _Ctx(players, actions, current_round, current_turn, total_rounds, turns_per_round)
    return {
        p.agent_id: float(model.predict_proba([ctx.match_features(p.agent_id)])[0][1])
        for p in players
    }


def score_round_win(
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    current_round: int,
    current_turn: int,
    turns_per_round: int,
) -> dict[str, float]:
    """Return {agent_id: round-win probability} for each player.

    Pass the full action list including the current turn's resolved submissions.
    Returns {} if the model file is absent or players is empty.
    """
    model = _load("round", _ROUND_MODEL_PATH)
    if model is None or not players:
        return {}
    ctx = _Ctx(players, actions, current_round, current_turn, 0, turns_per_round)
    return {
        p.agent_id: float(model.predict_proba([ctx.round_features(p.agent_id)])[0][1])
        for p in players
    }
