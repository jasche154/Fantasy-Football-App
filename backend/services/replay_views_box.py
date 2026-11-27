"""Replay League: one played NFL game's box score (re-exported by services/replay_views_league.py). Same
no-hindsight rule as every view: it reads `Game.data` only (results of weeks already played).

GET  .../box?week=W&team=T        the game NFL team T played in week W (1..the last played week)
      {week, teams: [BoxTeam (away), BoxTeam (home)]}
      BoxTeam   {team, home: bool, score: number | null, players: [BoxPlayer]}
      score     the team's final points; null when the season doesn't have it (a league saved before scores were
                kept, or a real game the database has no score for)
      BoxPlayer {id, name, position, points, box: BoxLine | null}
                everyone in the pool who played for the team that week, QB, RB, WR, TE, K, DST, then most points
                first. points: fantasy points under the league's scoring. box: null for K and DST; its pass_cmp and
                pass_att are null on a passing line that never recorded them (replay_box.shown).
      422 when W is not a played week or T had no game in week W (a bye, or not a team).
"""
from services import replay_box
from services.replay_views import Game

ORDER = ("QB", "RB", "WR", "TE", "K", "DST")


def _team(game: Game, team: str, week: int, home: bool) -> dict:
    data = game.data
    ids = [pid for pid, games in data.team_by_week.items() if (week, team) in games and (pid, week) in data.actual]
    players = [{"id": pid, "name": data.players[pid].name, "position": data.players[pid].position,
                "points": data.actual[(pid, week)],
                "box": replay_box.shown(line) if (line := data.box.get((pid, week))) else None} for pid in ids]
    players.sort(key=lambda row: (ORDER.index(row["position"]) if row["position"] in ORDER else len(ORDER),
                                  -row["points"], row["name"]))
    return {"team": team, "home": home, "score": data.scores.get((team, week)), "players": players}


def box_score(game: Game, week: int, team: str) -> dict:
    """The box score of `team`'s game in `week`, both sides. ValueError (422) for an unplayed week or no game."""
    if not 1 <= week <= game.played:
        raise ValueError(f"Week {week} has not been played yet.")
    found = game.data.schedule.game(team, week)
    if found is None:
        raise ValueError(f"{team} had no game in week {week}.")
    return {"week": week, "teams": [_team(game, found.away_team, week, False),
                                    _team(game, found.home_team, week, True)]}
