"""Retro grades for the season's completed trades in the user's Yahoo league.

For each side of a trade: the fantasy points (the league's scoring, from our nflverse weekly
stats) its received players actually scored in the weeks after the trade, minus the same for
the players it sent. "After the trade" starts with the first NFL week whose first game kicked
off after the trade went through (a trade on a Friday counts from the next week), and only
weeks with stats in the database are counted. A side is the winner when it's ahead by
WIN_MARGIN points or more. A player we can't match to our database (or a kicker / defense)
is skipped and listed under "unmatched".
"""
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import League, PlayerStatsWeekly
from integrations.weekly_sources import PlayerMatcher
from services import my_team as mt
from services.projections import load_game_lines
from services.schedule_strength import load_schedule
from services.yahoo_sync import modeled_position

WIN_MARGIN = 10.0
COMPLETED = ("successful", None)
NO_TIME_HOURS = 17                     # a game with a date but no time: about 1 PM US Eastern


def week_starts(session: Session, season: int) -> dict[int, datetime]:
    """{week: first kickoff (UTC)} from the NFL schedule."""
    starts: dict[int, datetime] = {}
    for game in load_schedule(session, season).games:
        moment = game.kickoff
        if moment is None and game.gameday:
            try:
                moment = datetime.strptime(game.gameday, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            moment += timedelta(hours=NO_TIME_HOURS)
        if moment is not None and (game.week not in starts or moment < starts[game.week]):
            starts[game.week] = moment
    return starts


def first_week_after(timestamp: int | None, starts: Mapping[int, datetime]) -> int | None:
    """The first week that started after `timestamp` (Unix seconds); None if unknown or none left."""
    if not timestamp:
        return None
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    return next((week for week in sorted(starts) if starts[week] > moment), None)


def stat_weeks(session: Session, season: int) -> list[int]:
    """Regular-season weeks with stats in the database."""
    rows = session.scalars(select(PlayerStatsWeekly.week).distinct().where(
        PlayerStatsWeekly.season == season, PlayerStatsWeekly.season_type == "REG"))
    return sorted(w for w in rows if w and w > 0)


def grade_trade(moves: Sequence[Mapping], weeks: Sequence[int], points: Mapping[int, Mapping[int, float]],
                names: Mapping[str, str]) -> dict:
    """Each side's points since the trade. moves: {player_id, name, from_team, to_team};
    points: {player id: {week: points}} (a week he didn't play counts 0)."""
    counted = set(weeks)
    teams: list[str] = []
    for move in moves:
        for team in (move.get("from_team"), move.get("to_team")):
            if team and team not in teams:
                teams.append(team)

    def scored(move) -> float:
        by_week = points.get(move["player_id"], {}) if move.get("player_id") is not None else {}
        return round(sum(p for w, p in by_week.items() if w in counted), 1)

    sides = []
    for team in teams:
        received = [{"name": m["name"], "points": scored(m)} for m in moves
                    if m.get("to_team") == team and m.get("player_id") is not None]
        sent = [{"name": m["name"], "points": scored(m)} for m in moves
                if m.get("from_team") == team and m.get("player_id") is not None]
        got, gave = round(sum(r["points"] for r in received), 1), round(sum(s["points"] for s in sent), 1)
        sides.append({"team_key": team, "team": names.get(team, team), "received": received, "sent": sent,
                      "received_points": got, "sent_points": gave, "net": round(got - gave, 1)})
    graded = bool(counted)
    best = max(sides, key=lambda s: s["net"], default=None)
    winner = best if graded and best is not None and best["net"] >= WIN_MARGIN else None
    return {"weeks": sorted(counted), "graded": graded, "sides": sides,
            "winner": winner["team"] if winner else None, "winner_key": winner["team_key"] if winner else None,
            "margin": winner["net"] if winner else None,
            "unmatched": [m["name"] for m in moves if m.get("player_id") is None]}


def _match(matcher: PlayerMatcher, move: Mapping) -> int | None:
    position = modeled_position(move.get("position"))
    if position not in mt.MODELED:
        return None                          # kickers and defenses aren't scored from box scores
    return matcher.match(move.get("name") or "", position=position, yahoo_id=move.get("yahoo_player_id"))


def league_trade_grades(session: Session, league: League, transactions: Sequence[Mapping],
                        names: Mapping[str, str]) -> list[dict]:
    """Every completed trade this season, newest first, graded on the points scored since."""
    trades = [t for t in transactions if t.get("type") == "trade" and t.get("status") in COMPLETED
              and any(m.get("type") == "trade" for m in t.get("moves", []))]
    if not trades:
        return []
    matcher = PlayerMatcher(session)
    starts = week_starts(session, league.season)
    available = stat_weeks(session, league.season)
    matched = [[{**m, "player_id": _match(matcher, m)} for m in t["moves"] if m.get("type") == "trade"] for t in trades]
    ids = {m["player_id"] for moves in matched for m in moves if m["player_id"] is not None}
    scoring = mt.league_settings(league).scoring_settings()
    lines = load_game_lines(session, league.season, scoring, player_ids=ids) if ids else {}
    points = {pid: {line.week: line.fantasy_points for line in games} for pid, (_, games) in lines.items()}
    out = []
    for trade, moves in zip(trades, matched):
        first = first_week_after(trade.get("timestamp"), starts)
        weeks = [w for w in available if first is not None and w >= first]
        grade = grade_trade(moves, weeks, points, names)
        when = datetime.fromtimestamp(trade["timestamp"], timezone.utc).date().isoformat() if trade.get("timestamp") else None
        out.append({"date": when, "timestamp": trade.get("timestamp"), "first_week": first, **grade})
    out.sort(key=lambda g: -(g["timestamp"] or 0))
    return out


def trade_records(grades: Sequence[Mapping]) -> dict[str, dict[str, int]]:
    """{team key: {graded, won, lost}}: graded trades, and those won / lost by WIN_MARGIN or more."""
    records: dict[str, dict[str, int]] = {}
    for grade in grades:
        if not grade.get("graded"):
            continue
        for side in grade["sides"]:
            row = records.setdefault(side["team_key"], {"graded": 0, "won": 0, "lost": 0})
            row["graded"] += 1
            if side["net"] >= WIN_MARGIN:
                row["won"] += 1
            elif side["net"] <= -WIN_MARGIN:
                row["lost"] += 1
    return records

