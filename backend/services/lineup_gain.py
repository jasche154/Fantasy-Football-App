"""Lineup gain (G): how many points a roster move adds to YOUR best lineup over the rest of the season.

    G = sum over weeks of weight(week) x (best-lineup points after the move - before)

- Weeks run from this week through the league's final. Regular-season weeks count
  REGULAR_DECAY^(weeks ahead); playoff weeks count your playoff odds (at least PLAYOFF_MIN).
- Each player's week: his healthy rest-of-season rate x that week's opponent factor, 0 on a
  bye. This week uses the actual weekly projection. Later weeks:
    * Questionable/Doubtful/low snaps now: healthy from next week, x (1 - weekly injury hazard);
    * Out or on IR now: out for his expected games missed (services/injury_recovery.py: body part,
      practice reports, a little shorter each week he's been out), playing the fractional week in
      part; his first games back keep the measured post-injury dip. Out for the season: 0.
      Without that estimate: Out plays week k with probability 1 - OUT_PERSIST^k, IR waits IR_WEEKS;
    * healthy: x his long-run share of weeks played, from the weekly injury hazard (QB 8.8%,
      WR 5.4%, RB/TE 4.3%, 2021-25 starters) and the 60% chance an injured player is still out
      the next week: 1 - hazard / (1 - 0.6 + hazard), e.g. a receiver misses about 12% of weeks.
      The season simulation (services/league_insights.py) runs the same chain week by week.
      A backup (a QB2, whose share is the games his starter misses) keeps that smaller share.
- Best lineup: QB/RB/WR/TE slots and FLEX (kickers and defenses aren't projected that far out).

Used for waiver pickups (with the named drop), trade offers on real rosters, and the playoff planner.
"""
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from services import my_team as mt
from services.valuation import MIN_SHARE, ROS_SCHEDULE_WEIGHT

HAZARD = {"QB": 0.088, "WR": 0.054, "RB": 0.043, "TE": 0.043}
OUT_PERSIST = 0.60
IR_WEEKS = 4
IR_STATUSES = {"IR", "PUP", "SUS", "NFI", "IR-R", "PUP-R"}
OUT_STATUSES = {"OUT"}
REGULAR_DECAY = 0.95
PLAYOFF_MIN = 0.1
DEFAULT_PLAYOFF_ODDS = 0.6
HANDCUFF_MULTIPLE = 2.0           # drop your own starter's handcuff only for an add worth 2x as much


def _after_injury(weeks_ahead: int, missed: float) -> float:
    """His share of week `weeks_ahead` (0 = this week) when he's expected to miss `missed` games from now."""
    from services.injury_recovery import params

    played = min(1.0, max(0.0, weeks_ahead + 1 - missed))
    back = weeks_ahead - math.ceil(missed - 1e-9)      # full games back, 0-based (negative: not back yet)
    rust = params()["rust"]
    return played * (rust[back] if 0 <= back < len(rust) else 1.0)


@dataclass(frozen=True)
class Horizon:
    current_week: int
    weights: Mapping[int, float]      # week -> weight

    @property
    def weeks(self) -> list[int]:
        return sorted(self.weights)


def healthy_share(position: str | None) -> float:
    """Long-run share of weeks a healthy player plays (the steady state of the injury chain)."""
    hazard = HAZARD.get(position or "", 0.05)
    return 1 - hazard / (1 - OUT_PERSIST + hazard)


def horizon(current_week: int, end_week: int, playoff_weeks: Iterable[int], playoff_odds: float | None) -> Horizon:
    playoffs = set(playoff_weeks)
    odds = max(PLAYOFF_MIN, min(1.0, playoff_odds if playoff_odds is not None else DEFAULT_PLAYOFF_ODDS))
    return Horizon(current_week, {w: odds if w in playoffs else REGULAR_DECAY ** (w - current_week)
                                  for w in range(current_week, end_week + 1)})


class Projector:
    """Each player's projected points in each week (see the module docstring)."""

    def __init__(self, valuation, availability: Mapping[int, object], weekly: Mapping[int, object], current_week: int):
        self.valuation, self.availability, self.weekly, self.current_week = valuation, availability, weekly, current_week
        self._cache: dict[tuple[int, int], float] = {}

    @staticmethod
    def expected_share(value) -> float:
        share = getattr(value, "availability", None)
        return 1.0 if share is None else share

    def healthy_rate(self, value) -> float:
        share = self.expected_share(value)
        return value.ros_ppg / share if share > MIN_SHARE else value.ros_ppg

    def available(self, value, weeks_ahead: int) -> float:
        status = (getattr(self.availability.get(value.player_id), "status", None) or "").upper()
        share = healthy_share(value.position)
        missed = getattr(value, "expected_missed", 0.0) or 0.0
        if missed > 0:
            return share * _after_injury(weeks_ahead, missed)
        if status in IR_STATUSES:
            return 0.0 if weeks_ahead < IR_WEEKS else share
        if status in OUT_STATUSES:
            return (1 - OUT_PERSIST ** weeks_ahead) * share
        # At a tiny share the rate wasn't divided back out (healthy_rate), so don't discount it twice.
        expected = self.expected_share(value)
        if expected <= MIN_SHARE:
            return share
        if status:
            # Questionable/Doubtful: healthy from next week (this week is projected), but a backup QB
            # still plays only the games his starter misses.
            return min(share, getattr(value, "role_share", 1.0))
        # A healthy player expected to miss more than his injury chain says is a backup (a QB2 plays
        # only the games his starter misses): keep that share instead of treating him as a starter.
        return min(share, expected)

    def points(self, value, week: int) -> float:
        key = (value.player_id, week)
        if key not in self._cache:
            self._cache[key] = self._points(value, week)
        return self._cache[key]

    def _points(self, value, week: int) -> float:
        if week == self.current_week and value.player_id in self.weekly:
            return self.weekly[value.player_id].projection
        schedule, defense = self.valuation.schedule, self.valuation.defense
        opponent = schedule.opponent(value.team, week) if value.team else None
        if opponent is None:
            return 0.0                                  # bye
        factor = defense.factor(opponent, value.position) ** ROS_SCHEDULE_WEIGHT
        ahead = week - self.current_week
        return self.healthy_rate(value) * factor * (self.available(value, ahead) if ahead > 0 else 1.0)


