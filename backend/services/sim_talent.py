"""Simulated seasons: the hidden truth behind the public belief.

`truth(belief, seed, from_week, last_week)` returns a World in which every player differs from his
reputation, plus dated role changes. Nothing in a simulated SeasonData reads it except the games it plays
(services/sim_season.py), so the public only learns it from results, like a real season.

- Level. Each player's role weights (targets and carries together) and his efficiency (yards and TDs per
  catch and per carry, together) are multiplied by lognormal draws with mean 1. The spread is
  `role_sd` / `eff_sd` x max(`floor`, sqrt(`evidence` / (`evidence` + games behind the belief))): a rookie
  or a player with few games is the least known, a veteran with a full season the best known. A QB's
  quality moves the same way with `quality_sd`. A `team_share` of each player's efficiency draw is shared
  by his whole offense (an offense can collapse or click together).
- A whole season (from week 1, a belief sim_next_year regressed toward the norm) adds the talent that belief
  can't know: each rate (catch, yards and TDs per catch and per carry; a QB's quality and INT rate) x its own
  lognormal draw, sd = the rate's true-talent spread at the position (sim_next_year.json `efficiency_sd`) x
  sqrt(1 - its `efficiency_keep`) for a veteran (what last season's rate leaves unexplained), the whole spread
  for a player with no games of evidence (a rookie), x `season_spread`. Catch rates stay below MAX_CATCH.
  `efficiency_sd` assumes the belief is talent plus noise (a lower bound); `season_spread` 2 (tuned, about what
  assuming talent itself changes over the offseason gives) brings whole seasons' leaderboards, big games and
  team spread to the real ones in each of 2022-2025 (receivers' top finishers overshoot a little).
- Teams: volume (targets and carries together) x lognormal `volume_sd`; pass defense, run defense and
  takeaways each x lognormal `defense_sd`.
- Dated changes (from a uniform week after from_week to CHANGE_LAST_WEEKS before the end): per team and
  season, a breakout (a non-lead receiver or back, `breakout_rate`, weights x `breakout_gain`), a bust (a
  lead one, `bust_rate`, weights x `bust_cut`) and a QB benching (`bench_rate` x exp((1 - his true quality) /
  BENCH_QUALITY): a poor passer is benched more often than a good one; the starter's starts weight x BENCHED,
  so the next QB plays from then on). World.at(week) applies them. In a whole season (from week 1)
  a team whose next QB in line is an unproven prospect (no games of evidence, starts weight ROOKIE_HOPEFUL or
  more: a drafted rookie, sim_next_year) benches its starter at `rookie_bench_rate` instead: most such
  rookies start before the season is out (sim_next_year_fit's `qb_takeover`, injuries included).

The spreads and rates are tuned so simulated seasons reshuffle like real ones (`sim_eval --tune`: ADP rank
vs final rank, breakouts, busts) and live in sim_params.json's tuned "talent" section.
"""
import math
from dataclasses import replace
from types import MappingProxyType

import numpy as np

from services import replay_state, sim_next_year
from services.sim_world import Player, RoleChange, Team, World, params

BENCHED = 0.01                   # a benched QB's starts weight is multiplied by this
LEADS = {"RB": 1, "WR": 2, "TE": 1}    # this many per position count as a team's lead players
CHANGE_LAST_WEEKS = 2            # no new role change in the season's last weeks
BENCH_QUALITY = 0.08             # a starter this much worse than average is e times as likely to be benched
ROOKIE_HOPEFUL = 0.1             # an unproven QB with this starts weight is a prospect pushing for the job
SEASON_RATES = ("catch", "ypc", "rec_td", "ypr", "rush_td", "quality", "int_rate")
MAX_CATCH = 0.95
DEFAULTS = {"role_sd": 0.30, "eff_sd": 0.12, "quality_sd": 0.08, "evidence": 6.0, "floor": 0.35,
            "team_share": 0.4, "volume_sd": 0.05, "defense_sd": 0.05,
            "breakout_rate": 0.25, "breakout_gain": [1.6, 2.6], "bust_rate": 0.15, "bust_cut": [0.4, 0.7],
            "bench_rate": 0.12, "rookie_bench_rate": 0.8, "season_spread": 1.0}


def settings() -> dict:
    return {**DEFAULTS, **params().get("talent", {})}


def _spread(sd: float, games: float, knobs: dict) -> float:
    return sd * max(knobs["floor"], math.sqrt(knobs["evidence"] / (knobs["evidence"] + max(0.0, games))))


def _lognormal(sd: float, z: float) -> float:
    return math.exp(sd * z - sd ** 2 / 2)


def _level(player: Player, z: np.ndarray, team_z: float, knobs: dict) -> Player:
    """z: (role, efficiency, quality) draws; team_z: the offense's shared efficiency draw."""
    role = _spread(knobs["role_sd"], player.games, knobs)
    eff = _spread(knobs["eff_sd"], player.games, knobs)
    quality = _spread(knobs["quality_sd"], player.games, knobs)
    share = knobs["team_share"]
    eff_x = _lognormal(eff, math.sqrt(1 - share) * z[1] + math.sqrt(share) * team_z)
    role_x = _lognormal(role, z[0])
    return replace(player, targets=player.targets * role_x, carries=player.carries * role_x,
                   ypc=player.ypc * eff_x, rec_td=player.rec_td * eff_x, ypr=player.ypr * eff_x,
                   rush_td=player.rush_td * eff_x, quality=player.quality * _lognormal(quality, z[2]))


