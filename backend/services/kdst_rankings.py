"""Week X kicker and defense rankings, and the DST pseudo-players rosters use."""
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Player
from services import kdst_model as km
from services.weather import forecasts

DST_PREFIX = "DST-"
TEAM_NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears",
    "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
    "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs", "LA": "Rams", "LAC": "Chargers",
    "LV": "Raiders", "MIA": "Dolphins", "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
    "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks", "SF": "49ers", "TB": "Buccaneers",
    "TEN": "Titans", "WAS": "Commanders",
}


def dst_player(session: Session, team: str) -> Player:
    """The pseudo-player standing for a team defense (created once, e.g. 'Steelers D/ST')."""
    gsis = f"{DST_PREFIX}{team}"
    player = session.scalar(select(Player).where(Player.gsis_id == gsis))
    if player is None:
        player = Player(gsis_id=gsis, full_name=f"{TEAM_NAMES.get(team, team)} D/ST", position="DST", nfl_team=team)
        session.add(player)
        session.flush()
    return player


def kdst_projections(session: Session, season: int, week: int,
                     k_scoring: Mapping[str, float] = km.DEFAULT_K,
                     dst_scoring: Mapping[str, float] = km.DEFAULT_DST) -> tuple[list, list, km.WeekInputs]:
    """(kicker projections, defense projections, inputs) for every team playing that week, best first."""
    inputs = km.week_inputs(session, season, week)
    inputs.forecast = forecasts(session, {g.game_id for g in inputs.games.values()})
    kickers = [p for team in inputs.games if (p := km.project_kicker(inputs, team, k_scoring)) is not None]
    defenses = [p for team in inputs.games if (p := km.project_defense(inputs, team, dst_scoring)) is not None]
    return (sorted(kickers, key=lambda p: -p.points), sorted(defenses, key=lambda p: -p.points), inputs)


def kicker_names(session: Session, ids) -> dict[int, str]:
    ids = [i for i in ids if i is not None]
    if not ids:
        return {}
    return dict(session.execute(select(Player.id, Player.full_name).where(Player.id.in_(ids))).all())


def serialize(session: Session, season: int, week: int, position: str) -> dict:
    kickers, defenses, _ = kdst_projections(session, season, week)
    if position == "K":
        names = kicker_names(session, [k.player_id for k in kickers])
        results = [{"rank": i, "player": {"id": k.player_id, "name": names.get(k.player_id, f"{k.team} kicker"),
                                          "position": "K", "team": k.team},
                    "opponent": k.opponent, "projection": k.points, "fg_attempts": k.fg_attempts, "fg_made": k.fg_made,
                    "pat_made": k.pat_made, "accuracy": dict(k.accuracy),
                    "wind": k.conditions.wind, "temp": k.conditions.temp, "indoors": k.conditions.indoors,
                    "reasons": list(k.reasons)} for i, k in enumerate(kickers, start=1)]
    else:
        results = [{"rank": i, "player": {"id": None, "name": f"{TEAM_NAMES.get(d.team, d.team)} D/ST",
                                          "position": "DST", "team": d.team},
                    "opponent": d.opponent, "projection": d.points, "sacks": d.sacks, "takeaways": d.takeaways,
                    "tds": d.tds, "opp_implied": d.points_allowed, "points_allowed_score": d.points_allowed_score,
                    "reasons": list(d.reasons)} for i, d in enumerate(defenses, start=1)]
    return {"season": season, "week": week, "position": position, "scoring": "Yahoo default", "results": results}


# Typical spread of weekly scores, for floor / ceiling (10th / 90th percentile = mean -/+ 1.28 sd).
SPREAD = {"K": 4.3, "DST": 5.6}


def weekly_players(session: Session, season: int, week: int, player_ids,
                   k_scoring: Mapping[str, float] | None = None,
                   dst_scoring: Mapping[str, float] | None = None) -> dict:
    """{player_id: WeeklyPlayer} for rostered kickers and defenses, scored with the league's rules."""
    from services.weekly_model import Matchup
    from services.weekly_rankings import SourceValue, Step, WeeklyPlayer

    players = {p.id: p for p in session.scalars(select(Player).where(
        Player.id.in_(list(player_ids)), Player.position.in_(("K", "DST"))))}
    if not players:
        return {}
    kickers, defenses, inputs = kdst_projections(session, season, week, {**km.DEFAULT_K, **(k_scoring or {})},
                                                 {**km.DEFAULT_DST, **(dst_scoring or {})})
    by_team = {("K", k.team): k for k in kickers} | {("DST", d.team): d for d in defenses}
    out = {}
    for pid, player in players.items():
        proj = by_team.get((player.position, player.nfl_team))
        if proj is None:
            continue                                   # bye week, or team unknown
        game = inputs.games.get(proj.team)
        spread = SPREAD[player.position] * 1.28
        matchup = Matchup(proj.opponent, bool(game and game.home_team == proj.team), 1.0, None, None, None,
                          bool(game and game.home_score is not None))
        out[pid] = WeeklyPlayer(
            player_id=pid, name=player.full_name, position=player.position, team=proj.team, matchup=matchup,
            projection=proj.points, sources={"model": SourceValue(proj.points)}, expert_ranks={},
            if_active=proj.points, floor=round(max(-5.0, proj.points - spread), 1), ceiling=round(proj.points + spread, 1),
            explanation=tuple(Step("Model", "", reason) for reason in proj.reasons))
    return out
