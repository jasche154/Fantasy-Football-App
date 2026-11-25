"""Simulated seasons: the pregame line of every game (expected margin and total), public and point in time.

`week_lines(known, week, belief, out, prior, posted)` reads only the games of `known` played before `week` (its
scores), its schedule, the public belief of that week (sim_world.estimate) and last season's ratings (`prior`),
never the truth:

1. Team ratings (`ratings`): an opponent-adjusted offense and defense, the points a team scores and allows over
   the league level (`SeasonData.scores`, home edge removed). A game `w` weeks old counts `decay` ** w;
   `prior_games` pseudo-games hold each team at last season's final rating x `carry` (`carry_over`), else at 0.
   The league level is this season's mean shrunk toward the long-run one by `level_games` pseudo-games.
2. The belief's own expectation: each side's mean points under the belief with the week's hard-tagged players
   out (`summary`), the input that knows who plays and who is hurt.
3. The quarterbacks: the belief's quality of each side's starter (`qb_level`) and that minus the quality of the
   team's usual passer (`qb_change`: the ratings were earned with him).
4. Rest: a team coming off its bye (+1), or on a short week (-1, a Thursday game after a Sunday), from the
   schedule's game days.
5. The line: margin = a constant (the home edge) + a linear mix of the inputs' margins (each side's value minus
   the other's); total = a constant + a linear mix of their sums. The coefficients are fitted to the Vegas
   closing line, 2018-2025, leave-season-out, and judged on held-out real results (services/sim_lines_fit.py,
   `python -m services.sim_lines_fit --fit`); they live in sim_lines.json. Inputs that added nothing on
   held-out seasons were dropped (yards per play, team EPA, the past weeks' Vegas lines: see sim_lines_fit).

A posted line (`posted`: {(home team, week): (spread or None, total or None)}, a live season's real lines for
the first simulated week, passed in by sim_setup.posted_lines; sim_season uses them in that week only, since a
later week's posted line knows nothing of the simulated games before it) replaces the model's part by part:
a posted spread or total is used where there is one, the model's for the other; a posted total keeps MIN_TOTAL;
a value outside POSTED_RANGE is logged and ignored.
"""
import json
import logging
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services.replay_pool import SeasonData
from services.sim_constants import fitted_json
from services.sim_game import _mean_points, _side_means, _side_of
from services.sim_world import World, params

LINES_PATH = Path(__file__).with_name("sim_lines.json")
LINES_FIT = "python -m services.sim_lines_fit --fit"
ITERATIONS = 8                   # alternating offense / defense solves (converged well before this)
BYE_DAYS = 13                    # this many days since the last game: coming off a bye
SHORT_DAYS = 5                   # this few: a short week
MIN_TOTAL = 20.0                 # a predicted total is kept at or above this (real totals: 28.5 at the lowest)
POSTED_RANGE = {"spread": (-35.0, 35.0), "total": (20.0, 80.0)}   # a posted value outside is a data error
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Line:
    margin: float                # home points minus away points expected
    total: float
    belief: tuple[float, float] | None = field(default=None, compare=False)   # the belief's own (home, away) points

    @property
    def home(self) -> float:
        return (self.total + self.margin) / 2

    @property
    def away(self) -> float:
        return (self.total - self.margin) / 2


@dataclass(frozen=True)
class Rating:
    offense: float = 0.0         # points it scores over the league level
    defense: float = 0.0         # points it allows over the league level


@dataclass(frozen=True)
class Rated:
    level: float                 # league points per team game
    teams: Mapping[str, Rating]


@lru_cache(maxsize=1)
def stored() -> Mapping:
    return MappingProxyType(fitted_json(LINES_PATH, LINES_FIT, ("ratings", "margin", "total", "yards_power")))


def yards_power() -> float:
    """How a side's yards move with its expected points (yards x f ** this when points go x f; sim_game)."""
    return float(stored()["yards_power"])


def scores(known: SeasonData, week: int) -> list[tuple[int, str, str, int, int]]:
    """[(week, home, away, home points, away points)] of `known`'s scored games before `week`."""
    out = []
    for g in known.schedule.games:
        hs, as_ = known.scores.get((g.home_team, g.week)), known.scores.get((g.away_team, g.week))
        if g.week < week and hs is not None and as_ is not None:
            out.append((g.week, g.home_team, g.away_team, hs, as_))
    return out


