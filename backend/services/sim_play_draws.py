"""Simulated seasons: the per-play draws of yards and touchdowns sim_parts draws a half's plays with (re-exported
by sim_parts and sim_draw): yards per player as gamma sums with their normal score, a half's TD count drive-limited
and leaning on that score (`game.td_yards_rho`, sim_parts' docstring)."""
import math

import numpy as np

from services.sim_world import params


def _phi(v: float) -> float:
    return 0.5 * (1 + math.erf(v / math.sqrt(2)))


def _yards(plays: np.ndarray, mean: np.ndarray, sd: np.ndarray, shift: float, rng: np.random.Generator) -> np.ndarray:
    """Whole yards over `plays` per player, each play a shifted gamma (mean, sd)."""
    return _yards_scored(plays, mean, sd, shift, rng, scored=False)[0]


def _yards_scored(plays: np.ndarray, mean: np.ndarray, sd: np.ndarray, shift: float,
                  rng: np.random.Generator, scored: bool = True) -> tuple[np.ndarray, float, float]:
    """`_yards` plus (sum of each player's yards sd x his normal score, sum of their variances): the normal score of
    a gamma draw by Wilson-Hilferty's cube root, close to exact from shape 1 (a player's yards over a few plays)."""
    out = np.zeros(len(plays), dtype=int)
    some = plays > 0
    if not some.any():
        return out, 0.0, 0.0
    base = np.maximum(mean[some] + shift, 0.5)
    k, theta = base ** 2 / sd[some] ** 2, sd[some] ** 2 / base
    shape = plays[some] * k
    drawn = rng.gamma(shape, theta)
    out[some] = np.round(drawn - plays[some] * shift).astype(int)
    if not scored:
        return out, 0.0, 0.0
    c = 1.0 / (9.0 * shape)
    score = ((drawn / (shape * theta)) ** (1.0 / 3.0) - (1.0 - c)) / np.sqrt(c)
    spread = np.sqrt(shape) * theta
    return out, float(spread @ score), float(spread @ spread)


def td_yards_rho() -> float:
    """`game.td_yards_rho` (sim_params.json's tuned section, or sim_eval --set): a correlation, so within [-1, 1]."""
    rho = float(params()["game"].get("td_yards_rho", 0.0))
    if not -1.0 <= rho <= 1.0:
        raise ValueError(f"game.td_yards_rho must be within [-1, 1], got {rho}")
    return rho


def _binomial_at(u: float, n: int, p: float) -> int:
    """The Binomial(n, p) count at quantile `u` (its inverse distribution function)."""
    if p >= 1.0:
        return n
    q, k = 1.0 - p, 0
    pmf = q ** n
    cdf = pmf
    while cdf < u and k < n:
        pmf *= (n - k) / (k + 1) * p / q
        k += 1
        cdf += pmf
    return k


def _touchdowns(catches: np.ndarray, rec_rate: np.ndarray, carries: np.ndarray, rush_rate: np.ndarray,
                drives: float, rng: np.random.Generator, slack: int = 1,
                yards: float | None = None, rho: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(receiving TDs, rushing TDs) per player: the total drive-limited, Binomial(drives, mean / drives) around the
    mean its catches and carries give (at least the mean + `slack` drives), then shared out play by play in
    proportion to each one's TD rate. A player never scores more TDs than he has catches or carries. With `yards`
    (the plays' yards as a standard normal score given the plays, `_yards_scored`) the count is the binomial's at
    the quantile Phi(rho x yards + sqrt(1 - rho^2) x a normal): the same Binomial, so every mean and spread is
    kept, but a half that gains more yards than its plays' mean scores more of its TDs, as a real one does."""
    plays = np.concatenate([catches, carries]).astype(int)
    rates = np.concatenate([rec_rate, rush_rate])
    mean = float((plays * rates).sum())
    tds = np.zeros(len(plays), dtype=int)
    if mean <= 0:
        return tds[:len(catches)], tds[len(catches):]
    n = max(int(round(drives)), int(np.ceil(mean)) + slack)
    if yards is None:
        total = min(int(rng.binomial(n, mean / n)), int(plays.sum()))
    else:
        u = _phi(rho * yards + math.sqrt(1.0 - rho * rho) * float(rng.standard_normal()))
        total = min(_binomial_at(u, n, mean / n), int(plays.sum()))
    while total > 0:                         # shared out in proportion; a player past his plays gives the rest back
        weights = (plays - tds) * rates
        if weights.sum() <= 0:
            break
        tds += rng.multinomial(total, weights / weights.sum())
        over = np.maximum(tds - plays, 0)
        tds -= over
        total = int(over.sum())
    return tds[:len(catches)], tds[len(catches):]