def _offseason(player: Player, z: np.ndarray, knobs: dict) -> Player:
    """A whole season's rates: z = one draw per SEASON_RATES (see the module docstring)."""
    model = sim_next_year.stored()
    spread, keep = model["efficiency_sd"].get(player.position, {}), model["efficiency_keep"]
    rates = {}
    for name, draw in zip(SEASON_RATES, z):
        if name in spread:
            sd = spread[name] * knobs["season_spread"] * (1.0 if player.games <= 0 else math.sqrt(1 - keep[name]))
            rates[name] = getattr(player, name) * _lognormal(sd, draw)
    if "catch" in rates:
        rates["catch"] = min(MAX_CATCH, rates["catch"])
    return replace(player, **rates)


def _team(team: Team, z: np.ndarray, knobs: dict) -> Team:
    """A team's true volume and defense: z = (volume, pass defense, rush defense, takeaways) draws."""
    volume, defense = _lognormal(knobs["volume_sd"], z[0]), knobs["defense_sd"]
    return replace(team, targets=team.targets * volume, carries=team.carries * volume,
                   pass_def=team.pass_def * _lognormal(defense, z[1]), rush_def=team.rush_def * _lognormal(defense, z[2]),
                   takeaways=team.takeaways * _lognormal(defense, z[3]))


def _ranked(players: list[Player], position: str) -> list[Player]:
    return sorted((p for p in players if p.position == position), key=lambda p: (-(p.targets + p.carries), p.player_id))


def _changes(team_players: list[Player], dice: np.random.Generator, weeks: tuple[int, int],
             knobs: dict, whole: bool = False) -> list[RoleChange]:
    """This team's dated changes (`whole`: a season simulated from week 1). Always the same number of draws,
    whichever happen."""
    leads, backups = [], []
    for position, count in LEADS.items():
        ranked = _ranked(team_players, position)
        leads += ranked[:count]
        backups += ranked[count:count + 2]
    qbs = sorted((p for p in team_players if p.position == "QB"), key=lambda p: (-p.starts, p.player_id))
    rolls, when = dice.random(3), dice.integers(weeks[0], weeks[1] + 1, 3)
    picks, sizes = dice.random(2), dice.random(2)
    out = []
    if backups and rolls[0] < knobs["breakout_rate"]:
        low, high = knobs["breakout_gain"]
        who = backups[int(picks[0] * len(backups))]
        gain = low + (high - low) * sizes[0]
        out.append(RoleChange(int(when[0]), who.player_id, gain, gain, 1.0, "breakout"))
    if leads and rolls[1] < knobs["bust_rate"]:
        low, high = knobs["bust_cut"]
        who = leads[int(picks[1] * len(leads))]
        cut = low + (high - low) * sizes[1]
        out.append(RoleChange(int(when[1]), who.player_id, cut, cut, 1.0, "bust"))
    prospect = len(qbs) > 1 and whole and qbs[1].games == 0 and qbs[1].starts >= ROOKIE_HOPEFUL
    bench = knobs["bench_rate"] * math.exp((1.0 - qbs[0].quality) / BENCH_QUALITY) if qbs else 0.0
    if len(qbs) > 1 and rolls[2] < (knobs["rookie_bench_rate"] if prospect else bench):
        out.append(RoleChange(int(when[2]), qbs[0].player_id, 1.0, 1.0, BENCHED, "bench"))
    return out


def truth(belief: World, seed: int, from_week: int, last_week: int) -> World:
    """The hidden true World for a season simulated from `from_week` (see the module docstring). Pure and
    deterministic for (belief, seed, from_week, last_week)."""
    knobs = settings()
    names = sorted(belief.teams)
    team_draws = replay_state.rng(seed, "sim", "teams").standard_normal((len(names), 5))
    team_z = {team: team_draws[i] for i, team in enumerate(names)}
    teams = {team: _team(belief.teams[team], team_z[team][1:], knobs) for team in names}
    ids = sorted(belief.players)
    draws = replay_state.rng(seed, "sim", "talent").standard_normal((len(ids), 3))
    players = {pid: _level(belief.players[pid], draws[i], float(team_z.get(belief.players[pid].team, [0.0])[0]), knobs)
               for i, pid in enumerate(ids)}
    if from_week == 1:
        season = replay_state.rng(seed, "sim", "offseason").standard_normal((len(ids), len(SEASON_RATES)))
        players = {pid: _offseason(players[pid], season[i], knobs) for i, pid in enumerate(ids)}
    first, last = from_week + 1, max(from_week + 1, last_week - CHANGE_LAST_WEEKS)
    changes = []
    for team in names:
        team_dice = replay_state.rng(seed, "sim", "changes", team)
        changes += _changes([p for p in players.values() if p.team == team], team_dice, (first, last), knobs,
                            from_week == 1)
    return World(MappingProxyType(players), MappingProxyType(teams),
                 tuple(sorted(changes, key=lambda c: (c.week, c.player_id))))
