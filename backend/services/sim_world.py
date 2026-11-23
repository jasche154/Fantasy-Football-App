"""Simulated seasons: what a set of NFL teams and players "is" (a World), and the public belief of one.

A World is everything the game simulator (services/sim_game.py) needs to play any game:

- per team: pass targets and rushing attempts per game, an "other" bucket for players outside the pool,
  the defense's effect on opponents (yards and TDs per catch / per carry, takeaways) and the quality of its
  usual passer (receivers' rates are measured with him, so a QB change moves them by the ratio);
- per player: ROLE WEIGHTS, not fixed shares. In a game a player's share of his team's targets is his
  weight / the weights of the teammates who are active, so an injured player's work flows to the others,
  a rookie dilutes everyone and a QB change moves every receiver. QBs and kickers have a `starts` weight:
  the active one with the highest weight plays. Efficiency (catch rate, yards per catch, TDs per catch,
  yards and TDs per carry, a QB's quality and INT rate) is per player.

`estimate(data, week, prior)` is the PUBLIC belief before `week`, read from `data.as_of(week)` only:

- Role weights are a Luce fit of each team's played games (`_luce`, Hunter's MM algorithm): in a game the
  counts follow the weights of the players who played it, a game a week older counts DECAY as much, and
  ROLE_PRIOR_GAMES pseudo-games hold every player at his prior share. The prior share is the `prior`
  World's (last season, carried over by sim_next_year), else the one his earliest known projections imply
  (projection / his position's points per opportunity / the league's team volume), else his ADP's (the
  Nth by ADP at a position gets the Nth value of `adp_curve`), else a depth share. Not the prior World
  averaged with projections: in a simulated season the projections come from the belief itself, and
  averaging them back in pulls every role toward an average player's (a rushing QB's carries toward the
  norm, an efficient receiver's targets up) a little more each week.
- Efficiency is his own rates shrunk toward the prior World's value (else the position norm) by
  EFFICIENCY_PRIOR pseudo-volume. Team volume and defense factors are decayed means of its games shrunk
  toward the prior World / league (last season's `defense_prior` stands in when there is no prior World).
- `out_weeks`: a hard tag known before `week` means IR_BELIEF_WEEKS (IR, PUP) or 1 more game out.

Constants measured from real seasons live in sim_params.json (`python -m services.sim_eval --fit`).
"""
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from pathlib import Path
from statistics import median
from types import MappingProxyType

import numpy as np

from services import replay_scoring
from services.replay_pool import SeasonData
from services.scoring import ScoringSettings, reception_points

PARAMS_PATH = Path(__file__).with_name("sim_params.json")
OTHER = 0                        # member id of a team's "other" bucket in role fits
DECAY = 0.9                      # a game one week older counts this much less in the role fit
ROLE_PRIOR_GAMES = 2.5           # pseudo-games of the prior share in the role fit
TEAM_PRIOR_GAMES = 2.0           # pseudo-games of the prior team volume / defense
EFFICIENCY_PRIOR = {"catch": 30.0, "ypc": 25.0, "rec_td": 30.0, "ypr": 50.0, "rush_td": 60.0,
                    "quality": 150.0, "int_rate": 250.0}    # pseudo targets / catches / carries / attempts
LUCE_ITERATIONS = 25
MIN_WEIGHT = 1e-4
MIN_SHARE_FOR_TEAM_TOTAL = 0.05  # target shares below this are too rough to back out the team's targets
IR_BELIEF_WEEKS = 4
STARTER_PROJECTION = {"QB": 9.0, "K": 3.0}   # a projection this high means he is expected to start
STARTER_ROLE = 0.5               # a QB whose prior starts weight is this high is expected to start
EARLY_PROJECTIONS = 3           # his first projections are the prior (later ones echo the fit itself)
PRIOR_GAMES_CARRY = 0.25         # last season's games count this much as evidence for this season
RECEIVERS = ("RB", "WR", "TE")


