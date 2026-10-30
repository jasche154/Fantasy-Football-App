"""Compare players over time: every regular-season game across the chosen seasons, plus a
per-season summary, so any 2-4 players can be charted against each other week by week."""
from collections.abc import Sequence
from statistics import mean

from sqlalchemy.orm import Session

from services.projections import GameLine, load_player_seasons
from services.scoring import ScoringSettings

MAX_PLAYERS = 4
MAX_SEASONS = 4


def _game(season: int, line: GameLine) -> dict:
    return {"season": season, "week": line.week, "opponent": line.opponent,
            "fantasy_points": round(line.fantasy_points, 2),
            "expected_points": round(line.expected_points, 2) if line.expected_points is not None else None,
            "targets": line.targets, "receptions": line.receptions, "carries": line.carries,
            "scrimmage_yards": line.rushing_yards + line.receiving_yards, "passing_yards": line.passing_yards,
            "touchdowns": line.total_tds, "snap_pct": line.snap_pct}


def _summary(season: int, games: Sequence[GameLine]) -> dict:
    points = [g.fantasy_points for g in games]
    expected = [g.expected_points for g in games if g.expected_points is not None]
    return {"season": season, "games": len(games), "ppg": round(mean(points), 2),
            "expected_ppg": round(mean(expected), 2) if expected else None,
            "best": round(max(points), 2), "worst": round(min(points), 2),
            "targets_per_game": round(mean(g.targets for g in games), 1),
            "carries_per_game": round(mean(g.carries for g in games), 1)}


def compare_over_time(session: Session, player_ids: Sequence[int], seasons: Sequence[int],
                      scoring: ScoringSettings) -> dict:
    """{seasons, players: [{player, games, summary}]} in the order the ids were given."""
    by_player: dict[int, dict] = {}
    for season in sorted(seasons):
        for player in load_player_seasons(session, season, scoring, player_ids):
            games = [g for g in player.games if g.season_type in (None, "REG")]
            entry = by_player.setdefault(player.player_id, {
                "player": {"id": player.player_id, "name": player.name, "position": player.position,
                           "team": player.team}, "games": [], "summary": []})
            entry["player"]["team"] = player.team or entry["player"]["team"]          # latest season's team
            entry["games"] += [_game(season, g) for g in games]
            if games:
                entry["summary"].append(_summary(season, games))
    return {"seasons": sorted(seasons), "players": [by_player[pid] for pid in player_ids if pid in by_player]}
