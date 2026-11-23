"""Simulated seasons: the offseason. Turns the public belief at the end of one season into the belief going
into the next (the prior World a whole simulated season starts from, services/sim_season.simulate).

`next_year(world, players, experience, rookies, moves, season_starts)` is pure. Every number below is measured from real
seasons 2019-2025 by services/sim_next_year_fit.py and lives in sim_next_year.json:

- Veterans keep their team unless `moves` sends them elsewhere (a team) or out of the league (None). Each
  one's role volume (targets and carries together) changes by his position's experience curve (young players
  grow, old ones fade: `age`), and a big or small role regresses toward his position's typical one (`keep`
  on the log volume, for relevant roles only: below VOLUME_MIN a role is noise and only ages). Each team's
  returning players are then rescaled to the weight they had, so the curve moves work between teammates and
  never changes a team's total; a moved player brings his weight, a retired one takes his away (shares are
  weight / active teammates' weights at game time). Efficiency regresses toward the position norm
  (`efficiency_keep`: rate = norm x (rate / norm) ^ keep). A moved receiver's rates are re-scaled to his new
  team's usual passer. Everyone is healthy (preseason injuries are drawn where the season is simulated).
  `Player.games` stays the evidence of last season: sim_world.estimate counts a prior season's games at
  PRIOR_GAMES_CARRY, which is what makes August less certain than December.
- Starters. A QB's `starts` weight is a decayed share of recent starts, so a starter hurt late in the season
  (or one who changed teams after it) looks like a backup; in August the healthy incumbent is the starter
  again: on each team (after the moves) the QB with the most starts over the whole season (`season_starts`;
  starting week 1 counts one more, which settles a split with an early fill-in; then the better passer) gets
  starts 1.0 and the others at most VETERAN_BACKUP_STARTS. A team left with no QB or kicker (a retirement)
  signs the best spare one: the backup with the highest starts weight on a team that has two or more.
- Rookies get the role a player drafted at that pick and position really had as a rookie (`rookies`: a line
  in log(pick), not extrapolated above ROOKIE_PICK_FLOOR for non-QBs: the fit has almost none there),
  position-norm efficiency at his team's passer scale, and no games of evidence, so sim_talent.truth draws
  them the widest. A rookie QB is named the starter when his team has no starter
  (nobody with STARTER_ROLE starts) or when his pick makes week 1 likely (week-1 chance ROOKIE_QB_NAMED or
  more) and the incumbent is a below-average passer; otherwise his starts weight is his week-1 chance, which
  puts him next in line (sim_talent's rookie takeover).
- Teams: volume and defenses regress toward the league (`team_keep`); the passer scale and the "other"
  buckets stay.

ADP for the new season is the market's under the new belief (sim_market.adp). Rookie ids: ROOKIE_ID_BASE +
season x 1000 + pick (`rookie_ids`); database ids are autoincrement row ids, far below the base.
"""
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services import sim_market
from services.replay_pool import PoolPlayer, SeasonData
from services.replay_scoring import ScoringRules
from services.schedule_strength import Schedule
from services.sim_offseason import Rookie
from services.sim_world import STARTER_ROLE, Player, Team, World, params, scoring_of

PATH = Path(__file__).with_name("sim_next_year.json")
ROOKIE_ID_BASE = 1_000_000_000          # rookie ids: far above any database row id, below 2^31
ROOKIE_ID_SEASON = 1000                 # ids per season (more than the most picks a draft has)
VOLUME_MIN = {"QB": 3.0, "RB": 3.0, "WR": 2.0, "TE": 1.5}    # opportunities per game (as the fit)
ROOKIE_PICK_FLOOR = 4                   # a non-QB's line is read at this pick or later (few go higher in the fit)
ROOKIE_QB_NAMED = 0.35                  # a rookie QB this likely to start week 1 beats a below-average incumbent
AVERAGE_PASSER = 1.0
VETERAN_BACKUP_STARTS = 0.15            # a veteran backup's starts weight at most (a first-round rookie is ahead)
RECEIVER_RATES = ("ypc", "rec_td")      # measured with his team's usual passer
EFFICIENCY = ("catch", "ypc", "rec_td", "ypr", "rush_td")
POSITIONS = ("QB", "RB", "WR", "TE")


@lru_cache(maxsize=1)
def stored() -> dict:
    """The fitted offseason model (sim_next_year.json)."""
    return json.loads(PATH.read_text(encoding="utf-8"))


def rookie_ids(season: int, rookies: Sequence[Rookie]) -> dict[int, Rookie]:
    """{id: rookie} for a draft class read from sim_data (picks are unique within a season)."""
    return {ROOKIE_ID_BASE + season * ROOKIE_ID_SEASON + r.pick: r for r in rookies}


def _bucket(value: float, edges: Sequence[int]) -> int:
    return max(0, sum(1 for edge in edges[1:] if value >= edge))


def volume(player: Player) -> float:
    """Opportunities per game with every teammate active (targets x league targets + carries x carries)."""
    league = params()["league"]
    return player.targets * league["targets"] + player.carries * league["carries"]


