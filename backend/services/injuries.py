"""Vacated opportunity and the injury context the weekly rankings use.

Chance to play (injury reports, live tags, untagged sit risk, backup QBs) is in
services/availability.py; its names are re-exported here for existing callers.

**Vacated opportunity.** When a player is likely to sit, the expected points he leaves behind
are split among his teammates:
- Each teammate gets a positional share (e.g. 75% of a running back's work stays with RBs).
- Inside the position, the next player on the depth chart takes the largest cut; the rest is
  split by current usage.
- When this season already has games without that player, the teammate's measured change in
  expected points in those games replaces the heuristic, shrunk toward it by sample size.
"""
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import DepthChartEntry
from services.projections import PlayerSeason
from services.schedule_strength import Schedule
from services.availability import (  # noqa: F401  (re-exported)
    BACKUP_FLOOR, DEFAULT_RATES, HEALTHY, HEALTHY_AVAILABILITY, INACTIVE_LAST_WEEK_PLAYS, SIT_RISK, SLEEPER_ZERO, Availability,
    _rate_cache, backup_qb_availability, learned_play_rates, load_availability, play_probability, qb_depth,
    team_weeks, usage_availability,
)

RELEVANT_PPG = 4.0             # players below this vacate too little to model
UNCERTAIN = 0.95               # below this play probability, the player vacates work
NEXT_MAN_SHARE = 0.6           # of the same-position share, for the next player on the depth chart
WITHOUT_PRIOR_GAMES = 2        # shrink measured with/without deltas toward the heuristic
MIN_WITHOUT_GAMES = 2          # one game without a teammate is too noisy to measure
MAX_BOOST_SHARE = 0.9          # nobody inherits more than this share of what the player vacates
REDISTRIBUTION = {
    "RB": {"RB": 0.75, "WR": 0.07, "TE": 0.05},
    "WR": {"WR": 0.6, "TE": 0.18, "RB": 0.07},
    "TE": {"TE": 0.5, "WR": 0.3, "RB": 0.05},
    "QB": {},                   # backups' own rates are unknown; QB swaps aren't redistributed
}
# Learned rates are normalized to this "healthy" key; these defaults are used without data.
@dataclass(frozen=True)
class Boost:
    """Extra expected points for one player because a teammate may sit."""

    player_id: int
    from_player_id: int
    from_name: str
    from_status: str
    full_points: float           # if the teammate sits for sure
    expected_points: float       # weighted by the teammate's chance of sitting
    method: str                  # "with/without (N games)" or "depth chart + usage share"


@dataclass(frozen=True)
class InjuryContext:
    availability: Mapping[int, Availability]
    boosts: Mapping[int, tuple[Boost, ...]] = field(default_factory=dict)
    # Teammates returning from injury: player_id -> who is back and the work he takes back.
    returning: Mapping[int, tuple["Returning", ...]] = field(default_factory=dict)

    def of(self, player_id: int) -> Availability:
        return self.availability.get(player_id, HEALTHY_AVAILABILITY)


# ---------- vacated opportunity ----------

def _depth(session: Session) -> dict[int, tuple[str, int]]:
    return {d.player_id: (d.position, d.depth) for d in session.scalars(select(DepthChartEntry))}


def _game_value(game) -> float:
    return game.expected_points if game.expected_points is not None else game.fantasy_points


def team_games(player: PlayerSeason, team: str, schedule: Schedule) -> dict[int, object]:
    """{week: game} for the player's games with this team (games for a former team are dropped)."""
    return {g.week: g for g in player.games if schedule.opponent(g.opponent, g.week) == team}


def with_without(beneficiary: PlayerSeason, absent: PlayerSeason, team: str, schedule: Schedule,
                 before_week: int) -> tuple[float, int] | None:
    """Beneficiary's expected points per game without vs with the teammate, on this team only."""
    weeks = team_weeks(schedule, team, before_week)
    absent_weeks = set(team_games(absent, team, schedule))
    mine = team_games(beneficiary, team, schedule)
    with_games = [_game_value(mine[w]) for w in weeks if w in mine and w in absent_weeks]
    without_games = [_game_value(mine[w]) for w in weeks if w in mine and w not in absent_weeks]
    if not without_games or not with_games:
        return None
    return mean(without_games) - mean(with_games), len(without_games)


