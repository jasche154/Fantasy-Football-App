"""Simulated seasons: the hidden truth behind the public belief.

`truth(belief, seed, from_week, last_week, rookies)` returns a World in which every player differs from his
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
  The keep here is the rate's own (unscaled: sim_next_year.keep_of), what a season leaves unexplained even
  though the belief applies only part of the regression. `season_spread` (tuned with `role_sd` on the
  leave-season-out full-mode gate: whole seasons' leaderboards and big games, WR leaders not overshooting).
- Teams: volume (targets and carries together) x lognormal `volume_sd`; pass defense, run defense and
  takeaways each x lognormal `defense_sd`.
- Dated changes (from a uniform week after from_week to CHANGE_LAST_WEEKS before the end): per team and
  season, a breakout (a non-lead receiver or back, `breakout_rate`, weights x `breakout_gain`), a bust (a
  lead one, `bust_rate`, weights x `bust_cut`; never a top-STAR_RANK player at his position by the public's
  rank, sim_market.position_ranks: real stars lose work to injuries, not to a coach's whim) and a QB benching
  (bench_chance: a logistic in the starter's public rank, fitted on real week-1 starters who lost the job
  healthy, sim_next_year_fit: a top-ranked starter almost never, a bottom one about a fifth of the time; x the
  share of the season's change weeks left; the starter's starts weight x BENCHED, so the next QB plays from
  then on). World.at(week) applies them. In a whole season (from week 1)
  a team whose next QB in line is an unproven prospect (no games of evidence, starts weight ROOKIE_HOPEFUL or
  more: a drafted rookie, sim_next_year) benches its starter at `rookie_bench_rate` instead: most such
  rookies start before the season is out (sim_next_year_fit's `qb_takeover`, injuries included).
- Rookie growth (`rookies`: players in their first season; only RBs, WRs and TEs grow, the positions it is
  fitted on): each one's role weights x `gain` from a uniform week around mid-season (split_week - 1 to split_week + 2) with `chance`, both fitted on real rookies against
  veterans with the same first half (sim_next_year.json `rookie_growth`, sim_next_year_calib); only steps after
  from_week happen (a live season's real weeks stay real). No buzz: the public learns it from results.

- Team strength (`strength`): each team's offense and defense differ from the public's line by a hidden
  number of points, normal with sd `strength_sd`, drawn once per season, and the line's edge (a shrunk,
  regression estimate, as a real line is: real margins run about 1.2 x the Vegas spread) is widened by
  `edge_scale` (`edge_scale()`); both tuned so simulated results against the sim's own pregame line look like
  real results against the Vegas line (sim_eval's game metrics). The public learns them only from the scores.

The spreads and rates are tuned so simulated seasons reshuffle like real ones (`sim_eval --set talent.<knob>=...`
against ADP rank vs final rank, breakouts, busts; `--save-tuned` stores them in sim_params.json's tuned "talent"
section).
"""
import math
from collections.abc import Collection, Mapping
from dataclasses import replace
from types import MappingProxyType

import numpy as np

from services import replay_state, sim_market, sim_next_year
from services.sim_world import Player, RoleChange, Team, World, params

BENCHED = 0.01                   # a benched QB's starts weight is multiplied by this
LEADS = {"RB": 1, "WR": 2, "TE": 1}    # this many per position count as a team's lead players
CHANGE_LAST_WEEKS = 2            # no new role change in the season's last weeks
STAR_RANK = 12                   # the public's top this many at a position: no bust, no false rumour
UNRANKED = 32                    # the rank of a player the public does not rank
STRENGTH_SD = 2.0               # default of the tuned `talent.strength_sd` (points)
EDGE_SCALE = 1.05               # default of the tuned `talent.edge_scale`
ROOKIE_HOPEFUL = 0.1             # an unproven QB with this starts weight is a prospect pushing for the job
SEASON_RATES = ("catch", "ypc", "rec_td", "ypr", "rush_td", "quality", "int_rate")
MAX_CATCH = 0.95
DEFAULTS = {"role_sd": 0.30, "eff_sd": 0.12, "quality_sd": 0.08, "evidence": 6.0, "floor": 0.35,
            "team_share": 0.4, "volume_sd": 0.05, "defense_sd": 0.05,
            "breakout_rate": 0.25, "breakout_gain": [1.6, 2.6], "bust_rate": 0.15, "bust_cut": [0.4, 0.7],
            "rookie_bench_rate": 0.8, "season_spread": 1.0}


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
    spread = model["efficiency_sd"].get(player.position, {})
    rates = {}
    for name, draw in zip(SEASON_RATES, z):
        if name in spread:
            keep = sim_next_year.keep_of(player.position, name, scaled=False)    # what the rate alone leaves
            sd = spread[name] * knobs["season_spread"] * (1.0 if player.games <= 0 else math.sqrt(max(0.0, 1 - keep)))
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


