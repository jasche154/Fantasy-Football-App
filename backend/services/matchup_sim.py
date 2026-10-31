"""Head-to-head matchup simulation: each player's score drawn from his own range, with real
correlations between players in the same game.

- Each player's score comes from his empirical quantiles (services/score_ranges.py): skewed,
  with real bust risk, rather than a normal curve clipped at 0.
- Whether he plays is a separate draw from his chance to play, so injury risk is counted once.
- Players in the same game move together (a Gaussian copula). The pairs are measured from
  2021-2025 box scores: a quarterback and his receivers rise and fall together (+0.32 WR,
  +0.29 TE), a defense does well when the quarterback it faces doesn't (-0.375), opposing
  quarterbacks share shootouts (+0.17). Pairs not listed are independent.
- A player whose game is final contributes his actual points.
"""
import math
from collections.abc import Sequence

import numpy as np

from services import score_ranges

SIMULATIONS = 20_000
EIGEN_FLOOR = 1e-6
# Same team, by position pair (unordered).
SAME_TEAM = {
    frozenset(("QB", "WR")): 0.32, frozenset(("QB", "TE")): 0.29, frozenset(("QB", "RB")): 0.08,
    frozenset(("K", "DST")): 0.27, frozenset(("K", "RB")): 0.12, frozenset(("K", "QB")): 0.07,
    frozenset(("K", "WR")): 0.06, frozenset(("DST", "QB")): -0.07, frozenset(("DST", "WR")): -0.07,
    frozenset(("DST", "TE")): -0.07,
}
# Opposing teams in the same game, by position pair (unordered).
OPPONENTS = {
    frozenset(("QB",)): 0.17, frozenset(("QB", "WR")): 0.09, frozenset(("QB", "TE")): 0.09,
    frozenset(("WR",)): 0.05, frozenset(("TE", "WR")): 0.05, frozenset(("DST", "QB")): -0.375,
    frozenset(("DST", "K")): -0.31, frozenset(("DST",)): -0.24, frozenset(("DST", "RB")): -0.19,
    frozenset(("K",)): -0.13, frozenset(("DST", "WR")): -0.12,
}
ALIASES = {"DEF": "DST", "D/ST": "DST", "PK": "K"}


def _position(player) -> str:
    position = (player.position or "").upper()
    return ALIASES.get(position, position)


def pair_correlation(a, b) -> float:
    if a.player_id == b.player_id or not a.team or not b.team:
        return 0.0
    pair = frozenset((_position(a), _position(b)))
    if a.team == b.team:
        return SAME_TEAM.get(pair, 0.0)
    opponent = getattr(getattr(a, "matchup", None), "opponent", None)
    return OPPONENTS.get(pair, 0.0) if opponent == b.team else 0.0


def correlation_matrix(players: Sequence) -> np.ndarray:
    """The pairwise table as a valid correlation matrix (eigenvalues clipped, diagonal rescaled to 1)."""
    n = len(players)
    matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i, j] = matrix[j, i] = pair_correlation(players[i], players[j])
    values, vectors = np.linalg.eigh(matrix)
    if values.min() < EIGEN_FLOOR:
        matrix = vectors @ np.diag(np.maximum(values, EIGEN_FLOOR)) @ vectors.T
        scale = np.sqrt(np.diag(matrix))
        matrix = matrix / np.outer(scale, scale)
    return matrix


def _normal_cdf(z: np.ndarray) -> np.ndarray:
    """Standard normal CDF (Abramowitz-Stegun erf, error < 1.5e-7)."""
    x = np.abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * erf)


def quantiles_for(player) -> tuple[float, ...]:
    if getattr(player, "quantiles", None):
        return tuple(player.quantiles)
    mean = player.if_active or player.projection
    floor = player.floor if player.floor is not None else mean * 0.5
    ceiling = player.ceiling if player.ceiling is not None else mean * 1.5
    return score_ranges.normal_quantiles(mean, floor, ceiling)


def simulate_scores(players: Sequence, rng: np.random.Generator, n: int = SIMULATIONS) -> np.ndarray:
    """(n, players) simulated points, correlated within games."""
    if not players:
        return np.zeros((n, 0))
    chol = np.linalg.cholesky(correlation_matrix(players))
    uniforms = _normal_cdf(rng.standard_normal((n, len(players))) @ chol.T)
    scores = np.zeros((n, len(players)))
    for i, player in enumerate(players):
        if player.actual is not None:
            scores[:, i] = player.actual                       # game over
            continue
        points = score_ranges.sample(quantiles_for(player), uniforms[:, i])
        play_prob = player.play_prob if player.play_prob is not None else 1.0
        scores[:, i] = np.where(rng.random(n) < play_prob, points, 0.0)
    return scores


def simulate_matchup(mine: Sequence, theirs: Sequence, rng: np.random.Generator,
                     n: int = SIMULATIONS) -> tuple[np.ndarray, np.ndarray]:
    """Both lineups' totals, simulated together so players in the same game stay correlated."""
    scores = simulate_scores([*mine, *theirs], rng, n)
    return scores[:, :len(mine)].sum(axis=1), scores[:, len(mine):].sum(axis=1)


def win_probability(mine: Sequence, theirs: Sequence, seed: int = 7, n: int = SIMULATIONS) -> float:
    a, b = simulate_matchup(mine, theirs, np.random.default_rng(seed), n)
    return float(np.mean(a > b) + 0.5 * np.mean(a == b))
