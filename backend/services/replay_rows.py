"""Replay League player rows: what one player's line in a week's values holds before charts and value over
replacement (services/replay_values.py's docstring: prior_ppg, ros_ppg, the out streak and availability).
Re-exported by replay_values."""
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from types import MappingProxyType

from services import replay_chart_injury
from services.replay_pool import PRESEASON_TAGS, PoolPlayer, SeasonData

RECENCY_WEIGHTS = (4, 3, 2, 1)                        # newest first: the projections (or priors) behind ros_ppg
OUT_SHARE = {1: 0.45, 2: 0.55, 3: 0.60, 4: 0.65, 5: 0.75}    # share of remaining games missed, by weeks out so far
OUT_SHARE_LONG = 0.90                                 # six or more weeks out (measured, RB/WR/TE 2021-25)
IR_OUT_SHARE = replay_chart_injury.IR_OUT_SHARE      # least share missed when tagged IR or PUP (measured 2018-25)
RECENT_GAMES = 5                                      # recent_points: his last games before this week (the chart's form window;
                                                      # other readers slice their own: replay_claim_ai, replay_mood)


@lru_cache(maxsize=64)
def _priors(data: SeasonData) -> Mapping[int, float]:
    """{player_id: preseason points per game} from ADP order and the week-1 projection curve."""
    by_position: dict[str, list[PoolPlayer]] = defaultdict(list)
    for player in data.players.values():
        by_position[player.position].append(player)
    priors: dict[int, float] = {}
    for players in by_position.values():
        curve = sorted((data.projection(p.player_id, 1) for p in players), reverse=True)
        drafted = sorted((p for p in players if p.adp is not None), key=lambda p: (p.adp, p.player_id))
        priors.update({p.player_id: data.projection(p.player_id, 1) for p in players if p.adp is None})
        priors.update({p.player_id: curve[n] for n, p in enumerate(drafted)})
    return MappingProxyType(priors)


def _projection(data: SeasonData, player_id: int, week: int) -> float:
    """That week's projection, or 0 when he carries a hard tag that week (a stale projection is not a plan)."""
    tag = data.injuries.get((player_id, week))
    return 0.0 if tag is not None and tag.hard else data.projection(player_id, week)


def _recent(data: SeasonData, player_id: int, week: int) -> list[float]:
    """His last non-zero projections up to `week`, newest first (at most one per weight)."""
    found: list[float] = []
    for w in range(week, 0, -1):
        if (points := _projection(data, player_id, w)) > 0:
            found.append(points)
            if len(found) == len(RECENCY_WEIGHTS):
                break
    return found


def _ros_ppg(recent: Sequence[float], prior: float) -> float:
    samples = [*recent, *[prior] * (len(RECENCY_WEIGHTS) - len(recent))]
    return sum(weight * points for weight, points in zip(RECENCY_WEIGHTS, samples)) / sum(RECENCY_WEIGHTS)


def _out_streak(data: SeasonData, player_id: int, prior: float, week: int) -> int:
    """Consecutive non-bye weeks ending at `week` with no projection, for a player who had one or a prior."""
    streak = 0
    for w in range(week, 0, -1):
        if _projection(data, player_id, w) > 0:
            return streak
        if not data.on_bye(player_id, w):
            streak += 1
    return streak if prior > 0 else 0


def _row(data: SeasonData, player: PoolPlayer, prior: float, week: int, team: str | None,
         games_left: int) -> dict:
    """Everything about a player except his value over replacement and his rank."""
    pid = player.player_id
    streak = _out_streak(data, pid, prior, week)
    injury = data.injuries.get((pid, max(1, week)))     # draft time: a preseason IR/PUP/SUS (replay_pool.as_of)
    if not week and (injury is None or injury.tag not in PRESEASON_TAGS):
        injury = None
    share = OUT_SHARE.get(streak, OUT_SHARE_LONG) if streak else 0.0
    if injury is not None and injury.tag in ("IR", "PUP"):
        share = max(share, IR_OUT_SHARE)
    recent = _recent(data, pid, week)
    weeks = [w for w in range(1, week) if (pid, w) in data.actual]
    played = [data.actual[(pid, w)] for w in weeks]
    return {
        "player_id": pid, "name": player.name, "position": player.position, "adp": player.adp, "team": team,
        "ros_ppg": round(_ros_ppg(recent, prior), 2),
        "projection": _projection(data, pid, week) if week else prior,   # draft time: only what ADP knew
        "last_projection": recent[0] if recent else 0.0, "injury": injury,
        "out": streak > 0 or (injury is not None and injury.hard), "games_left": games_left,
        "expected_missed": round(games_left * share, 2), "availability": round(1.0 - share, 3),
        "games": len(played), "ppg": round(sum(played) / len(played), 2) if played else 0.0,
        "recent_points": tuple(played[::-1][:RECENT_GAMES]), "last_game_week": weeks[-1] if weeks else 0,
    }