def bench_chance(rank: int) -> float:
    """The chance a starting QB the public ranks `rank` at his position (sim_market.position_ranks) loses his
    job healthy over a whole season: a logistic in the rank, fitted on real week-1 starters 2019-2025
    (sim_next_year.json `bench`)."""
    a, b = sim_next_year.stored()["bench"]["logit"]
    return 1.0 / (1.0 + math.exp(-(a + b * rank)))


def _starter(qbs: list[Player]) -> Player:
    return max(qbs, key=lambda p: (p.starts, -p.player_id))


def public_ranks(belief: World) -> dict[int, int]:
    """{player: the public's rank at his position} (sim_market.position_ranks), except that each team's starting
    QB (most starts weight) is ranked among the starting QBs only, as bench_chance is fitted."""
    ranks = sim_market.position_ranks(belief)
    starters = [_starter(qbs) for team in sorted(belief.teams)
                if (qbs := [p for p in belief.roster(team) if p.position == "QB"])]
    order = sorted(starters, key=lambda p: (ranks.get(p.player_id, UNRANKED), p.player_id))
    return {**ranks, **{p.player_id: rank for rank, p in enumerate(order, start=1)}}


def _roles(team_players: list[Player], ranks: Mapping[int, int]) -> tuple[list[Player], list[Player], list[Player]]:
    """(leads who may bust: not a top-STAR_RANK player at his position, backups who may break out, QBs by starts)."""
    leads, backups = [], []
    for position, count in LEADS.items():
        ranked = _ranked(team_players, position)
        leads += [p for p in ranked[:count] if ranks.get(p.player_id, STAR_RANK + 1) > STAR_RANK]
        backups += ranked[count:count + 2]
    return leads, backups, sorted((p for p in team_players if p.position == "QB"), key=lambda p: (-p.starts, p.player_id))


def _changes(team_players: list[Player], dice: np.random.Generator, weeks: tuple[int, int],
             knobs: dict, whole: bool = False, ranks: Mapping[int, int] | None = None,
             share: float = 1.0) -> list[RoleChange]:
    """This team's dated changes (`whole`: a season simulated from week 1; `ranks`: the public's position
    ranks, sim_market.position_ranks; `share`: the share of a whole season's change weeks left). Always the
    same number of draws, whichever happen."""
    leads, backups, qbs = _roles(team_players, ranks or {})
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
    bench = bench_chance((ranks or {}).get(qbs[0].player_id, UNRANKED)) * share if qbs else 0.0
    if len(qbs) > 1 and rolls[2] < (knobs["rookie_bench_rate"] if prospect else bench):
        out.append(RoleChange(int(when[2]), qbs[0].player_id, 1.0, 1.0, BENCHED, "bench"))
    return out


def _share(from_week: int, last_week: int) -> float:
    """The share of a whole season's change weeks left when simulating from `from_week`."""
    first, last = change_weeks(from_week, last_week)
    whole = change_weeks(1, last_week)
    return (last - first + 1) / (whole[1] - whole[0] + 1)


