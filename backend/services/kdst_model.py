"""Kicker and team-defense (D/ST) weekly projections.

Kicker = expected field goals by distance x make probability x points, plus extra points:
- FG attempts: a least-squares fit on past team-games of the team's Vegas implied total,
  its red-zone TD rate (stalled drives become field goals) and the opponent's red-zone TD
  rate allowed (1 - red-zone stop %). The team's 4th-down go rate was tested and left out:
  it didn't predict attempts beyond the implied total in either held-out season.
- Distance mix: the league's split of attempts into 0-39 / 40-49 / 50+ yards.
- Make probability: the kicker's own accuracy in each band this season and last, shrunk
  toward the league rate with ACCURACY_PRIOR attempts, then lowered for wind and cold
  outdoors (fit on past kicks).
- Extra points: expected touchdowns (fit on the implied total) x the kicker's PAT rate.

Defense = sacks, takeaways, TDs, safeties, blocks, and points allowed:
- Sacks: the opponent's expected dropbacks x a log5 blend of this defense's sack rate and
  the opponent's sacks allowed per dropback (relative to the league), then a fitted scale.
- Takeaways: this defense's takeaways per game x the opponent's giveaways per game / league.
- Defensive / return TDs: the league rate per takeaway, plus a small return-TD base.
- Points allowed: the opponent's Vegas implied total plus the historical distribution of
  (actual - implied), scored on the league's points-allowed tiers and averaged. The
  defense's EPA per play allowed was tested as an extra input and left out: it helped one
  held-out season and hurt the other (Vegas already prices it).

Every team rate comes from game_features.team_profiles (data before the week, shrunk toward
the league). Coefficients are fit by `python -m services.kdst_model --fit` on earlier
seasons and checked on held-out seasons against season-to-date averages.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from statistics import mean

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import KickerGame, NflGame, TeamGameStats
from services.game_features import Conditions, TeamProfiles, conditions_for, team_profiles

log = logging.getLogger(__name__)
COEF_PATH = Path(__file__).with_name("kdst_coefficients.json")
BANDS = ("0_39", "40_49", "50")
ACCURACY_PRIOR = 8                    # league-average attempts mixed into each kicker band
PAT_PRIOR = 20

# Yahoo's default scoring (league settings can override these by Yahoo stat id).
DEFAULT_K = {"0_39": 3.0, "40_49": 4.0, "50": 5.0, "miss_0_39": 0.0, "miss_40_49": 0.0, "miss_50": 0.0,
             "pat": 1.0, "pat_miss": 0.0}
DEFAULT_DST = {"sack": 1.0, "int": 2.0, "fum": 2.0, "td": 6.0, "safety": 2.0, "block": 2.0,
               "pa_0": 10.0, "pa_1_6": 7.0, "pa_7_13": 4.0, "pa_14_20": 1.0, "pa_21_27": 0.0,
               "pa_28_34": -1.0, "pa_35": -4.0}
# Yahoo stat ids -> the keys above.
YAHOO_K = {21: "0_39", 22: "40_49", 23: "50", 26: "miss_0_39", 27: "miss_40_49", 28: "miss_50", 29: "pat",
           30: "pat_miss"}
YAHOO_DST = {32: "sack", 33: "int", 34: "fum", 35: "td", 36: "safety", 37: "block", 50: "pa_0", 51: "pa_1_6",
             52: "pa_7_13", 53: "pa_14_20", 54: "pa_21_27", 55: "pa_28_34", 56: "pa_35"}


def pa_points(points_allowed: float, scoring: Mapping[str, float]) -> float:
    pa = round(points_allowed)
    for limit, key in ((0, "pa_0"), (6, "pa_1_6"), (13, "pa_7_13"), (20, "pa_14_20"), (27, "pa_21_27"),
                       (34, "pa_28_34")):
        if pa <= limit:
            return scoring[key]
    return scoring["pa_35"]


@dataclass(frozen=True)
class KickerProjection:
    team: str
    opponent: str
    player_id: int | None
    points: float
    fg_attempts: float
    fg_made: float
    pat_made: float
    accuracy: Mapping[str, float]
    conditions: Conditions
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DefenseProjection:
    team: str
    opponent: str
    points: float
    sacks: float
    takeaways: float
    tds: float
    points_allowed: float
    points_allowed_score: float
    reasons: tuple[str, ...]


@lru_cache(maxsize=1)
def coefficients() -> dict:
    try:
        return json.loads(COEF_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------- features (shared by fitting and projecting) ----------------

def kicker_features(profiles: TeamProfiles, team: str, opponent: str, implied: float) -> list[float]:
    own, opp, lg = profiles.get(team), profiles.get(opponent), profiles.league
    return [1.0, implied, own.rz_td_rate - lg.rz_td_rate, opp.rz_td_allowed - lg.rz_td_rate]


def sacks_expected(profiles: TeamProfiles, defense: str, offense: str) -> float:
    d, o, lg = profiles.get(defense), profiles.get(offense), profiles.league
    rate = d.sack_rate * o.sacks_taken_per_db / lg.sack_rate if lg.sack_rate else d.sack_rate
    return o.dropbacks_pg * rate


def takeaways_expected(profiles: TeamProfiles, defense: str, offense: str) -> float:
    d, o, lg = profiles.get(defense), profiles.get(offense), profiles.league
    return d.takeaways_pg * o.giveaways_pg / lg.giveaways_pg if lg.giveaways_pg else d.takeaways_pg


def _kick_rates(rows: list[KickerGame]) -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
    bands = {b: [0, 0] for b in BANDS}
    pat = [0, 0]
    for r in rows:
        for band in BANDS:
            made, missed = getattr(r, f"made_{band}"), getattr(r, f"missed_{band}")
            bands[band][0] += made
            bands[band][1] += made + missed
        pat[0] += r.pat_made
        pat[1] += r.pat_att
    return {b: (v[0], v[1]) for b, v in bands.items()}, (pat[0], pat[1])


# ---------------- projection ----------------

@dataclass
class WeekInputs:
    """Everything the K/DST projections need for one week, built from data before it."""
    season: int
    week: int
    profiles: TeamProfiles
    games: dict[str, NflGame]
    kickers: dict[str, int]                                    # team -> its current kicker's player id
    kicker_rows: dict[int, list[KickerGame]] = field(default_factory=dict)
    league_bands: dict[str, float] = field(default_factory=dict)  # make rate per band
    league_mix: dict[str, float] = field(default_factory=dict)    # share of attempts per band
    league_pat: float = 0.94
    forecast: Mapping[str, Conditions] = field(default_factory=dict)


def week_inputs(session: Session, season: int, week: int, forecast: Mapping[str, Conditions] | None = None) -> WeekInputs:
    profiles = team_profiles(session, season, week)
    games = {}
    for g in session.scalars(select(NflGame).where(NflGame.season == season, NflGame.week == week)):
        games[g.home_team] = games[g.away_team] = g
    recent = list(session.scalars(select(KickerGame).where(
        ((KickerGame.season == season) & (KickerGame.week < week)) | (KickerGame.season == season - 1))))
    kickers: dict[str, tuple[tuple[int, int], int]] = {}
    by_player: dict[int, list[KickerGame]] = defaultdict(list)
    for r in recent:
        by_player[r.player_id].append(r)
        if r.season == season and r.team and (r.team not in kickers or (r.season, r.week) > kickers[r.team][0]):
            kickers[r.team] = ((r.season, r.week), r.player_id)
    bands, pat = _kick_rates(recent)
    attempts = sum(a for _, a in bands.values()) or 1
    return WeekInputs(
        season=season, week=week, profiles=profiles, games=games,
        kickers={team: pid for team, (_, pid) in kickers.items()}, kicker_rows=by_player,
        league_bands={b: (m / a if a else 0.8) for b, (m, a) in bands.items()},
        league_mix={b: a / attempts for b, (_, a) in bands.items()},
        league_pat=pat[0] / pat[1] if pat[1] else 0.94, forecast=forecast or {})


def _game_side(inputs: WeekInputs, team: str) -> tuple[NflGame | None, str | None, float | None]:
    game = inputs.games.get(team)
    if game is None:
        return None, None, None
    opponent = game.away_team if game.home_team == team else game.home_team
    implied = None
    if game.spread_line is not None and game.total_line is not None:
        implied = (game.total_line + (1 if team == game.home_team else -1) * game.spread_line) / 2
    return game, opponent, implied


def project_kicker(inputs: WeekInputs, team: str, scoring: Mapping[str, float] = DEFAULT_K) -> KickerProjection | None:
    coefs = coefficients().get("kicker")
    game, opponent, implied = _game_side(inputs, team)
    if game is None or not coefs:
        return None
    implied = implied if implied is not None else inputs.profiles.get(team).points_pg
    fga = max(0.3, float(np.dot(coefs["fga"], kicker_features(inputs.profiles, team, opponent, implied))))
    tds = max(0.5, coefs["tds"][0] + coefs["tds"][1] * implied)
    conditions = conditions_for(game, inputs.forecast)
    player_id = inputs.kickers.get(team)
    own_bands, own_pat = _kick_rates(inputs.kicker_rows.get(player_id, []))
    accuracy = {}
    for band in BANDS:
        made, att = own_bands[band]
        base = (made + ACCURACY_PRIOR * inputs.league_bands[band]) / (att + ACCURACY_PRIOR)
        penalty = coefs["wind"][band] * conditions.wind_over / 10 + coefs["cold"][band] * conditions.cold / 20
        accuracy[band] = min(0.99, max(0.3, base - penalty))
    pat_rate = (own_pat[0] + PAT_PRIOR * inputs.league_pat) / (own_pat[1] + PAT_PRIOR)
    points = sum(fga * inputs.league_mix[b] * (accuracy[b] * scoring[b] + (1 - accuracy[b]) * scoring[f"miss_{b}"])
                 for b in BANDS)
    points += tds * (pat_rate * scoring["pat"] + (1 - pat_rate) * scoring["pat_miss"])
    fg_made = sum(fga * inputs.league_mix[b] * accuracy[b] for b in BANDS)
    own, lg = inputs.profiles.get(team), inputs.profiles.league
    reasons = [f"Vegas implied total {implied:.1f}", f"{fga:.1f} expected field-goal tries",
               f"red-zone TD rate {own.rz_td_rate:.0%} (league {lg.rz_td_rate:.0%})",
               f"opponent red-zone stop rate {1 - inputs.profiles.get(opponent).rz_td_allowed:.0%}",
               "accuracy " + ", ".join(f"{b.replace('_', '-')}: {accuracy[b]:.0%}" for b in BANDS)]
    if conditions.wind_over or conditions.cold:
        reasons.append(f"forecast {conditions.wind or 0:.0f} mph wind, {conditions.temp or 0:.0f}F")
    return KickerProjection(team, opponent, player_id, round(points, 2), round(fga, 2), round(fg_made, 2),
                            round(tds * pat_rate, 2), accuracy, conditions, tuple(reasons))


def project_defense(inputs: WeekInputs, team: str, scoring: Mapping[str, float] = DEFAULT_DST) -> DefenseProjection | None:
    coefs = coefficients().get("defense")
    game, opponent, _ = _game_side(inputs, team)
    if game is None or not coefs:
        return None
    _, _, opp_implied = _game_side(inputs, opponent)
    opp_implied = opp_implied if opp_implied is not None else inputs.profiles.get(opponent).points_pg
    sacks = max(0.0, coefs["sacks"][0] + coefs["sacks"][1] * sacks_expected(inputs.profiles, team, opponent))
    takeaways = max(0.0, coefs["takeaways"][0] + coefs["takeaways"][1] * takeaways_expected(inputs.profiles, team, opponent))
    tds = coefs["td_base"] + coefs["td_per_takeaway"] * takeaways
    residuals = coefs["pa_residuals"]
    pa_score = mean(pa_points(max(0.0, opp_implied + r), scoring) for r in residuals)
    int_share = coefs["int_share"]
    points = (sacks * scoring["sack"] + takeaways * (int_share * scoring["int"] + (1 - int_share) * scoring["fum"])
              + tds * scoring["td"] + coefs["safety_rate"] * scoring["safety"] + coefs["block_rate"] * scoring["block"]
              + pa_score)
    d, o, lg = inputs.profiles.get(team), inputs.profiles.get(opponent), inputs.profiles.league
    reasons = (f"opponent implied total {opp_implied:.1f}",
               f"sack rate {d.sack_rate:.1%} vs opponent allowing {o.sacks_taken_per_db:.1%} (league {lg.sack_rate:.1%})",
               f"takeaways {d.takeaways_pg:.1f}/game vs opponent giveaways {o.giveaways_pg:.1f}/game")
    return DefenseProjection(team, opponent, round(points, 2), round(sacks, 2), round(takeaways, 2), round(tds, 3),
                             round(opp_implied, 1), round(pa_score, 2), reasons)


# ---------------- fitting (offline) ----------------

def _actual_k(row: KickerGame, scoring: Mapping[str, float] = DEFAULT_K) -> float:
    return (sum(getattr(row, f"made_{b}") * scoring[b] + getattr(row, f"missed_{b}") * scoring[f"miss_{b}"]
                for b in BANDS) + row.pat_made * scoring["pat"] + (row.pat_att - row.pat_made) * scoring["pat_miss"])


def _actual_dst(row: TeamGameStats, scoring: Mapping[str, float] = DEFAULT_DST) -> float:
    return (row.def_sacks * scoring["sack"] + row.def_ints * scoring["int"] + row.def_fumble_rec * scoring["fum"]
            + row.def_tds * scoring["td"] + row.def_safeties * scoring["safety"] + row.def_blocks * scoring["block"]
            + pa_points(row.points_against or 0, scoring))


def collect(session: Session, seasons) -> list[dict]:
    """Team-game rows with features as of each week, plus what happened."""
    rows = []
    for season in seasons:
        stats = {(r.week, r.team): r for r in session.scalars(select(TeamGameStats).where(TeamGameStats.season == season))}
        kicks = defaultdict(list)
        for r in session.scalars(select(KickerGame).where(KickerGame.season == season)):
            kicks[(r.week, r.team)].append(r)
        for week in range(3, 19):
            inputs = week_inputs(session, season, week)
            for team, game in inputs.games.items():
                own = stats.get((week, team))
                if own is None:
                    continue
                _, opponent, implied = _game_side(inputs, team)
                _, _, opp_implied = _game_side(inputs, opponent)
                if implied is None or opp_implied is None:
                    continue
                rows.append({
                    "season": season, "week": week, "team": team, "opponent": opponent, "implied": implied,
                    "opp_implied": opp_implied, "kicker_x": kicker_features(inputs.profiles, team, opponent, implied),
                    "sack_x": sacks_expected(inputs.profiles, team, opponent),
                    "take_x": takeaways_expected(inputs.profiles, team, opponent),
                    "conditions": conditions_for(game), "fga": own.fg_att, "pat_att": own.pat_att,
                    "tds": own.pat_att + 0.0, "sacks": own.def_sacks, "takeaways": own.def_ints + own.def_fumble_rec,
                    "ints": own.def_ints, "def_tds": own.def_tds, "safeties": own.def_safeties, "blocks": own.def_blocks,
                    "points_against": own.points_against, "dst_points": _actual_dst(own),
                    "k_points": sum(_actual_k(k) for k in kicks.get((week, team), [])) if kicks.get((week, team)) else None,
                    "kicks": kicks.get((week, team), []), "inputs": inputs,
                })
    return rows


def _lstsq(x, y, ridge: float = 1.0) -> list[float]:
    x, y = np.array(x), np.array(y)
    return [float(b) for b in np.linalg.solve(x.T @ x + ridge * np.eye(x.shape[1]), x.T @ y)]


def fit(rows: list[dict]) -> dict:
    """Coefficients from these rows (call with earlier seasons only to test honestly)."""
    takeaways = sum(r["takeaways"] for r in rows) or 1
    wind = {b: 0.0 for b in BANDS}
    cold = {b: 0.0 for b in BANDS}
    # Weather: make-rate drop per 10 mph over 10 / per 20 F under 40, from outdoor kicks.
    for band in BANDS:
        calm, windy, warm, cool = [0, 0], [0, 0], [0, 0], [0, 0]
        for r in rows:
            for k in r["kicks"]:
                made, att = getattr(k, f"made_{band}"), getattr(k, f"made_{band}") + getattr(k, f"missed_{band}")
                target = windy if r["conditions"].wind_over >= 5 else calm
                target[0] += made
                target[1] += att
                target = cool if r["conditions"].cold >= 10 else warm
                target[0] += made
                target[1] += att
        if windy[1] >= 30 and calm[1]:
            wind[band] = max(0.0, (calm[0] / calm[1] - windy[0] / windy[1]) / 1.0)
        if cool[1] >= 30 and warm[1]:
            cold[band] = max(0.0, (warm[0] / warm[1] - cool[0] / cool[1]) / 1.0)
    return {
        "kicker": {"fga": _lstsq([r["kicker_x"] for r in rows], [r["fga"] for r in rows]),
                   "tds": _lstsq([[1.0, r["implied"]] for r in rows], [r["tds"] for r in rows]),
                   "wind": wind, "cold": cold},
        "defense": {"sacks": _lstsq([[1.0, r["sack_x"]] for r in rows], [r["sacks"] for r in rows]),
                    "takeaways": _lstsq([[1.0, r["take_x"]] for r in rows], [r["takeaways"] for r in rows]),
                    "int_share": sum(r["ints"] for r in rows) / takeaways,
                    "td_per_takeaway": sum(r["def_tds"] for r in rows) / takeaways * 0.8,
                    "td_base": sum(r["def_tds"] for r in rows) / len(rows) * 0.2,
                    "safety_rate": sum(r["safeties"] for r in rows) / len(rows),
                    "block_rate": sum(r["blocks"] for r in rows) / len(rows),
                    "pa_residuals": [round(r["points_against"] - r["opp_implied"], 1) for r in rows
                                     if r["points_against"] is not None]},
    }


def _season_to_date(rows: list[dict], key: str) -> dict[tuple[int, int, str], float]:
    """Baseline: each team's average fantasy points in earlier weeks of the season (league mean before any)."""
    out, totals = {}, defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["season"], r["week"])):
        history = totals[(r["season"], r["team"])]
        valid = [v for v in history if v is not None]
        league = [x[key] for x in rows if x[key] is not None]
        out[(r["season"], r["week"], r["team"])] = mean(valid) if valid else mean(league)
        history.append(r[key])
    return out