@dataclass(frozen=True)
class Player:
    player_id: int
    position: str
    team: str
    targets: float = 0.0         # role weight for targets (about his share of team targets with all active)
    carries: float = 0.0         # role weight for rushing attempts
    starts: float = 0.0          # QB / K: the active one with the highest weight plays
    catch: float = 0.0           # catches per target
    ypc: float = 0.0             # yards per catch (with his team's usual passer)
    rec_td: float = 0.0          # TDs per catch
    ypr: float = 0.0             # yards per carry
    rush_td: float = 0.0         # TDs per carry
    quality: float = 1.0         # QB: his receivers' yards and TDs per catch x this (1 = league average)
    int_rate: float = 0.0        # QB: interceptions per pass attempt
    games: float = 0.0           # games of evidence behind the estimate
    out_weeks: int = 0           # believed games still to miss (0 = healthy)


@dataclass(frozen=True)
class Team:
    team: str
    targets: float               # pass targets per game
    carries: float               # rushing attempts per game
    other_targets: float         # role weight of players outside the pool
    other_carries: float
    pass_def: float = 1.0        # opponents' yards and TDs per catch x this
    rush_def: float = 1.0        # opponents' yards and TDs per carry x this
    takeaways: float = 1.0       # sacks, INTs and fumbles it forces x this
    passer: float = 1.0          # quality of its usual passer


@dataclass(frozen=True)
class RoleChange:
    """From `week` on, the player's weights are multiplied (a breakout, a bust, a benching)."""
    week: int
    player_id: int
    targets: float = 1.0
    carries: float = 1.0
    starts: float = 1.0
    kind: str = "role"           # "breakout" | "bust" | "bench"


@dataclass(frozen=True)
class World:
    players: Mapping[int, Player]
    teams: Mapping[str, Team]
    changes: tuple[RoleChange, ...] = ()       # truth only; a belief has none

    def at(self, week: int) -> "World":
        """The world with every role change dated `week` or earlier applied (none due: itself)."""
        due = [c for c in self.changes if c.week <= week]
        if not due:
            return self
        players = dict(self.players)
        for change in due:
            p = players.get(change.player_id)
            if p is not None:
                players[change.player_id] = replace(p, targets=p.targets * change.targets,
                                                    carries=p.carries * change.carries,
                                                    starts=p.starts * change.starts)
        return World(MappingProxyType(players), self.teams, ())

    def roster(self, team: str) -> list[Player]:
        return list(self._rosters.get(team, ()))

    @cached_property
    def _rosters(self) -> dict[str, tuple[Player, ...]]:
        grouped: dict[str, list[Player]] = defaultdict(list)
        for player in self.players.values():
            grouped[player.team].append(player)
        return {team: tuple(players) for team, players in grouped.items()}


@lru_cache(maxsize=1)
def params() -> Mapping:
    """The constants in sim_params.json: the measured sections, each overlaid with its `tuned` values
    (constants set by matching simulated seasons to real ones, `sim_eval --tune`)."""
    stored = json.loads(PARAMS_PATH.read_text())
    tuned = stored.pop("tuned", {})
    return {name: {**value, **tuned.get(name, {})} if isinstance(value, dict) else value
            for name, value in {**{k: {} for k in tuned}, **stored}.items()}


def scoring_of(data: SeasonData) -> ScoringSettings:
    return replay_scoring.scoring_settings(data.scoring, data.rules)


def unit_points(position: str, scoring: ScoringSettings) -> tuple[float, float]:
    """(points per target, points per carry) of an average player at the position."""
    norm = params()["positions"][position]
    per_target = norm["catch"] * (reception_points(position, scoring) + norm["ypc"] * scoring.rec_yd
                                  + norm["rec_td"] * scoring.rec_td)
    return per_target, norm["ypr"] * scoring.rush_yd + norm["rush_td"] * scoring.rush_td


def implied_role(position: str, points: float, scoring: ScoringSettings) -> tuple[float, float, float]:
    """(target weight, carry weight, starts) a per-game projection implies for an average player."""
    league, norm = params()["league"], params()["positions"].get(position)
    if position in ("K", "DST") or norm is None:
        return 0.0, 0.0, 1.0 if points >= STARTER_PROJECTION.get(position, 0.0) else 0.1
    if position == "QB":
        starter = points >= STARTER_PROJECTION["QB"]
        return 0.0, norm["starter_carries"] if starter else 0.0, 1.0 if starter else norm["backup_starts"]
    per_target, per_carry = unit_points(position, scoring)
    ratio = norm["carries_per_target"]                       # his carries for each target he gets
    targets = max(0.0, points) / (per_target + ratio * per_carry)
    return targets / league["targets"], targets * ratio / league["carries"], 0.0