def rumors(belief: World, ranks: Mapping[int, int]) -> dict[str, dict[int, float]]:
    """{kind: {player: weight}} a false buzz line may be about (sim_market.buzz), each as plausible as the
    true changes: a team's backups break out (breakout_rate), its leads who are not stars bust (bust_rate),
    its starting QB is benched (bench_chance of his rank), but never one ranked in the top STAR_RANK: the
    public does not float a job-in-danger rumour about an established star."""
    knobs = settings()
    out: dict[str, dict[int, float]] = {"breakout": {}, "bust": {}, "bench": {}}
    for team in sorted(belief.teams):
        leads, backups, qbs = _roles(list(belief.roster(team)), ranks)
        out["breakout"].update({p.player_id: knobs["breakout_rate"] / len(backups) for p in backups})
        out["bust"].update({p.player_id: knobs["bust_rate"] / len(leads) for p in leads})
        if len(qbs) > 1 and ranks.get(qbs[0].player_id, UNRANKED) > STAR_RANK:
            out["bench"][qbs[0].player_id] = bench_chance(ranks.get(qbs[0].player_id, UNRANKED))
    return out


def expected_changes(belief: World, ranks: Mapping[int, int], from_week: int, last_week: int) -> float:
    """The true changes a season makes on average (sizes the false buzz: sim_market.expected_hints)."""
    knobs, share = settings(), _share(from_week, last_week)
    starters = [_starter(qbs) for team in belief.teams
                if (qbs := [p for p in belief.roster(team) if p.position == "QB"])]
    return (len(belief.teams) * (knobs["breakout_rate"] + knobs["bust_rate"])
            + share * sum(bench_chance(ranks.get(p.player_id, UNRANKED)) for p in starters))


def change_weeks(from_week: int, last_week: int) -> tuple[int, int]:
    """(first, last) week a dated role change may start in (buzz lines run in the same weeks)."""
    return from_week + 1, max(from_week + 1, last_week - CHANGE_LAST_WEEKS)


def growth(rookies: Collection[int], seed: int, from_week: int) -> list[RoleChange]:
    """The rookies' growth steps (see the module docstring): drawn for every rookie, in id order, so a step's
    fate does not depend on the weeks simulated; kept only when it falls after from_week (earlier weeks are
    real, or already simulated without it)."""
    fit = sim_next_year.stored()["rookie_growth"]
    ids = sorted(rookies)
    dice = replay_state.rng(seed, "sim", "growth")
    rolls, weeks = dice.random(len(ids)), dice.integers(fit["split_week"] - 1, fit["split_week"] + 3, len(ids))
    return [RoleChange(int(week), pid, fit["gain"], fit["gain"], 1.0, "growth")
            for pid, roll, week in zip(ids, rolls, weeks) if roll < fit["chance"] and week > from_week]


def truth(belief: World, seed: int, from_week: int, last_week: int, rookies: Collection[int] = ()) -> World:
    """The hidden true World for a season simulated from `from_week` (see the module docstring); `rookies`:
    the players in their first season, who may grow. Pure and deterministic for its arguments."""
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
    first, last = change_weeks(from_week, last_week)
    ranks, share = public_ranks(belief), _share(from_week, last_week)
    changes = []
    for team in names:
        team_dice = replay_state.rng(seed, "sim", "changes", team)
        changes += _changes([p for p in players.values() if p.team == team], team_dice, (first, last), knobs,
                            from_week == 1, ranks, share)
    # growth is fitted on rookie RBs/WRs/TEs only
    changes += growth([pid for pid in rookies if pid in players and players[pid].position in ("RB", "WR", "TE")],
                      seed, from_week)
    return World(MappingProxyType(players), MappingProxyType(teams),
                 tuple(sorted(changes, key=lambda c: (c.week, c.player_id))))


def strength(teams: Collection[str], seed: int) -> dict[str, tuple[float, float]]:
    """{team: (offense, defense)}: the hidden points it scores and allows over what the public's line expects
    (see the module docstring). Its own random stream, so no other draw moves."""
    sd = float(settings().get("strength_sd", STRENGTH_SD))
    names = sorted(teams)
    z = replay_state.rng(seed, "sim", "strength").standard_normal((len(names), 2))
    return {team: (sd * float(z[i, 0]), sd * float(z[i, 1])) for i, team in enumerate(names)}


def edge_scale() -> float:
    """How much wider the truth's margins are than the public line's (see the module docstring)."""
    return float(settings().get("edge_scale", EDGE_SCALE))