def ratings(games: Collection[tuple[int, str, str, int, int]], week: int, prior: Mapping[str, Rating] | None = None,
            knobs: Mapping | None = None) -> Rated:
    """Every team's rating before `week` from `games` (`scores`), each shrunk toward its `prior` (see the module
    docstring): points = level + offense[team] + defense[opponent] +/- half the home edge, solved by turns."""
    knobs, prior = knobs or stored()["ratings"], prior or {}
    k, edge = knobs["prior_games"], knobs["home"] / 2
    rows = [(team, opp, sign, knobs["decay"] ** (week - 1 - w), float(pts))
            for w, home, away, hp, ap in games if w < week
            for team, opp, sign, pts in ((home, away, 1.0, hp), (away, home, -1.0, ap))]
    weight = sum(r[3] for r in rows)
    level = (sum(r[3] * r[4] for r in rows) + knobs["level_games"] * knobs["level"]) / (weight + knobs["level_games"])
    teams = sorted({r[0] for r in rows} | set(prior))
    off = {t: prior.get(t, Rating()).offense for t in teams}
    dfn = {t: prior.get(t, Rating()).defense for t in teams}
    for _ in range(ITERATIONS if rows else 0):
        sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for team, opp, sign, w, pts in rows:
            sums[team][0] += w * (pts - level - dfn[opp] - sign * edge)
            sums[team][1] += w
        off = {t: (sums[t][0] + k * prior.get(t, Rating()).offense) / (sums[t][1] + k) for t in teams}
        sums = defaultdict(lambda: [0.0, 0.0])
        for team, opp, sign, w, pts in rows:
            sums[opp][0] += w * (pts - level - off[team] - sign * edge)
            sums[opp][1] += w
        dfn = {t: (sums[t][0] + k * prior.get(t, Rating()).defense) / (sums[t][1] + k) for t in teams}
    return Rated(level, MappingProxyType({t: Rating(off[t], dfn[t]) for t in teams}))


def carry_over(games: Collection[tuple[int, str, str, int, int]], knobs: Mapping | None = None) -> dict[str, Rating]:
    """Next season's prior from a season's scores (`scores`): its final ratings x `carry` (toward the league)."""
    knobs = knobs or stored()["ratings"]
    rated = ratings(games, max((g[0] for g in games), default=0) + 1, None, knobs)
    return {t: Rating(r.offense * knobs["carry"], r.defense * knobs["carry"]) for t, r in rated.teams.items()}


