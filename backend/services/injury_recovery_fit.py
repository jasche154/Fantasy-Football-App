"""Fit the injury-recovery numbers in services/injury_recovery.json from past seasons.

    venv/Scripts/python.exe -m services.injury_recovery_fit --seasons 2021-2025

An injury spell starts at a team game a fantasy-relevant player (8+ PPR points per game over his
last six, 3+ games played) misses right after playing, and ends when he plays again. "Played"
means 1+ offensive snaps. Spells still open at season's end are censored: their games missed are a
lower bound, which biases the averages slightly low. Games missed are capped at MAX_GAMES.

Fitted:
- games_missed[group]: average games missed per spell by body part, shrunk toward the overall
  average with PRIOR_SPELLS pseudo-spells.
- practice_factor[none | dnp | practicing]: average games still to miss after an absent week,
  given how he practiced that week, divided by the overall average. "none" (no injury report)
  is mostly injured reserve.
- rust[i]: PPR points in the i-th game back divided by his pre-injury rate, relative to the
  same ratio for healthy players (which corrects for regression to the mean).
- reinjury_excess: share of returners who start a new spell within 5 weeks, minus the share
  of healthy players who do.
- rb_role_loss: a returning RB's snap share (games 2-3 back) when his fill-in scored at least
  FILL_IN_RATIO of his rate, minus when the fill-in didn't, clipped to [0, MAX_ROLE_LOSS].
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from database.models import InjuryReport, NflGame, Player, PlayerSnapCountsWeekly, PlayerStatsWeekly  # noqa: E402
from services.injury_recovery import FILL_IN_RATIO, PARAMS_PATH, body_group, practice_key  # noqa: E402

RELEVANT_PPG = 8.0
MIN_GAMES_BEFORE = 3
MAX_GAMES = 10
PRIOR_SPELLS = 30
RUST_GAMES = 3
REINJURY_WEEKS = 5
MAX_ROLE_LOSS = 0.1
POSITIONS = ("QB", "RB", "WR", "TE")


def _load(session: Session, seasons: range):
    positions = dict(session.execute(select(Player.id, Player.position)).all())
    team_weeks: dict[tuple[int, str], list[int]] = defaultdict(list)
    for s, w, home, away in session.execute(select(NflGame.season, NflGame.week, NflGame.home_team, NflGame.away_team)
                                            .where(NflGame.game_type == "REG", NflGame.season.in_(seasons))):
        team_weeks[(s, home)].append(w)
        team_weeks[(s, away)].append(w)
    played: dict[tuple[int, int], dict[int, tuple[str, float]]] = defaultdict(dict)
    snap_rows = session.execute(select(
        PlayerSnapCountsWeekly.player_id, PlayerSnapCountsWeekly.season, PlayerSnapCountsWeekly.week,
        PlayerSnapCountsWeekly.team, PlayerSnapCountsWeekly.offense_snaps, PlayerSnapCountsWeekly.offense_pct)
        .where(PlayerSnapCountsWeekly.season.in_(seasons),
               (PlayerSnapCountsWeekly.game_type == "REG") | PlayerSnapCountsWeekly.game_type.is_(None)))
    for pid, s, w, team, snaps, pct in snap_rows:
        if snaps and positions.get(pid) in POSITIONS:
            played[(pid, s)][w] = (team, pct or 0.0)
    points = {(pid, s, w): fp or 0.0 for pid, s, w, fp in session.execute(select(
        PlayerStatsWeekly.player_id, PlayerStatsWeekly.season, PlayerStatsWeekly.week, PlayerStatsWeekly.fantasy_points_ppr)
        .where(PlayerStatsWeekly.season.in_(seasons), PlayerStatsWeekly.season_type == "REG"))}
    reports = {(pid, s, w): (practice, injury) for pid, s, w, practice, injury in session.execute(select(
        InjuryReport.player_id, InjuryReport.season, InjuryReport.week, InjuryReport.practice_status, InjuryReport.injury)
        .where(InjuryReport.season.in_(seasons)))}
    return positions, {k: sorted(v) for k, v in team_weeks.items()}, played, points, reports


def find_spells(positions, team_weeks, played, points, reports) -> list[dict]:
    spells = []
    for (pid, season), games in played.items():
        weeks = sorted(games)
        team = games[weeks[-1]][0]
        schedule = team_weeks.get((season, team), [])
        i = 1
        while i < len(schedule):
            week = schedule[i]
            if week in games or schedule[i - 1] not in games or games[schedule[i - 1]][0] != team:
                i += 1
                continue
            before = [w for w in weeks if w < week]
            pre_ppg = mean(points.get((pid, season, w), 0.0) for w in before[-6:])
            j = i
            while j < len(schedule) and schedule[j] not in games:
                j += 1
            if len(before) >= MIN_GAMES_BEFORE and pre_ppg >= RELEVANT_PPG:
                injury = (reports.get((pid, season, week)) or reports.get((pid, season, schedule[i - 1])) or (None, None))[1]
                back = [w for w in weeks if w > week][:RUST_GAMES]
                spells.append({"pid": pid, "season": season, "team": team, "position": positions[pid],
                               "group": body_group(injury), "absent": schedule[i:j], "missed": j - i,
                               "censored": j >= len(schedule), "pre_ppg": pre_ppg,
                               "pre_pct": mean(games[w][1] for w in before[-4:]),
                               "back_ppg": [points.get((pid, season, w), 0.0) for w in back],
                               "back_pct": [games[w][1] for w in back]})
            i = j + 1
    return spells


def _games_missed(spells: list[dict]) -> dict[str, float]:
    capped = lambda group: [min(s["missed"], MAX_GAMES) for s in spells if s["group"] == group]  # noqa: E731
    overall = mean(min(s["missed"], MAX_GAMES) for s in spells)
    out = {"overall": round(overall, 2)}
    for group in sorted({s["group"] for s in spells}):
        values = capped(group)
        out[group] = round((sum(values) + PRIOR_SPELLS * overall) / (len(values) + PRIOR_SPELLS), 2)
    return out


def _practice_factor(spells: list[dict], reports) -> dict[str, float]:
    still: dict[str, list[int]] = defaultdict(list)
    for s in spells:
        for k, week in enumerate(s["absent"]):
            practice = practice_key((reports.get((s["pid"], s["season"], week)) or (None, None))[0])
            still[practice].append(min(s["missed"], MAX_GAMES) - k - 1)
    overall = mean(v for values in still.values() for v in values)
    return {key: round(mean(values) / overall, 3) for key, values in sorted(still.items())}


def _healthy_baseline(played, points, positions, spells) -> tuple[list[float], float]:
    """Next-game / prior-six ratios for healthy relevant players, and their new-spell rate."""
    starts = defaultdict(set)
    for s in spells:
        starts[(s["pid"], s["season"])].add(s["absent"][0])
    pre, nxt, hurt = [], [[] for _ in range(RUST_GAMES)], []
    for (pid, season), games in played.items():
        weeks = sorted(games)
        for i in range(6, len(weeks) - RUST_GAMES, RUST_GAMES):
            if weeks[i - 1] - weeks[i - 6] > 6:
                continue                                   # missed time inside the window: not healthy
            prior = mean(points.get((pid, season, w), 0.0) for w in weeks[i - 6:i])
            if prior < RELEVANT_PPG:
                continue
            pre.append(prior)
            for k in range(RUST_GAMES):
                nxt[k].append(points.get((pid, season, weeks[i + k]), 0.0))
            hurt.append(any(weeks[i - 1] < w <= weeks[i - 1] + REINJURY_WEEKS for w in starts[(pid, season)]))
    return [mean(n) / mean(pre) for n in nxt], mean(hurt)


def _rust_and_reinjury(spells, played, points, positions) -> tuple[list[float], float]:
    baseline, healthy_hurt = _healthy_baseline(played, points, positions, spells)
    back = [s for s in spells if len(s["back_ppg"]) == RUST_GAMES and s["group"] != "non_injury"]
    rust = [round(min(1.0, mean(s["back_ppg"][k] for s in back) / mean(s["pre_ppg"] for s in back) / baseline[k]), 3)
            for k in range(RUST_GAMES)]
    by_player = defaultdict(list)
    for s in spells:
        by_player[(s["pid"], s["season"])].append(s)
    returned = [s for s in spells if not s["censored"]]
    again = [any(0 < o["absent"][0] - s["absent"][-1] <= REINJURY_WEEKS for o in by_player[(s["pid"], s["season"])])
             for s in returned]
    return rust, round(max(0.0, mean(again) - healthy_hurt), 3)


def _rb_role_loss(spells, played, points, positions) -> tuple[float, int]:
    """Snap-share drop for a returning RB whose fill-in played well, vs one whose fill-in didn't."""
    on_team = defaultdict(list)
    for (pid, season), games in played.items():
        for week, (team, _) in games.items():
            on_team[(season, team, week)].append(pid)
    strong, weak = [], []
    for s in spells:
        if s["position"] != "RB" or s["missed"] < 2 or len(s["back_pct"]) < RUST_GAMES or s["pre_pct"] <= 0:
            continue
        mates = {pid for w in s["absent"] for pid in on_team[(s["season"], s["team"], w)]
                 if pid != s["pid"] and positions.get(pid) == "RB"}
        if not mates:
            continue
        best = max(mean(points.get((pid, s["season"], w), 0.0) for w in s["absent"]) for pid in mates)
        ratio = mean(s["back_pct"][1:]) / s["pre_pct"]
        (strong if best >= FILL_IN_RATIO * s["pre_ppg"] else weak).append(ratio)
    if not strong or not weak:
        return 0.0, len(strong)
    return round(min(MAX_ROLE_LOSS, max(0.0, mean(weak) - mean(strong))), 3), len(strong)


def fit(session: Session, seasons: range) -> dict:
    positions, team_weeks, played, points, reports = _load(session, seasons)
    spells = find_spells(positions, team_weeks, played, points, reports)
    rust, reinjury = _rust_and_reinjury(spells, played, points, positions)
    role_loss, strong = _rb_role_loss(spells, played, points, positions)
    return {"seasons": [seasons.start, seasons.stop - 1], "spells": len(spells),
            "games_missed": _games_missed(spells), "practice_factor": _practice_factor(spells, reports),
            "rust": rust, "reinjury_excess": reinjury, "rb_role_loss": role_loss, "rb_role_cases": strong}


def main() -> None:
    from database.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seasons", default="2021-2025", help="first-last, e.g. 2021-2025")
    args = parser.parse_args()
    first, last = (int(x) for x in args.seasons.split("-"))
    with SessionLocal() as session:
        params = fit(session, range(first, last + 1))
    PARAMS_PATH.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(params, indent=2))


if __name__ == "__main__":
    main()
