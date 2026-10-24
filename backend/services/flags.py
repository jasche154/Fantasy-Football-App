"""Breakout and regression flags, aware of injury news.

| Flag | Kind | Trigger |
|---|---|---|
| Opportunity spike | breakout | A teammate may sit (Questionable or worse) and this player inherits 3+ pts of his work |
| Role growth | breakout | Expected points or snap share rising over the last 3 games |
| Touchdown luck | regression | TDs 2+ above expected (sell) or below (buy) |
| Efficiency outlier | regression | Scoring 3+ pts/game above or below what usage predicts |
| Snap share climbing | breakout | Under 18 pts/game (TE 13) recently; last 3 games' snap share 10+ points above his earlier games, latest 50%+ (route share proxy) |
| Efficient part-timer | breakout | Not yet a fantasy starter; a part-time role (RB <55%, WR <70%, TE <65% of snaps) producing at a pace that's a starter's on a full-time role |
| Competition returning | regression | A starter who missed the last two games is back; his fill-in's measured gain fades (QB backups returning are ignored) |
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


SNAP_RISE = 10.0             # last-3-game snap share over his earlier games, in points
RECENT_GAMES = 3
MIN_LATEST_SNAPS = 50.0
# Breakout flags are for players who haven't broken out: recent points per game below these.
BREAKOUT_CEILING = {"QB": 16.0, "RB": 13.0, "WR": 13.0, "TE": 9.0}
# A growing role still matters for a player already producing; only skip the elite.
SNAP_GROWTH_CEILING = {"RB": 18.0, "WR": 18.0, "TE": 13.0}
# A full-time role differs by position (RBs share far more than WRs).
FULL_TIME_SNAPS = {"RB": 70.0, "WR": 85.0, "TE": 80.0}
PART_TIME_SNAPS = {"RB": 55.0, "WR": 70.0, "TE": 65.0}
PER_SNAP_BAR = {"RB": 15.0, "WR": 15.0, "TE": 11.0}   # pts/game a full-time role would produce


def _not_broken_out(player: PlayerSeason, ceilings: dict[str, float] = BREAKOUT_CEILING) -> bool:
    recent = player.games[-4:]
    ceiling = ceilings.get(player.position or "")
    return bool(recent and ceiling) and sum(g.fantasy_points for g in recent) / len(recent) < ceiling


def _snap_growth(player: PlayerSeason) -> list[Flag]:
    """Snap share up over his last 3 games vs. earlier (route share isn't public in-season; snaps are the proxy)."""
    snaps = [g.snap_pct for g in player.games if g.snap_pct is not None]
    if player.position not in FULL_TIME_SNAPS or len(snaps) < RECENT_GAMES + 2 or not _not_broken_out(player, SNAP_GROWTH_CEILING):
        return []
    recent, earlier = snaps[-RECENT_GAMES:], snaps[:-RECENT_GAMES]
    rise = sum(recent) / len(recent) - sum(earlier) / len(earlier)
    if rise < SNAP_RISE or snaps[-1] < MIN_LATEST_SNAPS:
        return []
    path = " -> ".join(f"{s:.0f}%" for s in snaps[-6:])
    return [Flag("snap_growth", "breakout", "up", "Snap share climbing",
                 f"Snap share by game: {path}; his last {RECENT_GAMES} games average {rise:.0f} points above "
                 "his earlier ones. Usage tends to follow snaps, before the points show up.")]


def _efficient_part_timer(player: PlayerSeason) -> list[Flag]:
    """Productive per snap on a part-time role: a breakout candidate if the role grows."""
    position = player.position or ""
    games = [g for g in player.games if g.snap_pct]
    if position not in PER_SNAP_BAR or len(games) < 3 or not _not_broken_out(player):
        return []
    snap_avg = sum(g.snap_pct for g in games) / len(games)
    if snap_avg >= PART_TIME_SNAPS[position]:
        return []
    per_game = sum(g.fantasy_points for g in games) / len(games)
    full_time = per_game * FULL_TIME_SNAPS[position] / snap_avg
    if full_time < PER_SNAP_BAR[position]:
        return []
    return [Flag("efficient_part_timer", "breakout", "up", "Efficient part-timer",
                 f"{per_game:.1f} pts/game on {snap_avg:.0f}% of snaps; at a full-time {position} role "
                 f"({FULL_TIME_SNAPS[position]:.0f}%) that pace is about {full_time:.1f}. A breakout if his role grows.")]


def player_flags(player: PlayerSeason, context: InjuryContext) -> tuple[Flag, ...]:
    flags: list[Flag] = []
    for boost in context.boosts.get(player.player_id, ()):
        if boost.full_points >= OPPORTUNITY_POINTS:
            flags.append(Flag("opportunity", "breakout", "up", f"+{boost.full_points:.1f} if {boost.from_name} sits",
                              f"{boost.from_name} is {boost.from_status}. If he sits, {player.name} inherits about "
                              f"{boost.full_points:.1f} expected pts ({boost.method})."))
    for back in context.returning.get(player.player_id, ()):
        impact = (f" In {back.games_without} games without him, {player.name} gained about "
                  f"{back.expected_loss:.1f} expected pts/game; expect much of that to go back."
                  if back.expected_loss > 0 else "")
        flags.append(Flag("competition_returning", "regression", "down", f"{back.name} returning",
                          f"{back.name} missed the last two games and is expected back, so {player.name}'s "
                          f"expanded role may shrink.{impact}"))
    flags.extend(_snap_growth(player))
    flags.extend(_efficient_part_timer(player))
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
