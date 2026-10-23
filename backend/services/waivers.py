"""Waiver-wire assistant: who to add this week and what to bid.

Candidates are rostered in at most `max_rostered`% of ESPN leagues. Each is scored:

    score = rest-of-season points above replacement
          + 1.5 x points above replacement this week (projection - position replacement)
          + next-3-weeks schedule bonus (adjusted defense factors, centered on 1.0)
          + 3 per breakout flag (opportunity spike counts double)

The FAAB bid scales with the score relative to the best candidate this week, capped at
40% of budget (a league-winning add), with a 1% floor for speculative stashes.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from config import get_settings
from services.flags import Flag, player_flags
from services.valuation import Valuation
from services.weekly_rankings import WeeklyPlayer, WeeklyRankings

DEFAULT_MAX_ROSTERED = 50.0
WEEK_WEIGHT = 1.5
SCHEDULE_WEEKS = 3
SCHEDULE_SCALE = 20.0
FLAG_POINTS = 3.0
MAX_FAAB = 40
MIN_FAAB = 1


@dataclass(frozen=True)
class WaiverCandidate:
    player: WeeklyPlayer
    pct_rostered: float
    ros_value: float
    week_value: float
    schedule_factor: float
    flags: tuple[Flag, ...]
    score: float
    faab_pct: int
    reasons: tuple[str, ...]


def _schedule_factor(valuation: Valuation, team: str | None, position: str, week: int) -> float:
    end = min(get_settings().fantasy_end_week, week + SCHEDULE_WEEKS - 1)
    opponents = valuation.schedule.remaining_opponents(team, week - 1, end)
    return valuation.defense.schedule_factor(opponents, position)


def waiver_candidates(rankings: WeeklyRankings, valuation: Valuation, position: str | None = None,
                      max_rostered: float = DEFAULT_MAX_ROSTERED, limit: int = 25) -> list[WaiverCandidate]:
    seasons = {p.player_id: p for p in valuation.seasons}
    replacement = valuation.replacement
    rows = []
    for player in rankings.players:
        if player.pct_rostered is None or player.pct_rostered > max_rostered:
            continue
        if position and player.position != position:
            continue
        value = valuation.by_id.get(player.player_id)
        ros = value.trade_value if value else 0.0
        week_value = max(0.0, player.projection - replacement.get(player.position, 0.0))
        schedule = _schedule_factor(valuation, player.team, player.position, rankings.week)
        flags = tuple(f for f in (player_flags(seasons[player.player_id], rankings.context)
                                  if player.player_id in seasons and rankings.context else ()) if f.kind == "breakout")
        flag_points = sum(FLAG_POINTS * (2 if f.key == "opportunity" else 1) for f in flags)
        score = ros + WEEK_WEIGHT * week_value + SCHEDULE_SCALE * (schedule - 1) + flag_points
        if score <= 0:
            continue
        reasons = [f"Rostered in {player.pct_rostered:.0f}% of ESPN leagues."]
        if week_value > 0:
            reasons.append(f"Projects {player.projection:.1f} this week, {week_value:.1f} above a typical "
                           f"{player.position} replacement.")
        if ros > 0:
            reasons.append(f"Rest-of-season value: {ros:.0f} points above replacement.")
        if abs(schedule - 1) >= 0.03:
            reasons.append(f"Next {SCHEDULE_WEEKS} matchups are {abs(schedule - 1) * 100:.0f}% "
                           f"{'easier' if schedule > 1 else 'tougher'} than average.")
        reasons.extend(f.detail for f in flags)
        rows.append(WaiverCandidate(player, player.pct_rostered, round(ros, 1), round(week_value, 1),
                                    schedule, flags, round(score, 1), 0, tuple(reasons)))
    rows.sort(key=lambda c: -c.score)
    top = rows[0].score if rows else 1.0
    return [WaiverCandidate(**{**c.__dict__, "faab_pct": max(MIN_FAAB, round(MAX_FAAB * (c.score / top) ** 2))})
            for c in rows[:limit]]


def faab_label(pct: int) -> str:
    if pct >= 25:
        return "Priority add"
    if pct >= 10:
        return "Solid add"
    if pct >= 4:
        return "Depth add"
    return "Speculative stash"


__all__: Sequence[str] = ("WaiverCandidate", "faab_label", "waiver_candidates")
