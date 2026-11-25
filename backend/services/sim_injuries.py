"""Simulated seasons: injuries drawn from what really happened to fantasy-relevant players, 2018-2025.

Every rate and distribution lives in sim_injuries.json, fitted from the replay data by
    python -m services.sim_injuries --fit --seasons 2018-2025 [--check]
(the fit and the 1000-season sanity check are in services/sim_injuries_fit.py). This module only samples.

The model, per position (QB/RB/WR/TE/K; a defense is never hurt):
- `hazard`: the chance a player who plays a game is hurt in it: he misses the next team game with an injury
  tag, or he leaves it early (snaps well under his normal) and is listed Q/D for the next one but plays.
- `draw`: one injury. `games_out` comes from the real absence lengths in team games, counted across the
  offseason (a December season-ender who is back for week 1 missed the rest of that season only), so the
  long tail is real; `exit_share` is his share of that game's snaps (1.0 = he finished it); `body` follows the
  position's real body-part mix; `questionable_back` at the real rate (always for a banged-up player: that
  is how one is spotted in the data).
- `tag`: the tags a missed week carries, shaped like replay_injuries' real ones: week-to-week "O" with the
  report's usual practice status, and for a long absence "IR" (no practice status) from the missed game the
  real league usually designates it, held to the return. Shortcut: the real first missed week is sometimes
  Q/D (ruled out on game day) or PUP; those come out as O / IR, which every consumer treats the same.
- `phantom`: a healthy player listed Q/D who plays anyway, at the real rate.
- `open_spell` / `preseason`: an absence already running at the snapshot, and one that starts before week 1.

INJURY_SCALE (a product choice, not fitted: nobody likes injuries in fantasy football) multiplies `hazard` and
the preseason rate, so simulated seasons, full and mid alike, see about a quarter fewer new injuries than real
ones; lengths, tags and phantoms stay as fitted, and an absence already running (`open_spell`) is untouched.
sim_eval reports the real rates, the target (real x INJURY_SCALE) and the simulated ones.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bisect import bisect_right  # noqa: E402
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from services.replay_pool import IR_TAGS, Injury  # noqa: E402
from services.sim_constants import fitted_json  # noqa: E402

PATH = Path(__file__).with_name("sim_injuries.json")
FIT = "python -m services.sim_injuries --fit --seasons 2018-2025"
POSITIONS = ("QB", "RB", "WR", "TE", "K")
NEVER_HURT = ("DST",)
POOLED = "ALL"                      # the all-positions fit, used where a position has too little data
WEEKS_OUT_EDGES = (1, 2, 3, 4, 5, 7, 10)   # open_spell buckets: 1, 2, 3, 4, 5-6, 7-9, 10+ games out so far
MAX_BODY = 60
INJURY_SCALE = 0.75                 # simulated new injuries vs real ones (see the docstring)


@dataclass(frozen=True)
class Spell:
    games_out: int            # team games missed after the game he was hurt in (0 = banged up, misses none)
    exit_share: float         # share of that game he played before leaving, 0 < x <= 1
    body: str                 # "Hamstring" (shown to the user; at most 60 characters)
    questionable_back: bool   # listed Q in his first game back


@lru_cache(maxsize=1)
def stored() -> dict:
    """The fitted model (sim_injuries.json); raises if it is missing, since nothing can be drawn without it."""
    return fitted_json(PATH, FIT, ("positions", "ir", "remaining"))


def _fit(position: str) -> dict:
    if position not in POSITIONS:
        raise ValueError(f"No injury model for position {position!r}.")
    return stored()["positions"][position]


def _pick(dice: np.random.Generator, pmf: list) -> int:
    """A value from [[value, probability], ...]."""
    values, probs = zip(*pmf)
    weights = np.asarray(probs, dtype=float)
    return int(values[int(dice.choice(len(values), p=weights / weights.sum()))])


def _choose(dice: np.random.Generator, counts: dict | list) -> str:
    """A key from {key: count} or [[key, count], ...]."""
    items = list(counts.items()) if isinstance(counts, dict) else [tuple(pair) for pair in counts]
    keys, weights = zip(*items)
    weights = np.asarray(weights, dtype=float)
    return str(keys[int(dice.choice(len(keys), p=weights / weights.sum()))])


def _body(dice: np.random.Generator, fit: dict) -> str:
    return _choose(dice, fit["body"])[:MAX_BODY]


def hazard(position: str) -> float:
    """The chance a healthy player is hurt in a game he plays (0 for a defense): the fit x INJURY_SCALE."""
    if position in NEVER_HURT:
        return 0.0
    return INJURY_SCALE * float(_fit(position)["hazard"])


def draw(dice: np.random.Generator, position: str) -> Spell:
    """One injury suffered in a game (call it when `hazard` hits). 4 to 6 draws, in the same order (a banged-up
    player skips the exit and the return-tag ones)."""
    fit = _fit(position)
    banged = dice.random() < fit["banged_share"]
    games = _pick(dice, fit["length_pmf"])
    exits = banged or dice.random() < fit["exit_rate"]
    share = float(np.interp(dice.random(), np.linspace(0, 1, len(fit["exit_shares"])), fit["exit_shares"]))
    back = banged or dice.random() < fit["q_back"]
    body = _body(dice, fit)
    return Spell(games_out=0 if banged else games, exit_share=min(1.0, max(0.01, share)) if exits else 1.0,
                 body=body, questionable_back=back)


def _ir_from(games_out: int) -> int | None:
    """The first missed game (0-based) tagged IR, or None for a week-to-week absence."""
    ir = stored()["ir"]
    if games_out < ir["min_games"]:
        return None
    return max(0, min(ir["from"], games_out - ir["min_stint"]))


def tag(spell: Spell, missed: int) -> Injury:
    """His tag for a week he sits; `missed` = games already missed in this spell (0 for the first). Over a
    bye, use the tag of the game after it (replay_injuries carries a hard tag over a bye the same way)."""
    if missed < 0:
        raise ValueError(f"missed must be 0 or more, not {missed}.")
    start = _ir_from(spell.games_out)
    if start is not None and missed >= start:
        return Injury("IR", None, spell.body)
    return Injury("O", stored()["ir"]["o_practice"], spell.body)


def return_tag(spell: Spell) -> Injury | None:
    """The Q on his first game back (with the usual practice status for it), or None."""
    if not spell.questionable_back:
        return None
    return Injury("Q", stored()["ir"]["back_practice"], spell.body)


def phantom(dice: np.random.Generator, position: str) -> Injury | None:
    """A healthy player listed Q or D who plays anyway, at the real weekly rate. One draw when he is not listed
    (most players, every week: this runs for every regular), four when he is."""
    if position in NEVER_HURT:
        return None
    fit = _fit(position)
    if dice.random() >= fit["phantom_rate"]:
        return None
    label = _choose(dice, fit["phantom_tags"])
    practice = _choose(dice, fit["phantom_practice"][label])
    return Injury(label, practice, _body(dice, fit))


def open_spell(dice: np.random.Generator, position: str, tag: str, weeks_out: int) -> int:
    """Games still to miss after this one for a player already out at the snapshot: `tag` is his hard tag
    this week (O, IR or PUP), `weeks_out` the games he has missed so far counting this one. 0 = back next
    game; it may run past the end of the season. The conditional lengths are pooled over positions
    (`position` only selects 0 for a defense)."""
    if tag not in IR_TAGS:
        raise ValueError(f"open_spell needs an O, IR or PUP tag, not {tag!r}.")
    if position in NEVER_HURT:
        return 0
    _fit(position)
    group = "O" if tag == "O" else "IR"
    bucket = WEEKS_OUT_EDGES[max(0, bisect_right(WEEKS_OUT_EDGES, max(1, weeks_out)) - 1)]
    table = stored()["remaining"]
    pmf = table.get(f"{group}|{bucket}") or table[f"{POOLED}|{bucket}"]
    return max(0, _pick(dice, pmf))


def preseason(dice: np.random.Generator, position: str) -> Spell | None:
    """A player who starts the season hurt (PUP / IR / out in week 1), at the real rate of fresh ones x INJURY_SCALE (not
    counting last season's absences carried over: carry those with `open_spell`). games_out counts from
    week 1. Always 4 draws."""
    if position in NEVER_HURT:
        return None
    fit = _fit(position)
    hurt = dice.random() < INJURY_SCALE * fit["preseason_rate"]
    games = _pick(dice, fit["preseason_pmf"])
    body = _body(dice, fit)
    back = dice.random() < fit["q_back"]
    return Spell(games_out=max(1, games), exit_share=1.0, body=body, questionable_back=back) if hurt else None


if __name__ == "__main__":
    from services.sim_injuries_fit import main

    main()
