"""Injury-aware availability and vacated opportunity.

Layers, newest first:
1. Sleeper live status (player_status, refreshed hourly): game-day Out/Doubtful/Questionable,
   IR, PUP, suspensions.
2. The official weekly injury report (injury_reports, nflverse): game status plus the final
   practice participation (DNP / LP / FP).

**Play probability.** Each status and practice combination maps to a learned chance of playing:
how often a player who recorded stats the week before recorded stats again with that tag
(2021 onward), relative to a healthy full-practice player. That gives roughly: Questionable+DNP
about 0.5, Questionable+LP about 0.77, Questionable+FP about 0.84, Doubtful about 0.03, Out 0.

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
from datetime import datetime, timedelta, timezone
from statistics import mean
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import DataLoad, DepthChartEntry, InjuryReport, PlayerStatsWeekly, PlayerStatus
from integrations.weekly_sources import utc
from services.projections import PlayerSeason
from services.schedule_strength import Schedule

SLEEPER_FRESH_HOURS = 18
SLEEPER_ZERO = {"OUT", "IR", "PUP", "SUS", "DNR", "COV", "NA", "IR-R", "NFI"}
MIN_RATE_SAMPLE = 15
RELEVANT_PPG = 4.0             # players below this vacate too little to model
UNCERTAIN = 0.95               # below this play probability, the player vacates work
NEXT_MAN_SHARE = 0.6           # of the same-position share, for the next player on the depth chart
WITHOUT_PRIOR_GAMES = 2        # shrink measured with/without deltas toward the heuristic
MIN_WITHOUT_GAMES = 2          # one game without a teammate is too noisy to measure
MAX_BOOST_SHARE = 0.9          # nobody inherits more than this share of what the player vacates
REST_DAY = "not injury related"
LAST_REGULAR_WEEK = 18
# How a position's vacated work is redistributed (the rest is lost: efficiency, other positions).
REDISTRIBUTION = {
    "RB": {"RB": 0.75, "WR": 0.07, "TE": 0.05},
    "WR": {"WR": 0.6, "TE": 0.18, "RB": 0.07},
    "TE": {"TE": 0.5, "WR": 0.3, "RB": 0.05},
    "QB": {},                   # backups' own rates are unknown; QB swaps aren't redistributed
}
# Learned rates are normalized to this "healthy" key; these defaults are used without data.
HEALTHY = ("NONE", "FP")
DEFAULT_RATES = {("OUT", "*"): 0.0, ("DOUBTFUL", "*"): 0.03, ("QUESTIONABLE", "DNP"): 0.49,
                 ("QUESTIONABLE", "LP"): 0.77, ("QUESTIONABLE", "FP"): 0.84, ("QUESTIONABLE", "*"): 0.75,
                 ("NONE", "DNP"): 0.8, ("NONE", "LP"): 0.98, ("NONE", "FP"): 1.0}


@dataclass(frozen=True)
class Availability:
    player_id: int
    play_prob: float
    status: str | None          # "Questionable", "Out", "IR", ...
    practice: str | None        # final practice that week: DNP / LP / FP
    injury: str | None
    source: str                 # "sleeper", "report", "none"

    @property
    def label(self) -> str:
        if self.status is None and self.practice in (None, "FP"):
            return "Healthy"
        practice = f", {self.practice} in practice" if self.practice else ""
        injury = f" ({self.injury})" if self.injury else ""
        return f"{self.status or 'No designation'}{injury}{practice}"


HEALTHY_AVAILABILITY = Availability(0, 1.0, None, None, None, "none")


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


# ---------- play probability ----------

_rate_cache: dict[tuple, dict[tuple[str, str], float]] = {}
_rate_lock = Lock()


def _key(status: str | None, practice: str | None) -> tuple[str, str]:
    return ((status or "NONE").upper(), (practice or "*").upper())


def learned_play_rates(session: Session, exclude_season: int | None = None) -> dict[tuple[str, str], float]:
    """{(status, practice): chance of playing}, learned from regular-season reports.

    exclude_season leaves one season out (the backtest must not learn from the games it tests).
    """
    version = (str(session.get_bind().url), session.scalar(select(func.max(DataLoad.id))), exclude_season)
    with _rate_lock:
        if version in _rate_cache:
            return _rate_cache[version]
    # A set lookup in Python: the equivalent SQL self-join takes over a minute on SQLite.
    played = set(session.execute(select(PlayerStatsWeekly.player_id, PlayerStatsWeekly.season, PlayerStatsWeekly.week)
                                 .where(PlayerStatsWeekly.season_type == "REG")).tuples())
    reports = session.execute(select(InjuryReport.player_id, InjuryReport.season, InjuryReport.week,
                                     InjuryReport.report_status, InjuryReport.practice_status)
                              .where(InjuryReport.week <= LAST_REGULAR_WEEK,
                                     InjuryReport.season != (exclude_season or -1))).all()
    tallies: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for player_id, season, week, status, practice in reports:
        if (player_id, season, week - 1) not in played:
            continue                                   # only players who contributed the week before
        tally = tallies[_key(status, practice)]
        tally[0] += 1
        tally[1] += (player_id, season, week) in played
    observed = {key: (n, hits / n) for key, (n, hits) in tallies.items() if n >= MIN_RATE_SAMPLE}
    healthy = observed.get(HEALTHY, (0, None))[1]
    rates = dict(DEFAULT_RATES)
    if healthy:
        for key, (_, rate) in observed.items():
            rates[key] = round(min(1.0, rate / healthy), 3)
        rates[("OUT", "*")] = 0.0
    with _rate_lock:
        _rate_cache[version] = rates
    return rates


def play_probability(rates: Mapping[tuple[str, str], float], status: str | None, practice: str | None) -> float:
    status_key = (status or "NONE").upper()
    if status_key in SLEEPER_ZERO:
        return 0.0
    for key in ((status_key, (practice or "*").upper()), (status_key, "*")):
        if key in rates:
            return rates[key]
    return 1.0 if status_key == "NONE" else rates.get(("QUESTIONABLE", "*"), 0.75)


def load_availability(session: Session, season: int, week: int, use_live: bool = True,
                      exclude_rate_season: int | None = None) -> dict[int, Availability]:
    """Every listed player's availability for the week (unlisted players are healthy).

    use_live adds Sleeper's current tags; only valid for the current week.
    """
    rates = learned_play_rates(session, exclude_rate_season)
    report = {r.player_id: r for r in session.scalars(select(InjuryReport).where(
        InjuryReport.season == season, InjuryReport.week == week))}
    live = {}
    if use_live:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=SLEEPER_FRESH_HOURS)
        live = {s.player_id: s for s in session.scalars(select(PlayerStatus)) if utc(s.fetched_at) >= cutoff}
    availability = {}
    for player_id in set(report) | {pid for pid, s in live.items() if s.injury_status}:
        r, s = report.get(player_id), live.get(player_id)
        practice = r.practice_status if r else None
        if s is not None and s.injury_status:
            status, source, injury = s.injury_status, "sleeper", s.injury_body_part or (r.injury if r else None)
        else:
            status, source, injury = (r.report_status if r else None), "report", (r.injury if r else None)
        if status is None and injury and REST_DAY in injury.lower():
            practice = None                          # veteran rest days aren't injuries
        availability[player_id] = Availability(player_id, play_probability(rates, status, practice),
                                               status, practice, injury, source)
    return availability


# ---------- vacated opportunity ----------

def _depth(session: Session) -> dict[int, tuple[str, int]]:
    return {d.player_id: (d.position, d.depth) for d in session.scalars(select(DepthChartEntry))}


def _game_value(game) -> float:
    return game.expected_points if game.expected_points is not None else game.fantasy_points


def team_games(player: PlayerSeason, team: str, schedule: Schedule) -> dict[int, object]:
    """{week: game} for the player's games with this team (games for a former team are dropped)."""
    return {g.week: g for g in player.games if schedule.opponent(g.opponent, g.week) == team}


