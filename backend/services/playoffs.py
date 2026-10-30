"""Playoff outlook for your team: each player's fantasy-playoff weeks, and your best lineup each week.

The playoff weeks come from the league (Yahoo's playoff start week, through the final),
falling back to DEFAULT_PLAYOFF_WEEKS (16-18). For each rostered QB/RB/WR/TE and each
playoff week:
    projection = rest-of-season points per game x (that week's opponent defense factor)^ROS_SCHEDULE_WEIGHT
and 0 on a bye. The best lineup for each week fills your slots from those projections, so
you can see which week is weak (byes, tough defenses) and where a trade or pickup helps.
Kickers and defenses aren't projected this far out (their models need that week's Vegas lines).
"""
from types import SimpleNamespace

from sqlalchemy.orm import Session

from services import my_team as mt
from services.valuation import ROS_SCHEDULE_WEIGHT

DEFAULT_PLAYOFF_WEEKS = (16, 17, 18)
LAST_WEEK = 18


def playoff_weeks(rules: dict) -> tuple[int, ...]:
    weeks = rules.get("playoff_weeks")
    if isinstance(weeks, list) and weeks and all(isinstance(w, int) and 1 <= w <= LAST_WEEK for w in weeks):
        return tuple(weeks)
    return DEFAULT_PLAYOFF_WEEKS


def playoff_outlook(session: Session, reference: int | str | None) -> dict:
    team = mt.build_my_team(session, reference)
    weeks = playoff_weeks(mt.league_rules(team.league))
    valuation = team.valuation
    schedule, defense = valuation.schedule, valuation.defense
    rows, per_week = [], {w: [] for w in weeks}
    for spot in team.spots:
        value = valuation.by_id.get(spot.player_id)
        if value is None or value.position not in mt.MODELED:
            continue
        cells = []
        for week in weeks:
            opponent = schedule.opponent(value.team, week) if value.team else None
            if opponent is None:
                cells.append({"week": week, "opponent": None, "bye": True, "factor": None, "projection": 0.0})
                continue
            factor = defense.factor(opponent, value.position)
            projection = round(value.ros_ppg * factor ** ROS_SCHEDULE_WEIGHT, 2)
            cells.append({"week": week, "opponent": opponent, "bye": False, "factor": round(factor, 3),
                          "projection": projection})
            if spot.slot not in ("IR", "IR+", "NA"):
                per_week[week].append(SimpleNamespace(player_id=value.player_id, position=value.position,
                                                      projection=projection))
        rows.append({"player": {"id": value.player_id, "name": value.name, "position": value.position,
                                "team": value.team},
                     "slot": spot.slot, "ros_ppg": value.ros_ppg, "weeks": cells,
                     "playoff_total": round(sum(c["projection"] for c in cells), 1)})
    lineups = {}
    for week in weeks:
        best = mt.best_lineup(per_week[week], team.settings)
        by_id = {p.player_id: p for p in per_week[week]}
        starters = [pid for slot in mt.SLOT_ORDER for pid in best.get(slot, [])]
        empty = [slot for slot in ("QB", "RB", "WR", "TE", "FLEX")
                 if len(best.get(slot, [])) < team.settings.starters.get(slot, 0)]
        lineups[str(week)] = {"projected": round(sum(by_id[pid].projection for pid in starters), 1),
                              "starters": starters, "empty_slots": empty}
    rows.sort(key=lambda r: -r["playoff_total"])
    weakest = min(lineups, key=lambda w: lineups[w]["projected"]) if lineups else None
    return {"league": team.league.name, "team": team.team.name, "weeks": list(weeks),
            "source": "league" if mt.league_rules(team.league).get("playoff_weeks") else "default",
            "players": rows, "lineups": lineups, "weakest_week": int(weakest) if weakest else None}
