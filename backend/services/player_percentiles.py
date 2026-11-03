"""Where a player's season stats rank among same-position players.

The pool is every player at the position with at least `pool_min_games` regular-season
games (POOL_MIN_GAMES, relaxed early in the season). Each stat also needs its own sample
(a yards-per-carry needs MIN_CARRIES carries): players below it are left out of that
stat's pool, and a player's own value below it is shown as "small sample" with no
percentile. Percentiles count ties as half; stats where lower is better (interceptions,
fumbles) are flipped so 90 always reads "better than 90%". Context stats (aDOT) get a
percentile but no good/bad direction.
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import mean

from services.player_usage import UsageGame

POOL_MIN_GAMES = 4
MIN_SHARE_GAMES = 3          # games with a share value (target, air-yard or snap share)
MIN_PASS_ATTEMPTS = 100
MIN_CARRIES = 40
MIN_TARGETS = 20
MIN_TOUCHES = 50
HIGHER, LOWER, CONTEXT = "higher", "lower", "context"


@dataclass(frozen=True)
class Totals:
    games: int
    sums: dict[str, float]
    shares: dict[str, list[float]]

    def per_game(self, key: str) -> float:
        return self.sums[key] / self.games

    def ratio(self, key: str, over: str, scale: float = 1.0) -> float | None:
        return scale * self.sums[key] / self.sums[over] if self.sums[over] else None

    def share(self, key: str) -> float | None:
        return mean(self.shares[key]) if self.shares[key] else None


def totals(games: Sequence[UsageGame]) -> Totals:
    sums: dict[str, float] = {"fantasy_points": sum(g.fantasy_points for g in games)}
    for key in games[0].stats if games else ():
        sums[key] = sum(g.stats[key] for g in games)
    if games:
        sums["fumbles_lost"] = sums["sack_fumbles_lost"] + sums["rushing_fumbles_lost"] + sums["receiving_fumbles_lost"]
        sums["touches"] = sums["carries"] + sums["receptions"]
    shares = {key: [getattr(g, key) for g in games if getattr(g, key) is not None]
              for key in ("target_share", "air_yards_share", "snap_share")}
    return Totals(games=len(games), sums=sums, shares=shares)


@dataclass(frozen=True)
class StatDef:
    key: str
    label: str
    positions: tuple[str, ...]
    value: Callable[[Totals], float | None]
    sample: Callable[[Totals], float]     # the volume behind the stat
    min_sample: float
    sample_unit: str
    direction: str = HIGHER
    unit: str = ""


ALL, PASS_CATCHERS, RUSHERS = ("QB", "RB", "WR", "TE"), ("RB", "WR", "TE"), ("QB", "RB")


def _games(t: Totals) -> float:
    return t.games


STATS = (
    StatDef("ppg", "Fantasy PPG", ALL, lambda t: t.per_game("fantasy_points"), _games, POOL_MIN_GAMES, "games"),
    StatDef("snap_share", "Snap share", ALL, lambda t: t.share("snap_share"),
            lambda t: len(t.shares["snap_share"]), MIN_SHARE_GAMES, "games", unit="%"),
    StatDef("pass_att_pg", "Pass attempts / game", ("QB",), lambda t: t.per_game("attempts"),
            _games, POOL_MIN_GAMES, "games"),
    StatDef("ypa", "Yards / attempt", ("QB",), lambda t: t.ratio("passing_yards", "attempts"),
            lambda t: t.sums["attempts"], MIN_PASS_ATTEMPTS, "attempts"),
    StatDef("pass_td_rate", "TD rate", ("QB",), lambda t: t.ratio("passing_tds", "attempts", 100),
            lambda t: t.sums["attempts"], MIN_PASS_ATTEMPTS, "attempts", unit="%"),
    StatDef("int_rate", "INT rate", ("QB",), lambda t: t.ratio("passing_interceptions", "attempts", 100),
            lambda t: t.sums["attempts"], MIN_PASS_ATTEMPTS, "attempts", direction=LOWER, unit="%"),
    StatDef("pass_adot", "Air yards / attempt", ("QB",), lambda t: t.ratio("passing_air_yards", "attempts"),
            lambda t: t.sums["attempts"], MIN_PASS_ATTEMPTS, "attempts", direction=CONTEXT),
    StatDef("carries_pg", "Rush attempts / game", RUSHERS, lambda t: t.per_game("carries"),
            _games, POOL_MIN_GAMES, "games"),
    StatDef("ypc", "Yards / carry", RUSHERS, lambda t: t.ratio("rushing_yards", "carries"),
            lambda t: t.sums["carries"], MIN_CARRIES, "carries"),
    StatDef("targets_pg", "Targets / game", PASS_CATCHERS, lambda t: t.per_game("targets"),
            _games, POOL_MIN_GAMES, "games"),
    StatDef("target_share", "Target share", PASS_CATCHERS, lambda t: t.share("target_share"),
            lambda t: len(t.shares["target_share"]), MIN_SHARE_GAMES, "games", unit="%"),
    StatDef("air_yards_share", "Air-yard share", ("WR", "TE"), lambda t: t.share("air_yards_share"),
            lambda t: len(t.shares["air_yards_share"]), MIN_SHARE_GAMES, "games", unit="%"),
    StatDef("yards_per_target", "Yards / target", PASS_CATCHERS, lambda t: t.ratio("receiving_yards", "targets"),
            lambda t: t.sums["targets"], MIN_TARGETS, "targets"),
    StatDef("catch_rate", "Catch rate", PASS_CATCHERS, lambda t: t.ratio("receptions", "targets", 100),
            lambda t: t.sums["targets"], MIN_TARGETS, "targets", unit="%"),
    StatDef("adot", "aDOT", ("WR", "TE"), lambda t: t.ratio("receiving_air_yards", "targets"),
            lambda t: t.sums["targets"], MIN_TARGETS, "targets", direction=CONTEXT),
    StatDef("fumble_rate", "Fumbles lost / 100 touches", ("RB", "WR", "TE"),
            lambda t: t.ratio("fumbles_lost", "touches", 100),
            lambda t: t.sums["touches"], MIN_TOUCHES, "touches", direction=LOWER),
)


def pool_min_games(by_player: dict[int, Sequence[UsageGame]]) -> int:
    """POOL_MIN_GAMES, or half the most games anyone has played when the season is young."""
    most = max((len(g) for g in by_player.values()), default=0)
    return max(1, min(POOL_MIN_GAMES, most // 2))


def percentile(value: float, pool: Sequence[float]) -> int:
    below = sum(1 for v in pool if v < value)
    ties = sum(1 for v in pool if v == value)
    return round(100 * (below + 0.5 * ties) / len(pool))


def player_percentiles(player_id: int, position: str | None,
                       by_player: dict[int, Sequence[UsageGame]]) -> dict:
    """Percentile rows for one player against everyone at his position."""
    min_games = pool_min_games(by_player)
    pool = {pid: totals(games) for pid, games in by_player.items() if len(games) >= min_games}
    mine = totals(by_player.get(player_id, ()))
    rows = []
    for stat in STATS:
        if position not in stat.positions or not mine.games:
            continue
        own = stat.value(mine)
        qualified = [v for t in pool.values()
                     if stat.sample(t) >= stat.min_sample and (v := stat.value(t)) is not None]
        small = player_id not in pool or stat.sample(mine) < stat.min_sample or own is None
        pct = None if small or not qualified else percentile(own, qualified)
        if pct is not None and stat.direction == LOWER:
            pct = 100 - pct
        rows.append({"key": stat.key, "label": stat.label, "value": round(own, 2) if own is not None else None,
                     "unit": stat.unit, "percentile": pct, "direction": stat.direction,
                     "small_sample": small, "sample": round(stat.sample(mine), 1),
                     "min_sample": stat.min_sample, "sample_unit": stat.sample_unit, "pool_size": len(qualified)})
    return {"position": position, "pool_min_games": min_games, "pool_players": len(pool), "stats": rows}