def _next_man_up(absent: PlayerSeason, group: list[tuple[PlayerSeason, float]],
                 depth: Mapping[int, tuple[str, int]]) -> int | None:
    """The healthy teammate directly behind the absent player on the depth chart (ties: higher usage)."""
    if absent.player_id not in depth:
        return None
    absent_depth = depth[absent.player_id][1]
    behind = [(depth[p.player_id][1], -rate, p.player_id) for p, rate in group
              if p.player_id in depth and depth[p.player_id][1] > absent_depth]
    return min(behind)[2] if behind else None


def _heuristic_shares(absent: PlayerSeason, teammates: list[tuple[PlayerSeason, float]],
                      depth: Mapping[int, tuple[str, int]]) -> dict[int, float]:
    """Share of the absent player's vacated points each teammate receives."""
    plan = REDISTRIBUTION.get(absent.position or "", {})
    shares: dict[int, float] = {}
    for position, position_share in plan.items():
        group = [(p, rate) for p, rate in teammates if p.position == position]
        if not group:
            continue
        total_rate = sum(rate for _, rate in group) or 1.0
        next_up = _next_man_up(absent, group, depth) if position == absent.position else None
        for player, rate in group:
            usage = rate / total_rate
            if next_up is None:
                shares[player.player_id] = position_share * usage
            elif player.player_id == next_up:
                shares[player.player_id] = position_share * (NEXT_MAN_SHARE + (1 - NEXT_MAN_SHARE) * usage)
            else:
                shares[player.player_id] = position_share * (1 - NEXT_MAN_SHARE) * usage
    return shares


def vacated_boosts(seasons: Iterable[PlayerSeason], rates: Mapping[int, float],
                   availability: Mapping[int, Availability], depth: Mapping[int, tuple[str, int]],
                   schedule: Schedule, week: int) -> dict[int, tuple[Boost, ...]]:
    """{player_id: boosts} for teammates of players likely to miss the game.

    `rates` are each player's projected points per game before any injury adjustment.
    """
    seasons = list(seasons)
    by_team: dict[str, list[PlayerSeason]] = defaultdict(list)
    for player in seasons:
        if player.team:
            by_team[player.team].append(player)
    boosts: dict[int, list[Boost]] = defaultdict(list)
    for team, roster in by_team.items():
        for absent in roster:
            avail = availability.get(absent.player_id)
            vacated_rate = rates.get(absent.player_id, 0.0)
            if avail is None or avail.play_prob >= UNCERTAIN or vacated_rate < RELEVANT_PPG:
                continue
            teammates = [(p, rates.get(p.player_id, 0.0)) for p in roster
                         if p.player_id != absent.player_id and availability.get(p.player_id, HEALTHY_AVAILABILITY).play_prob > 0.5]
            shares = _heuristic_shares(absent, teammates, depth)
            sit_chance = 1 - avail.play_prob
            for player, _ in teammates:
                heuristic = vacated_rate * shares.get(player.player_id, 0.0)
                measured = with_without(player, absent, team, schedule, week)
                if measured is not None and player.position == absent.position and measured[1] >= MIN_WITHOUT_GAMES:
                    delta, games = measured
                    weight = games / (games + WITHOUT_PRIOR_GAMES)
                    full = min(weight * max(delta, 0.0) + (1 - weight) * heuristic, MAX_BOOST_SHARE * vacated_rate)
                    method = f"with/without ({games} game{'s' if games != 1 else ''} without him this season)"
                else:
                    full, method = heuristic, "depth chart + usage share"
                if full < 0.3:
                    continue
                boosts[player.player_id].append(Boost(
                    player.player_id, absent.player_id, absent.name, avail.label,
                    round(full, 2), round(full * sit_chance, 2), method))
    return {pid: tuple(sorted(b, key=lambda x: -x.full_points)) for pid, b in boosts.items()}


@dataclass(frozen=True)
class Returning:
    """A teammate coming back, and the work he is expected to take back from this player."""

    name: str
    expected_loss: float        # expected points per game this player gives back (0 if unmeasured)
    games_without: int


