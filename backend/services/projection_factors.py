"""Calibration, EPA matchup and weather adjustments to the model's weekly projection.

    model points x factor,   factor = 1 + b_pos . x   (kept within FACTOR_LIMITS)

x, per player-week (all known before the game):
- calibration: 1 (a per-position level correction; the raw model runs high)
- opp_pass_epa, opp_rush_epa: the opponent defense's EPA per dropback / per run allowed,
  minus the league average (x10), from team_game_stats before the week
- opp_sack: the opponent's sacks per dropback minus the league average (x20)
- wind: mph above 10, outdoors (/10); cold: degrees below 40 F, outdoors (/20); indoors: 1 for a dome
  or closed roof

How it was chosen (python -m services.projection_factors --fit, the leave-season-out report):
fit by least squares on backtest player-weeks from earlier seasons only, then scored on a
season the fit never saw. Least squares keeps projections as expected points; a median
fit would "improve" MAE just by shading every projection down. Candidates tested and
dropped because they didn't help in both test seasons: red-zone role (carries + targets
inside the 10/20; expected points already carry expected TDs), game script (spread) and
the game total (Vegas implied totals already price both).

The EPA terms come out slightly negative: schedule strength and the Vegas implied total
already tilt the model toward easy matchups, and the data says that tilt overshoots a
little, so EPA allowed pulls it back. Hence "matchup fine-tune" in explanations.

The factor applies to the model source only. Props and experts already price matchups and
weather, and their levels aren't biased the same way.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from services.game_features import Conditions, TeamProfiles

log = logging.getLogger(__name__)
FACTORS_PATH = Path(__file__).with_name("projection_factors.json")
FEATURES = ("calibration", "opp_pass_epa", "opp_rush_epa", "opp_sack", "wind", "cold", "indoors")
FACTOR_LIMITS = (0.75, 1.20)
RIDGE = 20.0
POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True)
class FactorInputs:
    opp_pass_epa: float = 0.0
    opp_rush_epa: float = 0.0
    opp_sack: float = 0.0
    wind: float = 0.0
    cold: float = 0.0
    indoors: float = 0.0

    def vector(self) -> list[float]:
        return [1.0, self.opp_pass_epa * 10, self.opp_rush_epa * 10, self.opp_sack * 20,
                self.wind / 10, self.cold / 20, self.indoors]


def inputs_for(profiles: TeamProfiles, opponent: str | None, conditions: Conditions) -> FactorInputs:
    league, opp = profiles.league, profiles.get(opponent)
    return FactorInputs(opp_pass_epa=opp.pass_epa_allowed - league.pass_epa_per_db,
                        opp_rush_epa=opp.rush_epa_allowed - league.rush_epa_per_play,
                        opp_sack=opp.sack_rate - league.sack_rate,
                        wind=conditions.wind_over, cold=conditions.cold, indoors=float(conditions.indoors))


@lru_cache(maxsize=1)
def coefficients() -> dict[str, list[float]]:
    try:
        data = json.loads(FACTORS_PATH.read_text(encoding="utf-8"))["positions"]
        return {pos: [float(data[pos][f]) for f in FEATURES] for pos in data}
    except (OSError, ValueError, KeyError):
        return {}


def factor(position: str | None, inputs: FactorInputs) -> float:
    """The multiplier for the model's projection (1.0 when no fitted coefficients exist)."""
    beta = coefficients().get(position or "")
    if not beta:
        return 1.0
    low, high = FACTOR_LIMITS
    return min(high, max(low, 1.0 + float(np.dot(beta, inputs.vector()))))


def describe(position: str | None, inputs: FactorInputs) -> str:
    """Plain-English reasons for the factor, largest effects first."""
    beta = coefficients().get(position or "")
    if not beta:
        return "No fitted adjustments yet."
    names = {"calibration": "model calibration", "opp_pass_epa": "matchup fine-tune (pass EPA allowed)",
             "opp_rush_epa": "matchup fine-tune (run EPA allowed)", "opp_sack": "opponent pass rush",
             "wind": "wind", "cold": "cold", "indoors": "indoors"}
    parts = sorted(((b * x, names[f]) for b, x, f in zip(beta, inputs.vector(), FEATURES) if abs(b * x) >= 0.005),
                   key=lambda item: -abs(item[0]))
    return "; ".join(f"{name} {effect * 100:+.0f}%" for effect, name in parts) or "No notable adjustments."


