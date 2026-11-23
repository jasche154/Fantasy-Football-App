"""Replay League: what the trade chart's model rank adds to replay_chart.model_ppg (point-in-time, pure).

ros_ppg averages a player's last four site projections, so the model half of the chart trailed the site by
weeks: a back who won his backfield, or a receiver whose star teammate went down, barely moved. Two
adjustments, each judged out of sample (2021-25, weeks 2-11: Spearman per position and top-N hits against
real points from that week on; see README):
- latest projection (from week 2): + LATEST_WEIGHT x (his newest projection - ros_ppg). The site re-rates a
  back who took the work (or lost it) every week; this lets that count now instead of a quarter at a time.
- vacated work: a teammate at his position expected to miss a share m of his remaining games (his seed-free
  availability from replay_values: out streak, IR) leaves m x his rate, of which injuries.REDISTRIBUTION's
  same-position share (RB 75%, WR 60%, TE 50%) is split over the others by rate (at most
  injuries.MAX_BOOST_SHARE to one; a player out for the season takes none). Only a receiver with no
  projection since the absence began gets it: he is out or on a bye himself, so nothing has priced it yet
  (Mike Evans when Chris Godwin went down in 2024); a healthy receiver's newest projection already has.
A usage-share signal (each back's share of his team's carries and targets against the share his rate
implies) was tried and left out: when it moved a player, his real rank agreed less often than not.
Reads only `data.as_of(week)`.
"""
from collections import defaultdict
from collections.abc import Mapping, Sequence

from services.injuries import MAX_BOOST_SHARE, REDISTRIBUTION
from services.replay_pool import SeasonData

VACATED_POSITIONS = ("RB", "WR", "TE")
LATEST_WEIGHT = 0.5            # x (newest projection - ros_ppg); 0.25-0.75 all beat 0, 0.5 best in every held-out season
LATEST_FROM_WEEK = 2           # once games are played: week 1's projection is still the draft's (and its matchup)


def _last_projected(data: SeasonData, player_id: int, week: int) -> int:
    """The last week up to `week` with a projection for him (0 = none)."""
    return next((w for w in range(week, 0, -1) if data.projection(player_id, w) > 0), 0)


def _absent_since(data: SeasonData, player_id: int, week: int) -> int:
    """First non-bye week of his current run without a projection up to `week`; `week + 1` if he has one."""
    since = week + 1
    for w in range(week, _last_projected(data, player_id, week), -1):
        if not data.on_bye(player_id, w):
            since = w
    return since


def _teams(group: Sequence[dict]) -> list[list[dict]]:
    by_team: dict[str, list[dict]] = defaultdict(list)
    for row in group:
        if row["team"] is not None and row["position"] in VACATED_POSITIONS:
            by_team[row["team"]].append(row)
    return list(by_team.values())


def vacated(data: SeasonData, group: Sequence[dict], rate: Mapping[int, float], week: int) -> dict[int, float]:
    """{id: extra points per game} from teammates at his position expected to miss games. `group` is one
    position's replay_values rows (team, availability), `rate` their points per game."""
    if week < 1:
        return {}
    boost: dict[int, float] = defaultdict(float)
    for rows in _teams(group):
        for gone in rows:
            missed = 1.0 - gone["availability"]
            if missed <= 0 or rate[gone["player_id"]] <= 0:
                continue
            takers = [r for r in rows if r is not gone and rate[r["player_id"]] > 0 and r["availability"] > 0]
            total = sum(rate[r["player_id"]] for r in takers)
            since = _absent_since(data, gone["player_id"], week)
            left = REDISTRIBUTION[gone["position"]][gone["position"]] * missed * rate[gone["player_id"]]
            for row in takers:
                pid = row["player_id"]
                if _last_projected(data, pid, week) < since:            # nothing he was projected on saw it
                    boost[pid] += left * min(MAX_BOOST_SHARE, rate[pid] / total)
    return dict(boost)


def model_scores(data: SeasonData, group: Sequence[dict], base: Mapping[int, float], week: int) -> dict[int, float]:
    """{id: model points per game}: `base` (replay_chart.model_ppg) plus the newest projection and the
    vacated work. `group` is one position's replay_values rows."""
    data = data.as_of(week)                     # whatever the caller passes, nothing from `week`'s games on
    scores = {}
    for row in group:
        latest = row["last_projection"]
        shift = LATEST_WEIGHT * (latest - row["ros_ppg"]) if latest > 0 and week >= LATEST_FROM_WEEK else 0.0
        scores[row["player_id"]] = base[row["player_id"]] + shift
    for pid, extra in vacated(data, group, scores, week).items():
        scores[pid] += extra
    return scores
