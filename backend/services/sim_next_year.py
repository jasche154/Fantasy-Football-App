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
  (rate = norm x (rate / norm) ^ keep, keep = keep_of: 1 - `efficiency_scale` x (1 - `efficiency_keep`); a
  QB's rushing has its own keeps, "QB ypr" / "QB rush_td"). A receiver's yards and TDs per catch come with
  his team's usual passer: that is divided out before the regression and his August starter's (regressed)
  quality multiplied back in (`_with_passers`, which also makes him the team's passer), so the QB's part
  regresses once, with the QB, and a QB change moves his receivers. Everyone is healthy (preseason injuries
  are drawn where the season is simulated).
  `Player.games` stays the evidence of last season: sim_world.estimate counts a prior season's games at
  PRIOR_GAMES_CARRY, which is what makes August less certain than December.
- Starters. A QB's `starts` weight is a decayed share of recent starts, so a starter hurt late in the season
  (or one who changed teams after it) looks like a backup; in August the healthy incumbent is the starter
  again: on each team (after the moves) the QB with the most starts over the whole season (`season_starts`,
  starts_by_team, each start labelled with that game's team, and the games a starter missed hurt counted as his:
  starts for his team plus those for a team he was
  the main starter of, so a fill-in elsewhere does not take the job from the team's own starter while a signed
  starter does; a QB traded in mid-season keeps his old team's starts too, unless his new team's main starter
  started its week 1 and is still there (Brissett behind Lamar); starting week 1 counts one
  more, which settles a split with an early fill-in; then the better passer) gets
  starts 1.0 and the others at most VETERAN_BACKUP_STARTS. A team left with no QB or kicker (a retirement)
  signs the best spare one: the backup with the highest starts weight on a team that has two or more.
- Rookies get the role a player drafted at that pick and position really had as a rookie (`rookies`: a line
  in log(pick), not extrapolated above ROOKIE_PICK_FLOOR for non-QBs: the fit has almost none there),
  position-norm efficiency at his team's passer scale, and no games of evidence, so sim_talent.truth draws
  them the widest. A rookie QB is named the starter when his team has no starter
  (nobody with STARTER_ROLE starts), or, when the incumbent is not an established starter (sim_qb.established_qbs
  on the veterans' preseason ranks: the user's choice, a rookie takes over from weak starters only), when his pick makes week 1 very likely (week-1 chance ROOKIE_QB_FRANCHISE or more),
  or likely (ROOKIE_QB_NAMED) and the incumbent is a below-average passer; otherwise his starts weight is his
  week-1 chance, which puts him next in line when the holder is hurt or benched (sim_qb_jobs). A rookie with `hype` (offseason file) gets his role x
  hype ^ HYPE_ROLE (1: the coaches buy the hype; tuned so Jeremiah Smith, hype 1.2, finishes a top-12 WR
  in about 35% of league seeds, top-24 55%, outside the top 36 20%); the market's premium is x hype as well.
- Teams: volume and defenses regress toward the league (`team_keep`); the passer scale and the "other"
  buckets stay.

ADP for the new season is the market's under the new belief in the league's format (`season_base`: its scoring,
the 2-QB list for a superflex league; sim_market.adp). The market pays for a rookie's upside where the belief
does not: his value x `rookie_premium` = max(1, exp(a + b x log(pick))) per position (pick held to
PREMIUM_PICK_CAP; b <= 0), fitted on all of 2019-2025 at once so the market's ADP of each real rookie class
(built like a future season from the season before) best matches the real ADP (sim_next_year_fit), then checked
on held-out seasons. The fitted premium is flat by position (b = 0: the same at every pick; none for a QB).
Only the price moves: his role and talent draw are as above. Rookie ids: ROOKIE_ID_BASE + season x 1000 + pick
(`rookie_ids`); database ids are autoincrement row ids, far below the base.
"""
import math
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services import sim_market, sim_metrics, sim_qb
from services.replay_adp_format import TWO_QB
from services.replay_pool import PoolPlayer, SeasonData
from services.replay_scoring import ScoringRules
from services.schedule_strength import Schedule
from services.sim_constants import fitted_json
from services.sim_offseason import Rookie
from services.sim_world import STARTER_ROLE, Player, Team, World, params, scoring_of

PATH = Path(__file__).with_name("sim_next_year.json")
ROOKIE_ID_BASE = 1_000_000_000          # rookie ids: far above any database row id, below 2^31
ROOKIE_ID_SEASON = 1000                 # ids per season (more than the most picks a draft has)
VOLUME_MIN = {"QB": 3.0, "RB": 3.0, "WR": 2.0, "TE": 1.5}    # opportunities per game (as the fit)
ROOKIE_PICK_FLOOR = 4                   # a non-QB's line is read at this pick or later (few go higher in the fit)
ROOKIE_QB_NAMED = 0.35                  # a rookie QB this likely to start week 1 beats a below-average incumbent
ROOKIE_QB_FRANCHISE = 0.5               # ... and this likely (about the top 5 picks) beats any incumbent: 2022-2025
                                        # named 7 week-1 rookie starters, as many as really started (1.75 a season)
AVERAGE_PASSER = 1.0
VETERAN_BACKUP_STARTS = 0.15            # a veteran backup's starts weight at most (a first-round rookie is ahead)
PREMIUM_PICK_CAP = 100                  # rookie_premium: picks past this one price like it
HYPE_ROLE = 1.0                         # a hyped rookie's role x hype ^ this (tuned: see the module docstring)
RECEIVER_RATES = ("ypc", "rec_td")      # measured with his team's usual passer
RECEIVERS = ("RB", "WR", "TE")
EFFICIENCY = ("catch", "ypc", "rec_td", "ypr", "rush_td")
POSITIONS = ("QB", "RB", "WR", "TE")


@lru_cache(maxsize=1)
def stored() -> dict:
    """The fitted offseason model (sim_next_year.json)."""
    return fitted_json(PATH, "python -m services.sim_next_year_fit --fit",
                       ("age", "keep", "center", "efficiency_keep", "efficiency_sd", "rookies", "team_keep",
                        "rookie_premium", "efficiency_scale", "rookie_growth"))


def rookie_ids(season: int, rookies: Sequence[Rookie]) -> dict[int, Rookie]:
    """{id: rookie} for a draft class read from sim_data (picks are unique within a season)."""
    return {ROOKIE_ID_BASE + season * ROOKIE_ID_SEASON + r.pick: r for r in rookies}


def premium_at(fit: Mapping[str, Sequence[float]], position: str, pick: int) -> float:
    """The market's multiplier on a rookie's value: max(1, exp(a + b x log(pick))) with [a, b] = fit[position],
    the pick held to 1..PREMIUM_PICK_CAP (the fit has few rookies past it); 1 for a position it lacks."""
    a, b = fit.get(position, (0.0, 0.0))
    return max(1.0, math.exp(a + b * math.log(min(max(pick, 1), PREMIUM_PICK_CAP))))


def rookie_premium(rookies: Mapping[int, Rookie]) -> dict[int, float]:
    """{id: the preseason market's multiplier on his value} for `rookies` ({id: rookie}): real drafters pay
    for a rookie's upside (sim_market.adp's `premium`; fitted on real rookie ADPs, `rookie_premium` in
    sim_next_year.json). The belief and the truth never see it."""
    fit = stored()["rookie_premium"]
    return {pid: premium_at(fit, r.position, r.pick) * r.hype for pid, r in rookies.items()}


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


def keep_of(position: str, name: str, scaled: bool = True) -> float:
    """A rate's efficiency_keep: the position's own when fitted (a QB's rushing), else the shared one; `scaled`:
    as the belief applies it, 1 - efficiency_scale x (1 - keep) (sim_next_year_calib)."""
    keep = stored()["efficiency_keep"]
    raw = keep.get(f"{position} {name}", keep[name])
    return 1.0 - stored()["efficiency_scale"].get(position, 1.0) * (1.0 - raw) if scaled else raw


def _efficiency(player: Player, passer: float = AVERAGE_PASSER) -> dict[str, float]:
    """His rates regressed toward the norm. A receiver's RECEIVER_RATES come out at an average passer (his
    `passer`, his team's usual one, divided out first: it regresses with that QB's quality instead)."""
    norms = params()["positions"].get(player.position, {})
    league = params()["league"]
    out = {}
    for name in EFFICIENCY:
        norm, value = norms.get(name), getattr(player, name)
        if name in RECEIVER_RATES and player.position in RECEIVERS:
            value /= max(passer, 1e-6)
        out[name] = norm * (value / norm) ** keep_of(player.position, name) if norm and value > 0 else value
    if player.position == "QB":
        out["quality"] = player.quality ** keep_of("QB", "quality") if player.quality > 0 else player.quality
        rate = league["int_rate"]
        out["int_rate"] = rate * (player.int_rate / rate) ** keep_of("QB", "int_rate") if player.int_rate > 0 else rate
    return out


def _veterans(world: World, experience: Mapping[int, int], moves: Mapping[int, str | None]) -> dict[int, Player]:
    aged: dict[int, Player] = {}
    for pid, player in world.players.items():
        team = moves.get(pid, player.team)
        if team is None or team not in world.teams:
            continue                                          # retired, or left the league
        m = _role_change(player, experience.get(pid, 1))
        rates = _efficiency(player, world.teams[player.team].passer)
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


DEPTH_TOP = {"targets": 5, "carries": 2}    # a team's top receivers and backs: the rest are its depth


def _concentrate(new: dict[int, Player]) -> None:
    """Within each team, the depth's targets (beyond its DEPTH_TOP receivers) and carries (beyond its top two
    non-QB rushers) x the tuned talent `depth_targets` / `depth_carries`, the rest going to the top ones in
    proportion (the team's total kept). A belief carried over from the end of a season spreads a team's work
    too thinly (late-season fill-ins, depth guesses, rookies joining): real teams' top five receivers see 74% of
    the targets, a whole simulated season built without it 71%, and too few receivers reach 1,000 yards
    (sim_metrics_shape, sim_eval full mode)."""
    talent = params().get("talent", {})
    for field, top in DEPTH_TOP.items():
        scale = talent.get(f"depth_{field}", 1.0)
        if scale == 1.0:
            continue
        rooms: dict[str, list[int]] = defaultdict(list)
        for pid, p in new.items():
            if p.position in POSITIONS and p.position != "QB" and getattr(p, field) > 0:
                rooms[p.team].append(pid)
        for pids in rooms.values():
            ranked = sorted(pids, key=lambda pid: (-getattr(new[pid], field), pid))
            lead = sum(getattr(new[pid], field) for pid in ranked[:top])
            depth = sum(getattr(new[pid], field) for pid in ranked[top:])
            if not depth or not lead:
                continue
            up = 1.0 + depth * (1.0 - scale) / lead
            for i, pid in enumerate(ranked):
                new[pid] = replace(new[pid], **{field: getattr(new[pid], field) * (up if i < top else scale)})


def _line(position: str, field: str, pick: int) -> float:
    fit = stored()["rookies"].get(position, {}).get(field)
    pick = pick if position == "QB" else max(pick, ROOKIE_PICK_FLOOR)
    return max(0.0, fit[0] + fit[1] * math.log(pick)) if fit else 0.0


def rookie_player(pid: int, rookie: Rookie, team: Team) -> Player:
    """A rookie as the public sees him in August (see the module docstring)."""
    norm, league = params()["positions"].get(rookie.position, {}), params()["league"]
    lift = rookie.hype ** HYPE_ROLE                     # the coaches share part of the hype: a bigger role
    starts = _line(rookie.position, "starts", rookie.pick) if rookie.position == "K" else 0.0
    if rookie.position == "QB":
        starts = min(1.0, _line("QB", "week1", rookie.pick))
    return Player(pid, rookie.position, team.team, targets=_line(rookie.position, "targets", rookie.pick) * lift,
                  carries=_line(rookie.position, "carries", rookie.pick) * lift, starts=starts,
                  catch=norm.get("catch", 0.0), ypc=norm.get("ypc", 0.0) * team.passer,
                  rec_td=norm.get("rec_td", 0.0) * team.passer, ypr=norm.get("ypr", 0.0),
                  rush_td=norm.get("rush_td", 0.0),
                  quality=stored()["rookies"]["qb_quality"] if rookie.position == "QB" else 1.0,
                  int_rate=league["int_rate"] if rookie.position == "QB" else 0.0, games=0.0)


def _qb_competition(players: dict[int, Player], rookie: Player, secure: Collection[int] = ()) -> None:
    """Name the rookie QB the starter when his team has no starter, or, unless the incumbent is in `secure` (the
    established starters), when his week-1 chance is very high, or high and the incumbent is a below-average
    passer (see the module docstring). Mutates `players` (local to next_year)."""
    others = [p for p in players.values() if p.team == rookie.team and p.position == "QB" and p.player_id != rookie.player_id]
    incumbent = max(others, key=lambda p: (p.starts, -p.player_id), default=None)
    if incumbent is None or incumbent.starts < STARTER_ROLE or incumbent.player_id not in secure and (
            rookie.starts >= ROOKIE_QB_FRANCHISE or (rookie.starts >= ROOKIE_QB_NAMED and incumbent.quality < AVERAGE_PASSER)):
        players[rookie.player_id] = replace(rookie, starts=1.0)
        if incumbent is not None:
            players[incumbent.player_id] = replace(incumbent, starts=min(incumbent.starts, VETERAN_BACKUP_STARTS))


def starts_by_team(data: SeasonData) -> dict[int, list[tuple[int, str | None]]]:
    """{QB: [(week, team)] of the weeks he started (sim_metrics.qb_starts), plus the weeks he missed with an injury
    tag while his team, one he started for, played}: the `season_starts` of next_year, so a starter's claim counts
    the games he missed hurt."""
    teams = {pid: dict(games) for pid, games in data.team_by_week.items()}     # the game's team, not the one before it
    out = {pid: [(week, teams.get(pid, {}).get(week) or data.team_at(pid, week)) for week in weeks]
           for pid, weeks in sim_metrics.qb_starts(data).items()}
    for pid, week in sorted(key for key in data.injuries if key[0] in out):
        team, claimed = data.team_at(pid, week), {t for _, t in out[pid]}
        if (pid, week) not in data.actual and team in claimed and data.schedule.opponent(team, week)                 and (week, team) not in out[pid]:
            out[pid].append((week, team))
    return {pid: sorted(weeks, key=lambda wt: wt[0]) for pid, weeks in out.items()}


def restore_starters(players: dict[int, Player], season_starts: Mapping[int, Sequence[tuple[int, str | None]]]) -> None:
    """Each team's August starter by whole-season starts (see the module docstring). Mutates `players`."""
    by_team: dict[str, list[Player]] = defaultdict(list)
    for player in players.values():
        if player.position == "QB":
            by_team[player.team].append(player)
    started: dict[str | None, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for pid, weeks in season_starts.items():
        for _, team in weeks:
            started[team][pid] += 1
    led = {team: max(counts, key=lambda pid: (counts[pid], -pid)) for team, counts in started.items()}
    for qbs in by_team.values():
        roster = {p.player_id for p in qbs}

        def claim(p: Player) -> int:
            mine = season_starts.get(p.player_id, ())
            own = [week for week, team in mine if team == p.team]
            # a team he led counts, except for a QB traded in mid-season to a team whose own opening-day starter
            # led it and is still there (Brissett behind Lamar at BAL); one traded to a team led by fill-ins keeps it
            leader = led.get(p.team, p.player_id)
            if own and leader != p.player_id and leader in roster and (1, p.team) in season_starts.get(leader, ()):
                return len(own) + (1 in own)
            weeks = own + [week for week, team in mine if team != p.team and led.get(team) == p.player_id]
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


def _with_passers(players: dict[int, Player], teams: dict[str, Team]) -> dict[str, Team]:
    """Each team's usual passer is now its August starter (his regressed quality); its receivers' rates (held
    at an average passer until now) are scaled to him. Mutates `players`; returns the teams."""
    starter: dict[str, Player] = {}
    for player in players.values():
        if player.position == "QB" and (player.team not in starter or (player.starts, -player.player_id) > (
                starter[player.team].starts, -starter[player.team].player_id)):
            starter[player.team] = player
    passer = {team: starter[team].quality if team in starter else AVERAGE_PASSER for team in teams}
    for pid, player in players.items():
        if player.position in RECEIVERS and player.team in passer:
            players[pid] = replace(player, **{name: getattr(player, name) * passer[player.team] for name in RECEIVER_RATES})
    return {name: replace(team, passer=passer[name]) for name, team in teams.items()}


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
              season_starts: Mapping[int, Sequence[tuple[int, str | None]]] | None = None,
              past: Collection[int] = (), record: Mapping[int, float] | None = None) -> tuple[World, dict[int, PoolPlayer]]:
    """(the belief going into next season, its player pool: names, positions and teams, no ADP yet) from the
    belief at the end of this one. `players`: names and positions of the world's players; `experience`: seasons each has played
    through this one; `rookies`: {id: rookie} (rookie_ids); `moves`: {id: new team, or None = gone};
    `season_starts`: (week, team) of each start a QB made this season (starts_by_team); `past`: the QBs established
    by their real ADP of the seasons before (sim_qb.established_qbs, read by sim_setup.qb_inputs); `record`: last
    season's points a game (sim_market.track_record), so August protects whom the season's ADP rule protects."""
    teams = _teams(world)
    new = _veterans(world, experience, moves)
    restore_starters(new, season_starts or {})
    _fill_vacancies(new, teams)
    secure = frozenset()
    if any(r.position == "QB" for r in rookies.values()):     # the veterans' preseason ranks, before the rookies join
        veterans = World(MappingProxyType(new), MappingProxyType(teams))
        secure = sim_qb.established_qbs(sim_qb.preseason_ranks(veterans, record), past)
    for pid, rookie in sorted(rookies.items()):
        if rookie.team in teams and pid not in new:
            new[pid] = rookie_player(pid, rookie, replace(teams[rookie.team], passer=AVERAGE_PASSER))
    for pid in sorted(rookies):
        if pid in new and new[pid].position == "QB":
            _qb_competition(new, new[pid], secure)
    _concentrate(new)
    teams = _with_passers(new, teams)
    belief = World(MappingProxyType(new), MappingProxyType(teams))
    pool = {}
    for pid, player in new.items():
        known = players.get(pid)
        name = known.name if known is not None else rookies[pid].name
        pool[pid] = PoolPlayer(pid, name, player.position, None, None, player.team)
    return belief, pool


def season_base(season: int, belief: World, pool: Mapping[int, PoolPlayer], schedule: Schedule, scoring: str,
                rules: ScoringRules | None, defense_prior: Mapping[tuple[str, str], float],
                adp_format: str = "ppr", record: Mapping[int, float] | None = None,
                premium: Mapping[int, float] | None = None) -> SeasonData:
    """The season before anything happened, ready for sim_season.simulate(base, seed, 1, prior=belief): the
    pool (each player's team is his PoolPlayer.adp_team, as nothing is played) with the market's ADP for the
    league's `adp_format` (replay_adp_format.wanted: priced on its own scoring, rules included; "2qb" for a
    superflex league; recorded as SeasonData.adp_format; leaning on `record`, the players' points a game in
    the season the belief came from, as a real August market leans on what it saw, and paying the rookies'
    `premium`, rookie_premium), the schedule, and a week-1
    projection for every player of the belief (the market's for those who dress, MIN_PROJECTION for backups
    and depth), so all of them exist from the start: SeasonData.as_of admits a player with an ADP, a
    projection or a game, and a backup QB or a WR6 must be there to step in. simulate replaces week 1's
    projections with its own. No games, tags or ranks."""
    empty = MappingProxyType({})
    base = SeasonData(season=season, scoring=scoring, players=MappingProxyType(dict(pool)), schedule=schedule,
                      actual=empty, projected=empty, team_by_week=empty,
                      last_week=max(game.week for game in schedule.games),
                      defense_prior=MappingProxyType(dict(defense_prior)), rules=rules, adp_format=adp_format)
    week_one = sim_market.projections(belief, schedule, 1, (), scoring_of(base))
    return replace(with_market_adp(base, belief, record=record, premium=premium),
                   projected=MappingProxyType({(pid, 1): week_one.get(pid, sim_market.MIN_PROJECTION)
                                               for pid in belief.players}))


def with_market_adp(data: SeasonData, belief: World, games_left: int = sim_market.SEASON_GAMES,
                    record: Mapping[int, float] | None = None,
                    premium: Mapping[int, float] | None = None) -> SeasonData:
    """`data` with every player's ADP the market's under `belief` with `games_left` games to play (none for a
    player it does not list), priced for data's scoring and its ADP list (data.adp_format "2qb": superflex),
    leaning on each player's `record` (sim_market.track_record) and paying the rookies' `premium`
    (rookie_premium) when given. A 2-QB list also records the 1-QB board of the same scoring as
    `scoring_adp` (replay_superflex values non-QBs on it)."""
    two_qb = data.adp_format == TWO_QB
    market = sim_market.adp(belief, scoring_of(data), two_qb, games_left, record, premium)
    players = {pid: replace(player, adp=market[pid][0] if pid in market else None,
                            adp_stdev=market[pid][1] if pid in market else None)
               for pid, player in data.players.items()}
    one_qb = sim_market.adp(belief, scoring_of(data), False, games_left, record, premium) if two_qb else {}
    return replace(data, players=MappingProxyType(players),
                   scoring_adp=MappingProxyType({pid: one_qb[pid][0] for pid in players if pid in one_qb}))