def _role_change(player: Player, experience: int) -> float:
    """The multiplier on his role weights from the experience curve and regression toward the norm."""
    if player.position not in POSITIONS:
        return 1.0
    age = stored()["age"][player.position]
    change = age["log_change"][_bucket(max(1, experience), age["edges"])]
    v = volume(player)
    if v >= VOLUME_MIN[player.position]:
        change += (stored()["keep"][player.position] - 1.0) * (math.log(v) - stored()["center"][player.position])
    return math.exp(change)


def _efficiency(player: Player) -> dict[str, float]:
    keep, norms = stored()["efficiency_keep"], params()["positions"].get(player.position, {})
    league = params()["league"]
    out = {}
    for name in EFFICIENCY:
        norm, value = norms.get(name), getattr(player, name)
        out[name] = norm * (value / norm) ** keep[name] if norm and value > 0 else value
    if player.position == "QB":
        out["quality"] = player.quality ** keep["quality"] if player.quality > 0 else player.quality
        rate = league["int_rate"]
        out["int_rate"] = rate * (player.int_rate / rate) ** keep["int_rate"] if player.int_rate > 0 else rate
    return out


def _veterans(world: World, experience: Mapping[int, int], moves: Mapping[int, str | None]) -> dict[int, Player]:
    aged: dict[int, Player] = {}
    for pid, player in world.players.items():
        team = moves.get(pid, player.team)
        if team is None or team not in world.teams:
            continue                                          # retired, or left the league
        m = _role_change(player, experience.get(pid, 1))
        rates = _efficiency(player)
        if team != player.team and player.position in ("RB", "WR", "TE"):
            scale = world.teams[team].passer / max(world.teams[player.team].passer, 1e-6)
            rates.update({name: rates[name] * scale for name in RECEIVER_RATES})
        aged[pid] = replace(player, team=team, targets=player.targets * m, carries=player.carries * m,
                            out_weeks=0, **rates)
    stayed = [pid for pid, p in aged.items() if p.team == world.players[pid].team]
    totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])     # (team, field): [before, after]
    for pid in stayed:
        for field in ("targets", "carries"):
            totals[(aged[pid].team, field)][0] += getattr(world.players[pid], field)
            totals[(aged[pid].team, field)][1] += getattr(aged[pid], field)
    for pid in stayed:                                        # the curve moves work within a team only
        scale = {field: before / after if after > 0 else 1.0
                 for field in ("targets", "carries") for before, after in [totals[(aged[pid].team, field)]]}
        aged[pid] = replace(aged[pid], targets=aged[pid].targets * scale["targets"],
                            carries=aged[pid].carries * scale["carries"])
    return aged


def _line(position: str, field: str, pick: int) -> float:
    fit = stored()["rookies"].get(position, {}).get(field)
    pick = pick if position == "QB" else max(pick, ROOKIE_PICK_FLOOR)
    return max(0.0, fit[0] + fit[1] * math.log(pick)) if fit else 0.0


def rookie_player(pid: int, rookie: Rookie, team: Team) -> Player:
    """A rookie as the public sees him in August (see the module docstring)."""
    norm, league = params()["positions"].get(rookie.position, {}), params()["league"]
    starts = _line(rookie.position, "starts", rookie.pick) if rookie.position == "K" else 0.0
    if rookie.position == "QB":
        starts = min(1.0, _line("QB", "week1", rookie.pick))
    return Player(pid, rookie.position, team.team, targets=_line(rookie.position, "targets", rookie.pick),
                  carries=_line(rookie.position, "carries", rookie.pick), starts=starts,
                  catch=norm.get("catch", 0.0), ypc=norm.get("ypc", 0.0) * team.passer,
                  rec_td=norm.get("rec_td", 0.0) * team.passer, ypr=norm.get("ypr", 0.0),
                  rush_td=norm.get("rush_td", 0.0),
                  quality=stored()["rookies"]["qb_quality"] if rookie.position == "QB" else 1.0,
                  int_rate=league["int_rate"] if rookie.position == "QB" else 0.0, games=0.0)


def _qb_competition(players: dict[int, Player], rookie: Player) -> None:
    """Name the rookie QB the starter when his team has no starter, or when his week-1 chance is high and the
    incumbent is a below-average passer (see the module docstring). Mutates `players` (local to next_year)."""
    others = [p for p in players.values() if p.team == rookie.team and p.position == "QB" and p.player_id != rookie.player_id]
    incumbent = max(others, key=lambda p: (p.starts, -p.player_id), default=None)
    if incumbent is None or incumbent.starts < STARTER_ROLE or (
            rookie.starts >= ROOKIE_QB_NAMED and incumbent.quality < AVERAGE_PASSER):
        players[rookie.player_id] = replace(rookie, starts=1.0)
        if incumbent is not None:
            players[incumbent.player_id] = replace(incumbent, starts=min(incumbent.starts, VETERAN_BACKUP_STARTS))