def summary(world: World, home: str, away: str | None, out: Collection[int]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """((home, away) mean points, (home, away) starting QB quality) under `world` as it stands; `away` None:
    `home` alone against a league-average opponent (one-element tuples). One side built per team."""
    league = params()["league"]
    teams = [home] if away is None else [home, away]
    sides = [_side_of(world, team, (away if team == home else home), out) for team in teams]
    means = [_side_means(side) for side in sides]
    points = tuple(_mean_points(m, means[1 - i] if len(means) == 2 else None) for i, m in enumerate(means))
    quality = tuple(world.players[s.qb].quality if s.qb is not None else league["backup_quality"] for s in sides)
    return points, quality


def rest_days(known: SeasonData, team: str, week: int) -> int | None:
    """Days since the team's last game before `week` (None: no earlier game or no dates)."""
    now = known.schedule.game(team, week)
    before = [g for g in known.schedule.games if g.week < week and team in (g.home_team, g.away_team)]
    if now is None or not now.gameday or not before or not before[-1].gameday:
        return None
    try:
        return (date.fromisoformat(now.gameday) - date.fromisoformat(before[-1].gameday)).days
    except ValueError:
        return None


def _rest(days: int | None) -> float:
    if days is None:
        return 0.0
    return 1.0 if days >= BYE_DAYS else -1.0 if days <= SHORT_DAYS else 0.0


def features(rated: Rated, home: str, away: str, belief_points: tuple[float, float],
             rest: tuple[int | None, int | None],
             qbs: tuple[tuple[float, float], tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """{input: (margin part, total part)} of one game: each side's value minus / plus the other's. `qbs`: each
    side's (starting QB quality, its usual passer's: World.teams[team].passer)."""
    h, a = rated.teams.get(home, Rating()), rated.teams.get(away, Rating())
    mine, theirs = h.offense + a.defense, a.offense + h.defense
    (hq, hp), (aq, ap) = qbs
    return {"points": (mine - theirs, mine + theirs + 2 * rated.level),
            "belief": (belief_points[0] - belief_points[1], belief_points[0] + belief_points[1]),
            "qb_level": (hq - aq, hq + aq),
            "qb_change": ((hq - hp) - (aq - ap), (hq - hp) + (aq - ap)),
            "rest": (_rest(rest[0]) - _rest(rest[1]), 0.0)}


def predict(parts: Mapping[str, tuple[float, float]], model: Mapping | None = None) -> Line:
    """The line from `features` and the fitted coefficients ({"margin" | "total": {"const": c, input: b}})."""
    model = model or stored()
    margin = model["margin"]["const"] + sum(b * parts[name][0] for name, b in model["margin"].items() if name != "const")
    total = model["total"]["const"] + sum(b * parts[name][1] for name, b in model["total"].items() if name != "const")
    return Line(round(margin, 2), round(max(MIN_TOTAL, total), 2))


def week_lines(known: SeasonData, week: int, belief: World, out: Collection[int],
               prior: Mapping[str, Rating] | None = None,
               posted: Mapping[tuple[str, int], tuple[float | None, float | None]] | None = None) -> dict[str, Line]:
    """{home team: Line} for every game of `week` (see the module docstring). Pure; of `known` it reads only the
    scores before `week` and the schedule, plus the public `belief` of that week and `out` (its hard-tagged
    players)."""
    rated = ratings(scores(known, week), week, prior)
    lines = {}
    for game in known.schedule.games:
        if game.week != week or game.home_team not in belief.teams or game.away_team not in belief.teams:
            continue
        home, away = game.home_team, game.away_team
        points, quality = summary(belief, home, away, out)
        model = predict(features(rated, home, away, points, (rest_days(known, home, week), rest_days(known, away, week)),
                                 ((quality[0], belief.teams[home].passer), (quality[1], belief.teams[away].passer))))
        spread, total = _posted((posted or {}).get((home, week)), home, week)
        lines[home] = Line(model.margin if spread is None else spread,
                           model.total if total is None else max(MIN_TOTAL, total), (points[0], points[1]))
    return lines


def _posted(line: tuple | None, home: str, week: int) -> tuple[float | None, float | None]:
    """(spread, total) of a posted line, each None when missing or outside POSTED_RANGE (logged)."""
    out = []
    for name, value in zip(("spread", "total"), line or (None, None)):
        low, high = POSTED_RANGE[name]
        if value is not None and not low <= float(value) <= high:          # NaN fails too
            log.warning("Posted %s %s for %s in week %s is out of range: the model's is used", name, value, home, week)
            value = None
        out.append(None if value is None else float(value))
    return out[0], out[1]


def edges(known: SeasonData, week: int, belief: World, out: Collection[int],
          prior: Mapping[str, Rating] | None = None) -> dict[str, float]:
    """{team: points its line adds to the belief's own mean}, against a league-average opponent on a neutral
    field (the line model with the other side at the league's level: rating 0, an average QB, no home edge,
    no rest edge). What the public's rest-of-season values add for the team's scoring (sim_market.values)."""
    rated = ratings(scores(known, week), week, prior)
    home_edge = stored()["margin"]["const"]
    out_edges = {}
    for team in sorted(belief.teams):
        (mine,), (quality,) = summary(belief, team, None, out)
        parts = features(rated, team, "", (mine, rated.level), (None, None),
                         ((quality, belief.teams[team].passer), (1.0, 1.0)))
        line = predict(parts)
        out_edges[team] = (line.total + line.margin - home_edge) / 2 - mine
    return out_edges


def write(model: Mapping) -> None:
    LINES_PATH.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8")
    stored.cache_clear()
