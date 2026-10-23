"""Breakout and regression flags, aware of injury news.

| Flag | Kind | Trigger |
|---|---|---|
| Opportunity spike | breakout | A teammate may sit (Questionable or worse) and this player inherits 3+ pts of his work |
| Role growth | breakout | Expected points or snap share rising over the last 3 games |
| Touchdown luck | regression | TDs 2+ above expected (sell) or below (buy) |
| Efficiency outlier | regression | Scoring 3+ pts/game above or below what usage predicts |
| Competition returning | regression | A teammate who missed the last two games is back; his fill-in's role may shrink |
| Injury risk | risk | The player himself has a designation (chance to play under 90%) |
"""
from dataclasses import dataclass

from services.injuries import InjuryContext
from services.insights import compute_signals
from services.projections import PlayerSeason

OPPORTUNITY_POINTS = 3.0
ROLE_USAGE = 1.5           # expected pts/game, last 3 vs season
ROLE_SNAPS = 8.0           # snap-share points
TD_LUCK = 2.0
EFFICIENCY = 3.0
INJURY_RISK = 0.9


@dataclass(frozen=True)
class Flag:
    key: str
    kind: str                  # "breakout", "regression", "risk"
    direction: str             # "up" or "down" for the player's outlook
    label: str
    detail: str


def player_flags(player: PlayerSeason, context: InjuryContext) -> tuple[Flag, ...]:
    flags: list[Flag] = []
    for boost in context.boosts.get(player.player_id, ()):
        if boost.full_points >= OPPORTUNITY_POINTS:
            flags.append(Flag("opportunity", "breakout", "up", f"+{boost.full_points:.1f} if {boost.from_name} sits",
                              f"{boost.from_name} is {boost.from_status}. If he sits, {player.name} inherits about "
                              f"{boost.full_points:.1f} expected pts ({boost.method})."))
    for name in context.returning.get(player.player_id, ()):
        flags.append(Flag("competition_returning", "regression", "down", f"{name} returning",
                          f"{name} missed the last two games and is expected back; {player.name}'s expanded role may shrink."))
    signals = compute_signals(player)
    if signals is not None:
        usage, snaps = signals.usage_trend or 0.0, signals.snap_trend or 0.0
        if usage >= ROLE_USAGE or snaps >= ROLE_SNAPS:
            parts = []
            if usage >= ROLE_USAGE:
                parts.append(f"expected points up {usage:.1f}/game")
            if snaps >= ROLE_SNAPS:
                parts.append(f"snap share up {snaps:.0f} points")
            flags.append(Flag("role_growth", "breakout", "up", "Role growing",
                              f"Over the last 3 games: {' and '.join(parts)} vs his season average."))
        if signals.td_diff is not None and abs(signals.td_diff) >= TD_LUCK:
            up = signals.td_diff < 0
            flags.append(Flag("td_luck", "regression", "up" if up else "down",
                              "TDs due" if up else "TD-heavy",
                              f"{signals.actual_tds} TDs vs {signals.expected_tds:.1f} expected from his looks; "
                              f"touchdown rates tend to even out, so expect {'more' if up else 'fewer'}."))
        if abs(signals.fpoe_per_game) >= EFFICIENCY:
            up = signals.fpoe_per_game < 0
            flags.append(Flag("efficiency", "regression", "up" if up else "down",
                              "Under-producing usage" if up else "Over-producing usage",
                              f"Scoring {abs(signals.fpoe_per_game):.1f} pts/game {'below' if up else 'above'} what his usage "
                              "predicts; that gap usually closes."))
    availability = context.of(player.player_id)
    if availability.play_prob < INJURY_RISK:
        flags.append(Flag("injury", "risk", "down", f"{availability.play_prob:.0%} to play",
                          f"{availability.label}."))
    return tuple(flags)
