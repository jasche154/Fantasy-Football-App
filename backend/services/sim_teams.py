"""Simulated seasons: the team half of the public belief (services/sim_world.estimate).

From the played games of an as_of copy (sim_world._Seen), each team's:

- targets / carries per game: the decayed mean of its games shrunk toward the prior World's team (else the
  league mean) by TEAM_PRIOR_GAMES pseudo-games;
- pass_def / rush_def: what opponents gained per target (receiving yards) and per non-QB carry against it,
  over the league rate, shrunk the same way toward the prior World's factor, else last season's
  `defense_prior` (WR / RB factor) at DEFENSE_PRIOR_WEIGHT strength, else 1;
- takeaways: interceptions its opponents threw per attempt over the league rate, shrunk by
  TAKEAWAY_PRIOR_ATTEMPTS (sacks and fumbles forced move with it in sim_game);
- passer: the quality of the QBs who threw its passes (pass-yard weighted), the scale its receivers'
  rates were measured on.
"""
from collections import defaultdict
from collections.abc import Mapping

from services import sim_calendar
from services.replay_pool import SeasonData
from services.sim_world import DECAY, TEAM_PRIOR_GAMES, Player, Team, World, _Seen, _shrink, params

DEFENSE_PRIOR_WEIGHT = 0.5       # last season's factor counts half (as replay_defense blends it)
TAKEAWAY_PRIOR_ATTEMPTS = 300.0
FACTOR_RANGE = (0.6, 1.6)        # a defense factor is kept in this range (odd data cannot make a wall or a sieve)


def _bounded(factor: float) -> float:
    return min(max(factor, FACTOR_RANGE[0]), FACTOR_RANGE[1])


def _decay(week: int, game_week: int) -> float:
    return DECAY ** max(0, week - 1 - game_week)


def _prior_factor(known: SeasonData, prior: World | None, team: str, field: str, position: str) -> float:
    if prior is not None and team in prior.teams:
        return getattr(prior.teams[team], field)
    last = known.defense_prior.get((team, position))
    return 1.0 + (last - 1.0) * DEFENSE_PRIOR_WEIGHT if last else 1.0


def team_forms(known: SeasonData, week: int, seen: _Seen, prior: World | None) -> dict[str, Team]:
    league = params()["league"]
    attempts_per_target = league["attempts_per_target"]
    per_target = league["pass_yards"] / league["targets"]
    sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for (team, game_week), lines in seen.games.items():
        decay = _decay(week, game_week)
        own, against = sums[team], sums[known.schedule.opponent(team, game_week) or ""]
        targets = seen.targets[(team, game_week)]
        season_targets, season_carries, _ = sim_calendar.factors(game_week)   # learned without the calendar
        own["games"] += decay
        own["targets"] += decay * targets / season_targets
        own["carries"] += decay * sum(line.carries for line in lines.values()) / season_carries
        against["d_games"] += decay
        against["d_targets"] += decay * targets
        against["d_rec_yds"] += decay * sum(line.rec_yds for line in lines.values())
        against["d_ints"] += decay * sum(line.pass_int for line in lines.values())
        rushers = [line for pid, line in lines.items()
                   if known.players.get(pid) is not None and known.players[pid].position != "QB"]
        against["d_carries"] += decay * sum(line.carries for line in rushers)
        against["d_rush_yds"] += decay * sum(line.rush_yds for line in rushers)
    forms = {}
    for team in sorted({t for t, _ in known.schedule.opponents}):
        got, old = sums[team], prior.teams.get(team) if prior is not None else None
        strength_targets = TEAM_PRIOR_GAMES * league["targets"]
        strength_carries = TEAM_PRIOR_GAMES * league["rb_carries"]
        forms[team] = Team(
            team=team,
            targets=_shrink(got["targets"], got["games"], old.targets if old else league["targets"], TEAM_PRIOR_GAMES),
            carries=_shrink(got["carries"], got["games"], old.carries if old else league["carries"], TEAM_PRIOR_GAMES),
            other_targets=old.other_targets if old else league["other_targets"],
            other_carries=old.other_carries if old else league["other_carries"],
            pass_def=_bounded(_shrink(got["d_rec_yds"] / per_target, got["d_targets"],
                             _prior_factor(known, prior, team, "pass_def", "WR"), strength_targets)),
            rush_def=_bounded(_shrink(got["d_rush_yds"] / league["rb_ypr"], got["d_carries"],
                             _prior_factor(known, prior, team, "rush_def", "RB"), strength_carries)),
            takeaways=_bounded(_shrink(got["d_ints"] / league["int_rate"], got["d_targets"] * attempts_per_target,
                              old.takeaways if old else 1.0, TAKEAWAY_PRIOR_ATTEMPTS)))
    return forms


def usual_passer(team: str, seen: _Seen, players: Mapping[int, Player], week: int) -> float:
    """Pass-yard weighted quality of the QBs who threw the team's passes; with no games, its starter's."""
    yards = weighted = 0.0
    for (game_team, game_week), lines in seen.games.items():
        if game_team != team:
            continue
        for pid, line in lines.items():
            qb = players.get(pid)
            if qb is not None and qb.position == "QB" and line.pass_yds > 0:
                share = _decay(week, game_week) * line.pass_yds
                yards += share
                weighted += share * qb.quality
    if yards > 0:
        return weighted / yards
    qbs = [p for p in players.values() if p.team == team and p.position == "QB"]
    return max(qbs, key=lambda p: p.starts).quality if qbs else 1.0


def played_weight(team: str, games: Mapping, carries: Mapping[int, float], week: int, decay: float) -> float:
    """The decayed mean, over the team's played games before `week`, of the carry weights of the players with a box
    line in the game plus the "other" bucket's (member 0): the sum a Luce weight is a share of. A depth player
    dressed in every simulated game appears in only some real ones, so a quarterback's weight over every dressed
    player's understates his real share (0.127 against 0.149, mid-season 2023-2025); sim_game gives him his weight
    over this. 0.0 when the team has no game (sim_world.estimate then keeps the prior World's)."""
    total = weight = 0.0
    for (game_team, game_week), lines in games.items():
        if game_team == team and game_week < week:
            d = decay ** max(0, week - 1 - game_week)
            total += d * (sum(carries.get(pid, 0.0) for pid in lines) + carries.get(0, 0.0))
            weight += d
    return total / weight if weight > 0 else 0.0