def team_weeks(schedule: Schedule, team: str, before_week: int) -> list[int]:
    return [g.week for g in schedule.games
            if g.is_final and g.week < before_week and team in (g.home_team, g.away_team)]


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
    seasons = list(seasons)
    by_team: dict[str, list[PlayerSeason]] = defaultdict(list)
    for p in seasons:
        if p.team:
            by_team[p.team].append(p)
    out: dict[int, list[Returning]] = defaultdict(list)
    for team, roster in by_team.items():
        recent = team_weeks(schedule, team, current_week)[-2:]
        for star in roster:
            played = set(team_games(star, team, schedule))
            if len(recent) < 2 or any(w in played for w in recent) or star.ppg < 8 or not played:
                continue                                     # needs a good player who missed the last two games
            status = availability.get(star.player_id)
            if status is None or status.play_prob < 0.5:
                continue                                     # no report (released?) or still out
            for mate in roster:
                if mate.player_id == star.player_id or mate.position != star.position:
                    continue
                if star.position == "QB" and len(played) < len(team_games(mate, team, schedule)):
                    continue                                 # a backup QB returning doesn't threaten the starter
                measured = with_without(mate, star, team, schedule, current_week)
                gain = max(measured[0], 0.0) if measured and measured[1] >= MIN_WITHOUT_GAMES else 0.0
                out[mate.player_id].append(Returning(star.name, round(gain * status.play_prob, 2),
                                                     measured[1] if measured else 0))
    return {pid: tuple(items) for pid, items in out.items()}


def build_context(session: Session, seasons: Iterable[PlayerSeason], rates: Mapping[int, float],
                  season: int, week: int, schedule: Schedule, use_live: bool = True,
                  exclude_rate_season: int | None = None) -> InjuryContext:
    """use_live adds Sleeper tags and today's depth chart: only for the current week."""
    seasons = list(seasons)
    availability = load_availability(session, season, week, use_live, exclude_rate_season)
    depth = _depth(session) if use_live else {}
    return InjuryContext(availability=availability,
                         boosts=vacated_boosts(seasons, rates, availability, depth, schedule, week),
                         returning=returning_competition(seasons, availability, week, schedule))