def _luce(games: list[tuple[float, dict[int, float]]], prior: Mapping[int, float], scale: float) -> dict[int, float]:
    """Luce weights from games [(decay weight, {member: count})] plus ROLE_PRIOR_GAMES pseudo-games in which
    each member's expected count is prior share x scale. Members with no game keep their prior."""
    members = sorted({*prior, *(m for _, counts in games for m in counts)})
    index = {m: i for i, m in enumerate(members)}
    games = [(decay, counts) for decay, counts in games if sum(counts.values()) > 0]
    played = np.zeros((len(games), len(members)))          # 1 where the member played that game
    counts = np.zeros((len(games), len(members)))
    for g, (_, game) in enumerate(games):
        for member, count in game.items():
            played[g, index[member]] = 1.0
            counts[g, index[member]] = count
    decay = np.array([d for d, _ in games])
    totals = decay * counts.sum(axis=1)
    pseudo = ROLE_PRIOR_GAMES * scale
    shares = np.array([prior.get(m, 0.0) for m in members])
    numerators = decay @ counts + pseudo * shares if games else pseudo * shares
    weights = np.maximum(shares, MIN_WEIGHT)
    for _ in range(LUCE_ITERATIONS if games else 0):
        exposure = (totals / (played @ weights)) @ played
        weights = np.maximum(MIN_WEIGHT, numerators / (exposure + pseudo))
    return dict(zip(members, weights.tolist()))


def _starts(games: list[tuple[float, dict[int, float]]], prior: Mapping[int, float]) -> dict[int, float]:
    """Decayed share of the team's games each player started (a game's counts: QB passing yards, K kicks),
    with ROLE_PRIOR_GAMES pseudo-games at his prior. One starter per game makes a Luce fit flat, so not that."""
    numerators = {member: ROLE_PRIOR_GAMES * share for member, share in prior.items()}
    total_weight = ROLE_PRIOR_GAMES
    for decay, counts in games:
        total = sum(counts.values())
        if total > 0:
            total_weight += decay
            for member, count in counts.items():
                numerators[member] = numerators.get(member, 0.0) + decay * count / total
    return {member: value / total_weight for member, value in numerators.items()}


def _shrink(total: float, volume: float, norm: float, strength: float) -> float:
    return (total + norm * strength) / (volume + strength)


# ---------------- the belief ----------------

@dataclass(frozen=True)
class _Seen:
    """The played games of one as_of copy, grouped for the fits."""
    games: Mapping[tuple[str, int], Mapping[int, object]]     # (team, week) -> {player: BoxLine}
    targets: Mapping[tuple[str, int], float]                  # team pass targets in that game
    kicks: Mapping[tuple[str, int], tuple[int, ...]]          # kickers who played it
    pass_yards: Mapping[tuple[str, int], float]               # team passing yards in that game


def _seen(known: SeasonData, week: int) -> _Seen:
    games: dict[tuple[str, int], dict[int, object]] = defaultdict(dict)
    kicks: dict[tuple[str, int], list[int]] = defaultdict(list)
    for pid, played in known.team_by_week.items():
        position = known.players[pid].position if pid in known.players else None
        for game_week, team in played:
            if game_week >= week:
                continue
            if (pid, game_week) in known.box:
                games[(team, game_week)][pid] = known.box[(pid, game_week)]
            elif position == "K" and (pid, game_week) in known.actual:
                kicks[(team, game_week)].append(pid)
    targets = {}
    for key, lines in games.items():
        backed = [u.targets / u.target_share for pid in lines if (u := known.usage.get((pid, key[1])))
                  and u.targets and (u.target_share or 0) >= MIN_SHARE_FOR_TEAM_TOTAL]
        pooled = float(sum(line.targets for line in lines.values()))
        targets[key] = max(pooled, median(backed)) if backed else pooled
    passing = {key: float(sum(max(0, line.pass_yds) for line in lines.values())) for key, lines in games.items()}
    return _Seen(games, targets, {key: tuple(v) for key, v in kicks.items()}, passing)


def _first_projections(known: SeasonData) -> dict[int, float]:
    weeks: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for (pid, week), points in known.projected.items():
        weeks[pid].append((week, points))
    return {pid: sum(p for _, p in sorted(v)[:EARLY_PROJECTIONS]) / len(sorted(v)[:EARLY_PROJECTIONS])
            for pid, v in weeks.items()}