def week_lineup(values: Sequence, week: int, projector: Projector, settings) -> tuple[float, set[int]]:
    """(points, starter ids) of the best QB/RB/WR/TE lineup that week."""
    pool = [SimpleNamespace(player_id=v.player_id, position=v.position, projection=projector.points(v, week))
            for v in values if v.position in mt.MODELED]
    best = mt.best_lineup(pool, settings)
    by_id = {p.player_id: p.projection for p in pool}
    starters = {pid for ids in best.values() for pid in ids}
    return sum(by_id[pid] for pid in starters), starters


def season_points(values: Sequence, horizon_: Horizon, projector: Projector, settings) -> float:
    return sum(weight * week_lineup(values, week, projector, settings)[0] for week, weight in horizon_.weights.items())


def gain(roster: Sequence, horizon_: Horizon, projector: Projector, settings,
         add: Sequence = (), drop: Sequence = ()) -> float:
    dropped = {v.player_id for v in drop}
    after = [v for v in roster if v.player_id not in dropped] + list(add)
    return round(season_points(after, horizon_, projector, settings)
                 - season_points(roster, horizon_, projector, settings), 2)


def handcuffs(roster: Sequence, horizon_: Horizon, projector: Projector, settings) -> set[int]:
    """Bench RBs on the same NFL team as one of your starting RBs this week."""
    _, starters = week_lineup(roster, horizon_.current_week, projector, settings)
    starting_teams = {v.team for v in roster if v.player_id in starters and v.position == "RB"}
    return {v.player_id for v in roster if v.position == "RB" and v.player_id not in starters and v.team in starting_teams}


def best_add(roster: Sequence, droppable: Sequence, candidate, horizon_: Horizon, projector: Projector,
             settings, protected: set[int] = frozenset()) -> tuple[float, object | None]:
    """(G, player to drop) for adding a candidate: the drop that leaves the best season.

    With an empty roster spot (droppable empty) nobody is dropped. A protected player (your
    starter's handcuff) is dropped only when the add is worth HANDCUFF_MULTIPLE x what he is.
    """
    base = season_points(roster, horizon_, projector, settings)
    with_add = list(roster) + [candidate]
    if not droppable:
        return round(season_points(with_add, horizon_, projector, settings) - base, 2), None
    best: tuple[float, object | None] | None = None
    for player in droppable:
        after = [v for v in with_add if v.player_id != player.player_id]
        value = season_points(after, horizon_, projector, settings) - base
        if player.player_id in protected:
            loss = base - season_points([v for v in roster if v.player_id != player.player_id], horizon_,
                                        projector, settings)
            if value <= HANDCUFF_MULTIPLE * loss:
                continue
        # Equal seasons (he wouldn't start either way): drop the one with less rest-of-season value.
        if best is None or (round(value, 2), -player.ros_ppg) > (round(best[0], 2), -best[1].ros_ppg):
            best = (value, player)
    return (round(best[0], 2), best[1]) if best else (0.0, None)


def starter_weeks(roster: Sequence, player_id: int, weeks: Sequence[int], projector: Projector, settings) -> int:
    """How many of these weeks the player is in the best lineup."""
    return sum(player_id in week_lineup(roster, w, projector, settings)[1] for w in weeks)


def playoff_odds_for(league_id: int) -> float | None:
    """The user's playoff odds from the last power-rankings run (cached; no Yahoo call)."""
    from services import league_insights

    with league_insights._lock:
        cached = league_insights._cache.get(league_id)
    if not cached:
        return None
    return next((t.get("playoff_odds") for t in cached[1]["teams"] if t.get("is_users_team")), None)


def context_for(team) -> tuple[Horizon, Projector]:
    """The horizon and projector for a built MyTeam (services/my_team.build_my_team)."""
    from config import get_settings
    from services.playoffs import playoff_weeks

    rules = mt.league_rules(team.league)
    weeks = playoff_weeks(rules)
    end = max(max(weeks), team.rankings.week)
    end = min(end, get_settings().fantasy_end_week) if not rules.get("playoff_weeks") else end
    availability = team.rankings.context.availability if team.rankings.context else {}
    projector = Projector(team.valuation, availability, team.weekly(), team.rankings.week)
    return horizon(team.rankings.week, end, weeks, playoff_odds_for(team.league.id)), projector


def roster_values(team, spots: Iterable | None = None) -> list:
    """The team's active (not IR-slot) QB/RB/WR/TE player values."""
    spots = team.spots if spots is None else spots
    return [v for s in spots if s.slot not in ("IR", "IR+", "NA")
            and (v := team.valuation.by_id.get(s.player_id)) is not None and v.position in mt.MODELED]