def returning_competition(seasons: Iterable[PlayerSeason], availability: Mapping[int, Availability],
                          current_week: int, schedule: Schedule) -> dict[int, tuple[Returning, ...]]:
    """Players whose teammate is back after missing recent games (their bump may fade).

    Returns {beneficiary_id: (Returning, ...)} for same-position teammates.
    - Only players with an injury report or live status this week count as "back" (released
      players without a status are skipped).
    - Quarterbacks: a returning QB only matters if he was the starter. A backup returning
      (e.g. Drew Lock behind Sam Darnold) never flags the incumbent, who keeps the job unless
      he plays his way out of it.
    - The expected loss is the fill-in's measured gain without the returning player (same
      team, 2+ games), weighted by the returner's chance to play.
    """
    out: dict[int, list[Returning]] = defaultdict(list)
    for mate, star, gain, games in fill_in_gains(seasons, current_week, schedule):
        status = availability.get(star.player_id)
        if status is None or status.play_prob < 0.5:
            continue                                         # no report (released?) or still out
        out[mate.player_id].append(Returning(star.name, round(gain * status.play_prob, 2), games))
    return {pid: tuple(items) for pid, items in out.items()}


def fill_in_gains(seasons: Iterable[PlayerSeason], current_week: int,
                  schedule: Schedule) -> list[tuple[PlayerSeason, PlayerSeason, float, int]]:
    """(fill-in, absent starter, measured gain per game, games without) for starters out lately.

    A starter is a same-position teammate scoring 8+ per game who missed the team's last two
    games. The gain is the fill-in's expected points without him minus with him (0 when it
    can't be measured from 2+ games). A backup QB never counts as the starter.
    """
    by_team: dict[str, list[PlayerSeason]] = defaultdict(list)
    for p in seasons:
        if p.team:
            by_team[p.team].append(p)
    pairs = []
    for team, roster in by_team.items():
        recent = team_weeks(schedule, team, current_week)[-2:]
        for star in roster:
            played = set(team_games(star, team, schedule))
            if len(recent) < 2 or any(w in played for w in recent) or star.ppg < 8 or not played:
                continue                                     # needs a good player who missed the last two games
            for mate in roster:
                if mate.player_id == star.player_id or mate.position != star.position:
                    continue
                if star.position == "QB" and len(played) < len(team_games(mate, team, schedule)):
                    continue                                 # a backup QB returning doesn't threaten the starter
                measured = with_without(mate, star, team, schedule, current_week)
                gain = max(measured[0], 0.0) if measured and measured[1] >= MIN_WITHOUT_GAMES else 0.0
                pairs.append((mate, star, gain, measured[1] if measured else 0))
    return pairs


def ros_role_losses(seasons: Iterable[PlayerSeason], current_week: int, schedule: Schedule,
                    starter_share: Mapping[int, float]) -> dict[int, tuple[str, float]]:
    """{fill-in id: (starter name, ppg to take off his rest-of-season rate)}.

    A fill-in's recent games were played without the starter, so his recency-weighted rate
    inflates his season rate once the starter is back. The loss is his measured gain, times
    the share of his games played without the starter (that's how much of his rate it
    inflated), times the share of remaining games the starter is expected to play
    (starter_share: players with a live injury status; 0 when out for the season, so the
    fill-in keeps the job).
    """
    out: dict[int, tuple[str, float]] = {}
    for mate, star, gain, games_without in fill_in_gains(seasons, current_week, schedule):
        share = starter_share.get(star.player_id)
        if share is None or gain <= 0 or share <= 0:
            continue                                         # no status (released?), no gain, or done for the year
        loss = round(gain * games_without / max(mate.games_played, 1) * share, 2)
        if loss > out.get(mate.player_id, ("", 0.0))[1]:
            out[mate.player_id] = (star.name, loss)
    return out


def build_context(session: Session, seasons: Iterable[PlayerSeason], rates: Mapping[int, float],
                  season: int, week: int, schedule: Schedule, use_live: bool = True,
                  exclude_rate_season: int | None = None) -> InjuryContext:
    """use_live adds Sleeper tags and today's depth chart: only for the current week."""
    seasons = list(seasons)
    availability = load_availability(session, season, week, use_live, exclude_rate_season)
    availability = {**usage_availability(seasons, availability, week, schedule), **availability}
    if use_live:
        availability = {**availability, **backup_qb_availability(qb_depth(session), availability)}
    depth = _depth(session) if use_live else {}
    return InjuryContext(availability=availability,
                         boosts=vacated_boosts(seasons, rates, availability, depth, schedule, week),
                         returning=returning_competition(seasons, availability, week, schedule))
