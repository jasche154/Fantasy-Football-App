"""Replay League custom scoring: a league's key settings on top of a preset, and what they do to points.

`ScoringRules` holds the four settings a league may change: points per reception (0-1.5 in steps of 0.25),
the TE premium (0, 0.5 or 1 more per TE catch), points per passing TD (4 or 6) and the turnover penalty
(INT and fumble lost: -1 or -2). A league whose rules equal a preset is stored as that preset with no rules
(`normalize`), so presets keep reading the stored columns and give exactly the numbers they always did.

Played games are exact: `scoring_settings` gives the full ScoringSettings and every real point is
recomputed from the box score. Pre-game projections are stored only as pts_ppr / pts_half / pts_std, so
under custom rules they are an ESTIMATE (`convert`):

    catches   = pts_ppr - pts_std                       (the sites' implied receptions)
    points    = pts_std + (reception + TE premium if a TE) x catches
              + QB only: (pass_td - 4) x expected passing TDs + (turnover + 2) x expected INTs

A QB's expected passing TDs (and INTs) = that week's pts_std x his rate per standard point, where the rate is
his season-to-date passing TDs (INTs) over his standard points from the box scores of weeks BEFORE that
week, shrunk toward a league QB rate (QB_TD_PER_POINT, QB_INT_PER_POINT) worth PRIOR_POINTS of his own: no
later week is ever read. Fumbles lost are not in the box lines, so the projection ignores their change
(a fraction of a point per season for most players).
"""
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace

from services.replay_box import BoxLine
from services.scoring import POINTS_PER_RECEPTION, ScoringSettings, scoring_for

RECEPTION_MAX, RECEPTION_STEP = 1.5, 0.25
TE_PREMIUMS = (0.0, 0.5, 1.0)
PASS_TDS = (4.0, 6.0)
TURNOVERS = (-1.0, -2.0)
BASE_PASS_TD, BASE_TURNOVER = 4.0, -2.0     # what pts_std (and every preset) assumes
QB_TD_PER_POINT = 0.09        # passing TDs per standard point, an average starting QB (about 1.5 on 16.5 points)
QB_INT_PER_POINT = 0.05       # interceptions per standard point (about 0.8 a game)
PRIOR_POINTS = 60.0           # the league rate weighs as much as this many of his own standard points (3-4 starts)

PlayerWeek = tuple[int, int]


@dataclass(frozen=True)
class ScoringRules:
    reception: float = 0.5
    te_premium: float = 0.0
    pass_td: float = BASE_PASS_TD
    turnover: float = BASE_TURNOVER


def preset_rules(scoring: str) -> ScoringRules:
    return ScoringRules(reception=POINTS_PER_RECEPTION[scoring])


def problem(rules: ScoringRules) -> str | None:
    """Why these rules are not allowed, or None."""
    steps = rules.reception / RECEPTION_STEP
    if not 0 <= rules.reception <= RECEPTION_MAX or steps != round(steps):
        return f"Points per reception must be 0 to {RECEPTION_MAX} in steps of {RECEPTION_STEP}."
    if rules.te_premium not in TE_PREMIUMS:
        return f"The TE premium must be one of {list(TE_PREMIUMS)}."
    if rules.pass_td not in PASS_TDS:
        return f"A passing TD is worth {PASS_TDS[0]:g} or {PASS_TDS[1]:g} points."
    if rules.turnover not in TURNOVERS:
        return f"The turnover penalty must be {TURNOVERS[0]:g} or {TURNOVERS[1]:g}."
    return None


def normalize(scoring: str, rules: ScoringRules | None) -> tuple[str, ScoringRules | None]:
    """(preset, rules): rules that equal a preset become that preset with no rules (exact stored columns)."""
    if rules is None:
        return scoring, None
    same = [preset for preset in (scoring, *POINTS_PER_RECEPTION) if preset_rules(preset) == rules]
    return (same[0], None) if same else (scoring, rules)


def scoring_settings(scoring: str, rules: ScoringRules | None) -> ScoringSettings:
    """The full scoring for real points: the preset's, with the custom rules on top."""
    if rules is None:
        return scoring_for(scoring)
    return replace(ScoringSettings(), reception=rules.reception, te_premium=rules.te_premium,
                   pass_td=rules.pass_td, interception=rules.turnover, fumble_lost=rules.turnover)


def box_std_points(line: BoxLine) -> float:
    """Standard points of a box line (no fumbles or two-point plays: the box does not hold them)."""
    return (line.pass_yds * 0.04 + line.pass_td * BASE_PASS_TD + line.pass_int * BASE_TURNOVER
            + (line.rush_yds + line.rec_yds) * 0.1 + (line.rush_td + line.rec_td) * 6)


def _qb_rates(box: Mapping[PlayerWeek, BoxLine], positions: Mapping[int, str]):
    """rate(player, week) -> (passing TDs, INTs) per standard point from his games before `week` only."""
    games: dict[int, list[tuple[int, BoxLine]]] = defaultdict(list)
    for (pid, week), line in box.items():
        if positions.get(pid) == "QB":
            games[pid].append((week, line))

    def rate(pid: int, week: int) -> tuple[float, float]:
        played = [line for game_week, line in games.get(pid, ()) if game_week < week]
        points = max(1.0, PRIOR_POINTS + sum(box_std_points(line) for line in played))
        tds = PRIOR_POINTS * QB_TD_PER_POINT + sum(line.pass_td for line in played)
        ints = PRIOR_POINTS * QB_INT_PER_POINT + sum(line.pass_int for line in played)
        return tds / points, ints / points
    return rate


def convert(raw: Mapping[PlayerWeek, tuple[float, float]], positions: Mapping[int, str],
            box: Mapping[PlayerWeek, BoxLine], rules: ScoringRules) -> dict[PlayerWeek, float]:
    """{(player, week): estimated projection under `rules`} from {(player, week): (pts_ppr, pts_std)}; only
    values above zero are kept (as the stored projections are). See the module docstring for the formula."""
    rate = _qb_rates(box, positions)
    out: dict[PlayerWeek, float] = {}
    for (pid, week), (ppr, std) in raw.items():
        position = positions.get(pid)
        catches = max(0.0, ppr - std)
        points = std + (rules.reception + (rules.te_premium if position == "TE" else 0.0)) * catches
        if position == "QB" and (rules.pass_td != BASE_PASS_TD or rules.turnover != BASE_TURNOVER):
            tds, ints = rate(pid, week)
            points += ((rules.pass_td - BASE_PASS_TD) * tds + (rules.turnover - BASE_TURNOVER) * ints) * max(0.0, std)
        if points > 0:
            out[(pid, week)] = round(points, 2)
    return out