def _adp_points(known: SeasonData) -> dict[int, float]:
    curve, out = params()["adp_curve"][known.scoring], {}
    by_position: dict[str, list] = defaultdict(list)
    for player in known.players.values():
        if player.adp is not None:
            by_position[player.position].append((player.adp, player.player_id))
    for position, ranked in by_position.items():
        points = curve.get(position, [0.0])
        for rank, (_, pid) in enumerate(sorted(ranked)):
            out[pid] = points[min(rank, len(points) - 1)]
    return out


def _prior_roles(known: SeasonData, team_of: Mapping[int, str], prior: World | None,
                 scoring: ScoringSettings) -> dict[int, tuple[float, float, float]]:
    projected, adp = _first_projections(known), _adp_points(known)
    norms, out = params()["positions"], {}
    for pid in team_of:
        position = known.players[pid].position
        guesses = []
        if prior is not None and pid in prior.players:
            p = prior.players[pid]
            guesses.append((p.targets, p.carries, p.starts))
        elif pid in projected:
            guesses.append(implied_role(position, projected[pid], scoring))
        if not guesses and pid in adp:
            guesses.append(implied_role(position, adp[pid], scoring))
        if not guesses:
            norm = norms.get(position, {})
            guesses.append((norm.get("depth_targets", 0.0), norm.get("depth_carries", 0.0), 0.02))
        out[pid] = tuple(sum(g[i] for g in guesses) / len(guesses) for i in range(3))
    return out


def _team_roles(team: str, seen: _Seen, members: list[int], roles: Mapping[int, tuple[float, float, float]],
                positions: Mapping[int, str], week: int, other: tuple[float, float]) -> dict[str, dict[int, float]]:
    """{"targets"|"carries"|"starts": {player: weight}} for one team's current players (OTHER included)."""
    league = params()["league"]
    target_games, carry_games, pass_games, kick_games = [], [], [], []
    for (game_team, game_week), lines in seen.games.items():
        if game_team != team:
            continue
        decay = DECAY ** max(0, week - 1 - game_week)
        counts = {pid: float(line.targets) for pid, line in lines.items() if positions.get(pid) != "QB"}
        counts[OTHER] = max(0.0, seen.targets[(team, game_week)] - sum(counts.values()))
        target_games.append((decay, counts))
        carry_games.append((decay, {**{pid: float(line.carries) for pid, line in lines.items()}, OTHER: 0.0}))
        passing = {pid: float(max(0, line.pass_yds)) for pid, line in lines.items() if positions.get(pid) == "QB"}
        if passing:
            pass_games.append((decay, passing))
    for (game_team, game_week), kickers in seen.kicks.items():
        if game_team == team:
            kick_games.append((DECAY ** max(0, week - 1 - game_week), dict.fromkeys(kickers, 1.0)))
    receivers = {pid: roles[pid][0] for pid in members if positions[pid] in RECEIVERS}
    rushers = {pid: roles[pid][1] for pid in members if positions[pid] in (*RECEIVERS, "QB")}
    qbs = {pid: roles[pid][2] for pid in members if positions[pid] == "QB"}
    kickers = {pid: roles[pid][2] for pid in members if positions[pid] == "K"}
    return {"targets": _luce(target_games, {**receivers, OTHER: other[0]}, league["targets"]),
            "carries": _luce(carry_games, {**rushers, OTHER: other[1]}, league["carries"]),
            "starts": {**_starts(pass_games, qbs), **_starts(kick_games, kickers)}}


BOX_KEYS = ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "rush_td", "pass_yds", "pass_int")