# ---------------- fitting (offline) ----------------

def collect_rows(session, seasons) -> list[dict]:
    """Backtest player-weeks with factor inputs, each from data before its week."""
    from sqlalchemy import select

    from database.models import NflGame
    from services import backtest as bt
    from services.game_features import conditions_for, team_profiles
    from services.weekly_model import WeeklyModelSettings

    rows = []
    for season in seasons:
        data = bt.load_season(session, season)
        games = {}
        for g in session.scalars(select(NflGame).where(NflGame.season == season, NflGame.game_type == "REG")):
            games[(g.week, g.home_team)] = (g, g.away_team)
            games[(g.week, g.away_team)] = (g, g.home_team)
        for week in range(bt.FIRST_WEEK, bt.LAST_WEEK + 1):
            targets = bt.project_week(session, data, week, WeeklyModelSettings(use_factors=False))
            before = {p.player_id: p for p in data.cache[("before", week)]}
            profiles = team_profiles(session, season, week)
            for t in targets:
                game, opponent = games.get((week, before[t.player_id].team), (None, None))
                if game is None:
                    continue
                rows.append({"season": season, "pos": t.position, "actual": t.actual, "projected": t.projected,
                             "if_active": t.if_active, "played": t.played, "week": week,
                             "x": inputs_for(profiles, opponent, conditions_for(game)).vector()})
    return rows


def fit(rows: list[dict]) -> dict[str, list[float]]:
    out = {}
    for pos in POSITIONS:
        train = [r for r in rows if r["pos"] == pos and r["played"] and r["if_active"] > 0]
        if len(train) < 200:
            continue
        x = np.array([[r["if_active"] * v for v in r["x"]] for r in train])
        y = np.array([r["actual"] - r["if_active"] for r in train])
        out[pos] = [float(b) for b in np.linalg.solve(x.T @ x + RIDGE * np.eye(x.shape[1]), x.T @ y)]
    return out


def _metrics(rows: list[dict], preds: list[float]) -> dict:
    actual, pred = np.array([r["actual"] for r in rows]), np.array(preds)
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[(r["season"], r["week"], r["pos"])].append(i)
    rhos = [np.corrcoef(np.argsort(np.argsort(actual[i])), np.argsort(np.argsort(pred[i])))[0, 1]
            for i in groups.values() if len(i) >= 8]
    return {"rmse": round(float(np.sqrt(np.mean((actual - pred) ** 2))), 3),
            "mae": round(float(np.mean(np.abs(actual - pred))), 3), "rank_corr": round(float(np.nanmean(rhos)), 3)}


def _apply(rows: list[dict], coefs: dict[str, list[float]]) -> list[float]:
    preds = []
    low, high = FACTOR_LIMITS
    for r in rows:
        beta = coefs.get(r["pos"])
        f = min(high, max(low, 1.0 + float(np.dot(beta, r["x"])))) if beta else 1.0
        preds.append(r["projected"] * f)
    return preds


def leave_season_out(rows: list[dict], tests) -> dict:
    report = {}
    for test in tests:
        train = [r for r in rows if r["season"] < test]
        held = [r for r in rows if r["season"] == test]
        report[str(test)] = {"before": _metrics(held, [r["projected"] for r in held]),
                             "after": _metrics(held, _apply(held, fit(train)))}
    return report


def main(argv: list[str] | None = None) -> None:
    from sqlalchemy.orm import Session

    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="refit and write projection_factors.json")
    parser.add_argument("--seasons", type=int, nargs="+", default=[2021, 2022, 2023, 2024, 2025])
    args = parser.parse_args(argv)
    with Session(engine) as session:
        rows = collect_rows(session, args.seasons)
    report = leave_season_out(rows, [s for s in args.seasons[-2:]])
    for season, result in report.items():
        log.info("held-out %s: before %s -> after %s", season, result["before"], result["after"])
    if args.fit:
        coefs = fit(rows)
        FACTORS_PATH.write_text(json.dumps({
            "seasons": args.seasons, "features": list(FEATURES), "held_out": report,
            "positions": {pos: dict(zip(FEATURES, (round(b, 5) for b in beta))) for pos, beta in coefs.items()},
        }, indent=2), encoding="utf-8")
        coefficients.cache_clear()
        log.info("wrote %s", FACTORS_PATH)


if __name__ == "__main__":
    main()
