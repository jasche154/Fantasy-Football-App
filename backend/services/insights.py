"""Buy-low / sell-high signals.

Core idea: expected fantasy points (from usage: targets, carries, air yards, red-zone
looks) predict future scoring better than actual points do. A player scoring well below
expectation with a healthy role is a buy-low; one scoring well above it is a sell-high.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean

from services.projections import GameLine, PlayerSeason, average_expected, expected_games
from services.scoring import SCORING_LABELS
from services.valuation import Valuation

RECENT_GAMES = 3
MIN_GAMES = 3
FPOE_THRESHOLD = 1.0          # pts/game away from expectation before it's a signal
USAGE_DROP_LIMIT = 2.0        # buy-lows can't be losing more than this much expected ppg
USAGE_WEIGHT = 0.5
SLUMP_WEIGHT = 0.3
TD_GAP_THRESHOLD = 1.5
USAGE_TREND_THRESHOLD = 1.0
SNAP_TREND_THRESHOLD = 5.0    # percentage points


@dataclass(frozen=True)
class Insight:
    player_id: int
    name: str
    position: str | None
    team: str | None
    kind: str                  # "buy_low" or "sell_high"
    score: float
    games: int
    ppg: float
    expected_ppg: float
    fpoe_per_game: float
    td_diff: float | None
    recent_ppg: float
    usage_trend: float | None
    trade_value: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Signals:
    # Actual vs expected are compared over the same games (those with expected-points data).
    matched_ppg: float
    expected_ppg: float
    fpoe_per_game: float
    actual_tds: int
    expected_tds: float | None
    td_diff: float | None
    recent_ppg: float
    usage_trend: float | None
    snap_trend: float | None


def _snap_average(games: Sequence[GameLine]) -> float | None:
    values = [g.snap_pct for g in games if g.snap_pct is not None]
    return mean(values) if values else None


def compute_signals(player: PlayerSeason) -> Signals | None:
    expected, fpoe = player.expected_ppg, player.fpoe_per_game
    if player.games_played < MIN_GAMES or expected is None or fpoe is None:
        return None
    covered = expected_games(player.games)
    recent = player.recent(RECENT_GAMES)
    recent_expected = average_expected(recent)
    actual_tds = sum(g.total_tds for g in covered)
    expected_td_values = [g.expected_tds for g in covered]
    expected_tds = (round(sum(expected_td_values), 1)
                    if all(t is not None for t in expected_td_values) else None)
    season_snaps, recent_snaps = _snap_average(player.games), _snap_average(recent)
    snap_trend = (round(recent_snaps - season_snaps, 1)
                  if season_snaps is not None and recent_snaps is not None else None)
    return Signals(
        matched_ppg=round(expected + fpoe, 2), expected_ppg=expected, fpoe_per_game=fpoe,
        actual_tds=actual_tds, expected_tds=expected_tds,
        td_diff=round(actual_tds - expected_tds, 1) if expected_tds is not None else None,
        recent_ppg=round(mean(g.fantasy_points for g in recent), 2),
        usage_trend=round(recent_expected - expected, 2) if recent_expected is not None else None,
        snap_trend=snap_trend,
    )


def _reasons(player: PlayerSeason, signals: Signals, scoring: str) -> tuple[str, ...]:
    label = SCORING_LABELS.get(scoring, scoring)
    gap = signals.fpoe_per_game
    actual, expected = signals.matched_ppg, signals.expected_ppg
    reasons = [
        f"Scoring {abs(gap):.1f} {'fewer' if gap < 0 else 'more'} {label} pts/game than usage predicts "
        f"({actual:.1f} actual vs {expected:.1f} expected)."
        if abs(gap) >= 0.5 else
        f"Production matches usage ({actual:.1f} actual vs {expected:.1f} expected {label} pts/game)."
    ]
    if signals.td_diff is not None and abs(signals.td_diff) >= TD_GAP_THRESHOLD:
        tds = signals.actual_tds
        direction = "below" if signals.td_diff < 0 else "above"
        reasons.append(f"{tds} TD{'' if tds == 1 else 's'} vs {signals.expected_tds:.1f} expected, "
                       f"running {direction} expectation; touchdown rates tend to even out.")
    if signals.usage_trend is not None and abs(signals.usage_trend) >= USAGE_TREND_THRESHOLD:
        direction = "rising" if signals.usage_trend > 0 else "falling"
        reasons.append(f"Usage is {direction}: expected points over the last {RECENT_GAMES} games are "
                       f"{abs(signals.usage_trend):.1f}/game {'above' if signals.usage_trend > 0 else 'below'} "
                       "the season average.")
    if signals.snap_trend is not None and abs(signals.snap_trend) >= SNAP_TREND_THRESHOLD:
        reasons.append(f"Snap share {'up' if signals.snap_trend > 0 else 'down'} {abs(signals.snap_trend):.0f} "
                       f"points over the last {RECENT_GAMES} games.")
    if abs(signals.recent_ppg - player.ppg) >= 3:
        trend = "a slump that can lower the asking price" if signals.recent_ppg < player.ppg \
            else "a hot streak that can raise the asking price"
        reasons.append(f"Last {RECENT_GAMES} games: {signals.recent_ppg:.1f} PPG vs {player.ppg:.1f} season, {trend}.")
    return tuple(reasons)


def _score(kind: str, player: PlayerSeason, signals: Signals, replacement: float,
           trade_value: float) -> float | None:
    """Signal strength as a percent of expected points, so QBs don't swamp other positions.

    Only players with positive trade value qualify: nobody pays for (or buys) a waiver-level player.
    """
    usage = signals.usage_trend or 0.0
    if kind == "buy_low":
        qualifies = (player.expected_ppg >= replacement and signals.fpoe_per_game <= -FPOE_THRESHOLD
                     and usage >= -USAGE_DROP_LIMIT)
        raw = (-signals.fpoe_per_game + USAGE_WEIGHT * usage
               + SLUMP_WEIGHT * max(0.0, player.ppg - signals.recent_ppg))
    else:
        qualifies = (player.ppg >= replacement and signals.fpoe_per_game >= FPOE_THRESHOLD
                     and usage <= USAGE_DROP_LIMIT)
        raw = (signals.fpoe_per_game - USAGE_WEIGHT * usage
               + SLUMP_WEIGHT * max(0.0, signals.recent_ppg - player.ppg))
    if not qualifies or trade_value <= 0:
        return None
    return round(100 * raw / max(player.expected_ppg, 1.0), 1)


def _trade_value(valuation: Valuation, player_id: int) -> float:
    value = valuation.by_id.get(player_id)
    return value.trade_value if value else 0.0


def player_signals(player: PlayerSeason, valuation: Valuation) -> dict:
    """Signal flags and reasons for a single player's profile page."""
    signals = compute_signals(player)
    if signals is None:
        return {"buy_low": False, "sell_high": False,
                "reasons": [f"Needs {MIN_GAMES}+ games with expected-points data for a signal."]}
    replacement = valuation.replacement.get(player.position, 0.0)
    value = _trade_value(valuation, player.player_id)
    return {
        "buy_low": _score("buy_low", player, signals, replacement, value) is not None,
        "sell_high": _score("sell_high", player, signals, replacement, value) is not None,
        "reasons": list(_reasons(player, signals, valuation.league.scoring)),
    }


def find_insights(valuation: Valuation, kind: str, position: str | None = None,
                  limit: int = 10) -> list[Insight]:
    if kind not in ("buy_low", "sell_high"):
        raise ValueError("kind must be 'buy_low' or 'sell_high'")
    insights = []
    for player in valuation.seasons:
        if position and player.position != position:
            continue
        signals = compute_signals(player)
        if signals is None:
            continue
        trade_value = _trade_value(valuation, player.player_id)
        score = _score(kind, player, signals, valuation.replacement.get(player.position, 0.0), trade_value)
        if score is None:
            continue
        insights.append(Insight(
            player_id=player.player_id, name=player.name, position=player.position, team=player.team,
            kind=kind, score=score, games=player.games_played, ppg=player.ppg,
            expected_ppg=player.expected_ppg, fpoe_per_game=signals.fpoe_per_game,
            td_diff=signals.td_diff, recent_ppg=signals.recent_ppg, usage_trend=signals.usage_trend,
            trade_value=trade_value,
            reasons=_reasons(player, signals, valuation.league.scoring),
        ))
    insights.sort(key=lambda i: (-i.score, i.name))
    return insights[:limit]