def _efficiency(seen: _Seen, games: list[tuple[str, int]], pid: int, position: str,
                prior: World | None, starter: bool) -> dict[str, float]:
    """His rates shrunk toward the prior. A QB's attempts in a game are his share of the team's passing
    yards x the team's targets x attempts per target (the box lines carry no attempts). A QB with no prior
    is a league-average passer if expected to start, else a typical backup."""
    league = params()["league"]
    norm = {"quality": 1.0 if starter else league["backup_quality"], "int_rate": league["int_rate"],
            **params()["positions"].get(position, {})}
    if prior is not None and pid in prior.players:
        old = prior.players[pid]
        norm.update(catch=old.catch, ypc=old.ypc, rec_td=old.rec_td, ypr=old.ypr, rush_td=old.rush_td,
                    quality=old.quality, int_rate=old.int_rate)
    totals: dict[str, float] = defaultdict(float)
    for key in games:
        line = seen.games.get(key, {}).get(pid)
        if line is None:
            continue
        totals["games"] += 1
        for name in BOX_KEYS:
            totals[name] += getattr(line, name)
        team_yards = seen.pass_yards[key]
        if line.pass_yds > 0 and team_yards > 0:
            totals["attempts"] += line.pass_yds / team_yards * seen.targets[key] * league["attempts_per_target"]
    k = EFFICIENCY_PRIOR
    per_attempt = league["pass_yards"] / (league["targets"] * league["attempts_per_target"])
    return {"catch": _shrink(totals["rec"], totals["targets"], norm.get("catch", 0.0), k["catch"]),
            "ypc": _shrink(totals["rec_yds"], totals["rec"], norm.get("ypc", 0.0), k["ypc"]),
            "rec_td": _shrink(totals["rec_td"], totals["rec"], norm.get("rec_td", 0.0), k["rec_td"]),
            "ypr": _shrink(totals["rush_yds"], totals["carries"], norm.get("ypr", 0.0), k["ypr"]),
            "rush_td": _shrink(totals["rush_td"], totals["carries"], norm.get("rush_td", 0.0), k["rush_td"]),
            "quality": _shrink(max(0.0, totals["pass_yds"]) / per_attempt, totals["attempts"],
                               norm.get("quality", 1.0), k["quality"]),
            "int_rate": _shrink(totals["pass_int"], totals["attempts"], norm.get("int_rate", 0.0), k["int_rate"]),
            "games": totals["games"]}


def estimate(data: SeasonData, week: int, prior: World | None = None) -> World:
    """The public belief before `week` (see the module docstring). Pure; reads data.as_of(week) only."""
    from services.sim_teams import team_forms, usual_passer     # it imports this module

    known = data.as_of(week)
    scoring = scoring_of(known)
    schedule_teams = {team for team, _ in known.schedule.opponents}
    team_of: dict[int, str] = {}
    for pid in known.players:
        team = known.team_at(pid, week)
        if team not in schedule_teams and prior is not None and pid in prior.players:
            team = prior.players[pid].team
        if team in schedule_teams:
            team_of[pid] = team
    positions = {pid: known.players[pid].position for pid in team_of}
    seen = _seen(known, week)
    roles = _prior_roles(known, team_of, prior, scoring)
    teams = team_forms(known, week, seen, prior)
    tags = {pid: injury.tag for (pid, tag_week), injury in known.injuries.items() if tag_week == week}
    players = {}
    for team in sorted(schedule_teams):
        members = sorted(pid for pid, t in team_of.items() if t == team)
        fitted = _team_roles(team, seen, members, roles, positions, week,
                             (teams[team].other_targets, teams[team].other_carries))
        teams[team] = replace(teams[team], other_targets=fitted["targets"][OTHER],
                              other_carries=fitted["carries"][OTHER])
        for pid in members:
            played = [(t, w) for w, t in known.team_by_week.get(pid, ()) if w < week]
            rates = _efficiency(seen, played, pid, positions[pid], prior, roles[pid][2] >= STARTER_ROLE)
            carried = prior.players[pid].games * PRIOR_GAMES_CARRY if prior and pid in prior.players else 0.0
            players[pid] = Player(
                pid, positions[pid], team, fitted["targets"].get(pid, 0.0), fitted["carries"].get(pid, 0.0),
                fitted["starts"].get(pid, 0.0), rates["catch"], rates["ypc"], rates["rec_td"], rates["ypr"],
                rates["rush_td"], rates["quality"], rates["int_rate"], rates["games"] + carried,
                believed_out(tags.get(pid)))
    teams = {team: replace(form, passer=usual_passer(team, seen, players, week)) for team, form in teams.items()}
    return World(MappingProxyType(players), MappingProxyType(teams))


def believed_out(tag: str | None) -> int:
    if tag in ("IR", "PUP"):
        return IR_BELIEF_WEEKS
    return 1 if tag in ("O", "SUS") else 0
