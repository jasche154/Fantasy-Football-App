"""Playoff outlook for your team: each player's fantasy-playoff weeks, and your best lineup each week.

The playoff weeks come from the league (Yahoo's playoff start week, through the final),
falling back to DEFAULT_PLAYOFF_WEEKS (16-18). For each rostered QB/RB/WR/TE and each
playoff week:
    projection = healthy points per game x long-run share of weeks played
                 x (that week's opponent defense factor)^ROS_SCHEDULE_WEIGHT
and 0 on a bye. The best lineup for each week fills your slots from those projections, so
you can see which week is weak (byes, tough defenses) and where a trade or pickup helps.
Kickers and defenses aren't projected this far out (their models need that week's Vegas lines).

The planner part (`fixes`): for each position, your weakest starter's playoff total is compared
with every other player's. Free agents in your league and players on other teams whose
playoff total beats it by at least MIN_GAIN points are listed (FIXES_SHOWN of each), best first,
so you can pick them up or trade for them before your league's trade deadline.
"""
from types import SimpleNamespace

from sqlalchemy.orm import Session

from services import my_team as mt
from services.valuation import ROS_SCHEDULE_WEIGHT

DEFAULT_PLAYOFF_WEEKS = (16, 17, 18)
LAST_WEEK = 18
MIN_GAIN = 3.0                  # playoff points over your weakest starter to be worth listing
FIXES_SHOWN = 3


def playoff_weeks(rules: dict) -> tuple[int, ...]:
    weeks = rules.get("playoff_weeks")
    if isinstance(weeks, list) and weeks and all(isinstance(w, int) and 1 <= w <= LAST_WEEK for w in weeks):
        return tuple(weeks)
    return DEFAULT_PLAYOFF_WEEKS


def playoff_cells(value, weeks, schedule, defense) -> list[dict]:
    """One player's projection for each playoff week (0 on a bye)."""
    cells = []
    for week in weeks:
        opponent = schedule.opponent(value.team, week) if value.team else None
        if opponent is None:
            cells.append({"week": week, "opponent": None, "bye": True, "factor": None, "projection": 0.0})
            continue
        factor = defense.factor(opponent, value.position)
        cells.append({"week": week, "opponent": opponent, "bye": False, "factor": round(factor, 3),
                      "projection": round(playoff_rate(value) * factor ** ROS_SCHEDULE_WEIGHT, 2)})
    return cells


def playoff_rate(value) -> float:
    """Points per game in the playoff weeks: his healthy rate (a player hurt now is usually back by then)
    x the long-run share of weeks a healthy player plays (services/lineup_gain.healthy_share)."""
    from services.lineup_gain import healthy_share

    share = getattr(value, "availability", 1.0) or 1.0
    healthy = value.ros_ppg / share if share > 0.05 else value.ros_ppg
    return healthy * healthy_share(value.position)


def _total(cells: list[dict]) -> float:
    return round(sum(c["projection"] for c in cells), 1)


def playoff_fixes(session: Session, team: "mt.MyTeam", weeks: tuple[int, ...]) -> list[dict]:
    """Per position: your weakest starter's playoff total, and free agents / other teams' players who beat it."""
    from sqlalchemy import select

    from database.models import Team

    valuation = team.valuation
    schedule, defense = valuation.schedule, valuation.defense
    rosters = mt.rosters(session, team.league)
    owner = {s.player_id: team_id for team_id, spots in rosters.items() for s in spots}
    names = {t.id: t.name for t in session.scalars(select(Team).where(Team.league_id == team.league.id))}
    totals = {v.player_id: _total(playoff_cells(v, weeks, schedule, defense))
              for v in valuation.values if v.position in mt.MODELED and v.team}
    mine = [s.player_id for s in team.spots if s.slot not in ("IR", "IR+", "NA")]
    fixes = []
    for position in mt.MODELED:
        starters = team.settings.starters.get(position, 0)
        if not starters:
            continue
        my_players = sorted((pid for pid in mine if pid in totals and valuation.by_id[pid].position == position),
                            key=lambda pid: -totals[pid])
        weakest = my_players[starters - 1] if len(my_players) >= starters else None
        bar = totals[weakest] if weakest is not None else 0.0
        better = sorted((v for v in valuation.values if v.position == position and v.player_id in totals
                         and totals[v.player_id] >= bar + MIN_GAIN and owner.get(v.player_id) != team.team.id),
                        key=lambda v: -totals[v.player_id])

        def row(v) -> dict:
            return {"player": {"id": v.player_id, "name": v.name, "position": v.position, "team": v.team},
                    "playoff_total": totals[v.player_id], "gain": round(totals[v.player_id] - bar, 1),
                    "owner": names.get(owner[v.player_id]) if v.player_id in owner else None}

        free = [row(v) for v in better if v.player_id not in owner][:FIXES_SHOWN]
        trade = [row(v) for v in better if v.player_id in owner][:FIXES_SHOWN]
        if free or trade:
            fixes.append({"position": position,
                          "weakest_starter": {"name": valuation.by_id[weakest].name, "playoff_total": bar}
                          if weakest is not None else None,
                          "waivers": free, "trade_targets": trade})
    return fixes


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
        cells = playoff_cells(value, weeks, schedule, defense)
        for cell in cells:
            if not cell["bye"] and spot.slot not in ("IR", "IR+", "NA"):
                per_week[cell["week"]].append(SimpleNamespace(player_id=value.player_id, position=value.position,
                                                              projection=cell["projection"]))
        rows.append({"player": {"id": value.player_id, "name": value.name, "position": value.position,
                                "team": value.team},
                     "slot": spot.slot, "ros_ppg": value.ros_ppg, "weeks": cells,
                     "playoff_total": _total(cells)})
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
            "players": rows, "lineups": lineups, "weakest_week": int(weakest) if weakest else None,
            "fixes": playoff_fixes(session, team, weeks)}