def _restore_starters(players: dict[int, Player], season_starts: Mapping[int, Sequence[int]]) -> None:
    """Each team's August starter by whole-season starts (see the module docstring). Mutates `players`."""
    by_team: dict[str, list[Player]] = defaultdict(list)
    for player in players.values():
        if player.position == "QB":
            by_team[player.team].append(player)
    for qbs in by_team.values():
        def claim(p: Player) -> int:
            weeks = season_starts.get(p.player_id, ())
            return len(weeks) + (1 in weeks)

        order = sorted(qbs, key=lambda p: (-claim(p), -p.quality, -p.starts, p.player_id))
        for rank, player in enumerate(order):
            starts = 1.0 if rank == 0 else min(player.starts, VETERAN_BACKUP_STARTS)
            players[player.player_id] = replace(player, starts=starts)


def _fill_vacancies(players: dict[int, Player], teams: Mapping[str, Team]) -> None:
    """A team with no QB or kicker signs the best spare one (see the module docstring). Mutates `players`."""
    for position in ("QB", "K"):
        rosters: dict[str, list[Player]] = defaultdict(list)
        for player in players.values():
            if player.position == position:
                rosters[player.team].append(player)
        for team in sorted(t for t in teams if not rosters.get(t)):
            spares = [p for group in rosters.values() if len(group) > 1
                      for p in sorted(group, key=lambda p: (-p.starts, p.player_id))[1:]]
            if not spares:
                continue
            signed = max(spares, key=lambda p: (p.starts, -p.player_id))
            rosters[signed.team].remove(signed)
            players[signed.player_id] = replace(signed, team=team)
            rosters[team].append(players[signed.player_id])


def _teams(world: World) -> dict[str, Team]:
    keep, league = stored()["team_keep"], params()["league"]

    def toward(value: float, norm: float, k: float) -> float:
        return norm * (value / norm) ** k if value > 0 else norm

    return {name: replace(team, targets=toward(team.targets, league["targets"], keep["targets"]),
                          carries=toward(team.carries, league["carries"], keep["carries"]),
                          pass_def=toward(team.pass_def, 1.0, keep["pass_def"]),
                          rush_def=toward(team.rush_def, 1.0, keep["rush_def"]),
                          takeaways=toward(team.takeaways, 1.0, keep["takeaways"]))
            for name, team in world.teams.items()}


def next_year(world: World, players: Mapping[int, PoolPlayer], experience: Mapping[int, int],
              rookies: Mapping[int, Rookie], moves: Mapping[int, str | None],
              season_starts: Mapping[int, Sequence[int]] | None = None) -> tuple[World, dict[int, PoolPlayer]]:
    """(the belief going into next season, its player pool with the market's ADP) from the belief at the end
    of this one. `players`: names and positions of the world's players; `experience`: seasons each has played
    through this one; `rookies`: {id: rookie} (rookie_ids); `moves`: {id: new team, or None = gone};
    `season_starts`: the weeks each QB started this season (sim_metrics.qb_starts)."""
    teams = _teams(world)
    new = _veterans(world, experience, moves)
    _restore_starters(new, season_starts or {})
    _fill_vacancies(new, teams)
    for pid, rookie in sorted(rookies.items()):
        if rookie.team in teams and pid not in new:
            new[pid] = rookie_player(pid, rookie, teams[rookie.team])
    for pid in sorted(rookies):
        if pid in new and new[pid].position == "QB":
            _qb_competition(new, new[pid])
    belief = World(MappingProxyType(new), MappingProxyType(teams))
    market = sim_market.adp(belief)
    pool = {}
    for pid, player in new.items():
        known = players.get(pid)
        name = known.name if known is not None else rookies[pid].name
        adp, stdev = market.get(pid, (None, None))
        pool[pid] = PoolPlayer(pid, name, player.position, adp, stdev, player.team)
    return belief, pool


def season_base(season: int, belief: World, pool: Mapping[int, PoolPlayer], schedule: Schedule, scoring: str,
                rules: ScoringRules | None, defense_prior: Mapping[tuple[str, str], float]) -> SeasonData:
    """The season before anything happened, ready for sim_season.simulate(base, seed, 1, prior=belief): the
    pool (each player's team is his PoolPlayer.adp_team, as nothing is played), the schedule, and a week-1
    projection for every player of the belief (the market's for those who dress, MIN_PROJECTION for backups
    and depth), so all of them exist from the start: SeasonData.as_of admits a player with an ADP, a
    projection or a game, and a backup QB or a WR6 must be there to step in. simulate replaces week 1's
    projections with its own. No games, tags or ranks."""
    empty = MappingProxyType({})
    base = SeasonData(season=season, scoring=scoring, players=MappingProxyType(dict(pool)), schedule=schedule,
                      actual=empty, projected=empty, team_by_week=empty,
                      last_week=max(game.week for game in schedule.games),
                      defense_prior=MappingProxyType(dict(defense_prior)), rules=rules, adp_format="ppr")
    week_one = sim_market.projections(belief, schedule, 1, (), scoring_of(base))
    return replace(base, projected=MappingProxyType({(pid, 1): week_one.get(pid, sim_market.MIN_PROJECTION)
                                                     for pid in belief.players}))
