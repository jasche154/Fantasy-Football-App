"""Streaming grid for defenses and quarterbacks: your player and the best free agents over the next
WEEKS weeks, with a suggested path (hold, then add) that needs the fewest pickups.

- Defenses: services/kdst_model projections for each week. The first week uses that week's Vegas
  lines; later weeks fall back to season scoring averages until lines are posted ("early").
- Quarterbacks (one-QB leagues): services/lineup_gain projections (healthy rate x matchup, this
  week's projection for this week), with rushing points per game, which steady a streamer's floor.
"""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Player
from services import lineup_gain
from services import my_team as mt
from services.kdst_rankings import kdst_projections

WEEKS = 3
SHOWN = 10
STREAM_EDGE = 2.0          # points a week a pickup must add over who you'd otherwise start


def suggested_path(rows: Sequence[dict], weeks: Sequence[int], mine: set[str]) -> list[str]:
    """['Hold X weeks 9-10', 'Add Y for week 11']: the best option each week, merged into runs."""
    plan: list[tuple[str, list[int]]] = []
    held = next((r for r in rows if r["name"] in mine), None)
    for week in weeks:
        points = lambda r: r["by_week"].get(str(week), {}).get("projection", 0.0)  # noqa: E731
        best = max(rows, key=points, default=None)
        if best is None:
            continue
        current = next((r for r in rows if plan and r["name"] == plan[-1][0]), held)
        if current is not None and points(best) - points(current) < STREAM_EDGE:
            best = current                             # not worth a pickup for a small edge
        if plan and plan[-1][0] == best["name"]:
            plan[-1][1].append(week)
        else:
            plan.append((best["name"], [week]))

    def span(ws: list[int]) -> str:
        return f"week {ws[0]}" if len(ws) == 1 else f"weeks {ws[0]}-{ws[-1]}"
    return [f"{'Hold' if name in mine else 'Add'} {name} for {span(ws)}" for name, ws in plan]


def _defense_grid(session: Session, team, weeks: Sequence[int], rostered: set[int], mine: set[int]) -> dict:
    rules = mt.league_rules(team.league)
    by_team: dict[str, dict] = {}
    for index, week in enumerate(weeks):
        if rules.get("dst_scoring"):
            _, defenses, _ = kdst_projections(session, team.rankings.season, week, dst_scoring=rules["dst_scoring"])
        else:
            _, defenses, _ = kdst_projections(session, team.rankings.season, week)
        for d in defenses:
            by_team.setdefault(d.team, {})[str(week)] = {"opponent": d.opponent, "projection": round(d.points, 1),
                                                          "early": index > 0}
    players = {p.nfl_team: p for p in session.scalars(select(Player).where(Player.position == "DST"))}
    rows = []
    for nfl_team, cells in by_team.items():
        player = players.get(nfl_team)
        if player is None or (player.id in rostered and player.id not in mine):
            continue
        rows.append({"player": {"id": player.id, "name": player.full_name, "position": "DST", "team": nfl_team},
                     "name": player.full_name, "mine": player.id in mine, "by_week": cells,
                     "total": round(sum(c["projection"] for c in cells.values()), 1)})
    return _shape(rows, weeks)


def _qb_grid(team, weeks: Sequence[int], rostered: set[int], mine: set[int]) -> dict:
    horizon, projector = lineup_gain.context_for(team)
    seasons = {p.player_id: p for p in team.valuation.seasons}
    rows = []
    for value in team.valuation.values:
        if value.position != "QB" or not value.team or (value.player_id in rostered and value.player_id not in mine):
            continue
        cells = {}
        for week in weeks:
            opponent = team.valuation.schedule.opponent(value.team, week)
            cells[str(week)] = {"opponent": opponent, "projection": round(projector.points(value, week), 1),
                                "early": week != horizon.current_week} if opponent else {"bye": True, "projection": 0.0}
        games = seasons.get(value.player_id).games if value.player_id in seasons else ()
        rush = (sum(g.rushing_yards * 0.1 + g.rushing_tds * 6 for g in games) / len(games)) if games else 0.0
        rows.append({"player": {"id": value.player_id, "name": value.name, "position": "QB", "team": value.team},
                     "name": value.name, "mine": value.player_id in mine, "by_week": cells,
                     "rush_ppg": round(rush, 1), "total": round(sum(c["projection"] for c in cells.values()), 1)})
    return _shape(rows, weeks)


def _shape(rows: list[dict], weeks: Sequence[int]) -> dict:
    mine = [r for r in rows if r["mine"]]
    free = sorted((r for r in rows if not r["mine"]), key=lambda r: -r["total"])[:SHOWN]
    shown = sorted(mine, key=lambda r: -r["total"]) + free
    return {"rows": shown, "path": suggested_path(shown, weeks, {r["name"] for r in mine})}


def streaming_grid(session: Session, reference: int | str | None) -> dict:
    team = mt.build_my_team(session, reference)
    first = team.rankings.week
    last_scheduled = max((w for _, w in team.valuation.schedule.opponents), default=first)
    last = min(first + WEEKS - 1, max(last_scheduled, first))
    weeks = list(range(first, last + 1))
    rostered = {s.player_id for spots in mt.rosters(session, team.league).values() for s in spots}
    mine = {s.player_id for s in team.spots}
    grids = {"DST": _defense_grid(session, team, weeks, rostered, mine)}
    if not team.settings.superflex and team.settings.starters.get("QB", 1) <= 1:
        grids["QB"] = _qb_grid(team, weeks, rostered, mine)
    return {"league": team.league.name, "weeks": weeks, "grids": grids,
            "note": "Week 1 uses this week's lines; later weeks are early estimates until lines are posted."}