def _report(held: list[dict], preds: dict, key: str) -> dict:
    base = _season_to_date(held, key)
    pairs = [(r[key], preds[(r["season"], r["week"], r["team"])], base[(r["season"], r["week"], r["team"])])
             for r in held if r[key] is not None and (r["season"], r["week"], r["team"]) in preds]
    actual, model, baseline = (np.array(v) for v in zip(*pairs))
    by_week = defaultdict(list)
    for i, r in enumerate([r for r in held if r[key] is not None and (r["season"], r["week"], r["team"]) in preds]):
        by_week[r["week"]].append(i)
    rho = lambda p: float(np.nanmean([np.corrcoef(np.argsort(np.argsort(actual[i])), np.argsort(np.argsort(p[i])))[0, 1]  # noqa: E731
                                      for i in by_week.values() if len(i) >= 8]))
    return {"n": len(pairs), "model_mae": round(float(np.mean(np.abs(actual - model))), 3),
            "baseline_mae": round(float(np.mean(np.abs(actual - baseline))), 3),
            "model_rank": round(rho(model), 3), "baseline_rank": round(rho(baseline), 3)}


def held_out(rows: list[dict], tests) -> dict:
    global coefficients
    report = {}
    for test in tests:
        coefs = fit([r for r in rows if r["season"] < test])
        held = [r for r in rows if r["season"] == test]
        original = coefficients
        coefficients = lambda c=coefs: c  # noqa: E731  (score with coefficients that never saw the test season)
        try:
            k_preds, d_preds = {}, {}
            for r in held:
                key = (r["season"], r["week"], r["team"])
                k = project_kicker(r["inputs"], r["team"])
                d = project_defense(r["inputs"], r["team"])
                if k:
                    k_preds[key] = k.points
                if d:
                    d_preds[key] = d.points
        finally:
            coefficients = original
        report[str(test)] = {"kicker": _report(held, k_preds, "k_points"),
                             "defense": _report(held, d_preds, "dst_points")}
    return report


def main(argv: list[str] | None = None) -> None:
    from database.db import engine

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="refit and write kdst_coefficients.json")
    parser.add_argument("--seasons", type=int, nargs="+", default=[2021, 2022, 2023, 2024, 2025])
    args = parser.parse_args(argv)
    with Session(engine) as session:
        rows = collect(session, args.seasons)
        report = held_out(rows, args.seasons[-2:])
    for season, result in report.items():
        log.info("held-out %s: %s", season, result)
    if args.fit:
        coefs = fit(rows)
        coefs["held_out"] = report
        coefs["seasons"] = args.seasons
        COEF_PATH.write_text(json.dumps(coefs, indent=1), encoding="utf-8")
        coefficients.cache_clear()
        log.info("wrote %s", COEF_PATH)


if __name__ == "__main__":
    main()
