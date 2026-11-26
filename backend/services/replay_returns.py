"""Replay League: when a hard-tagged player really came back (SeasonData.returns). Pure; re-exported by
services/replay_pool.py, which builds `returns` with it."""
from collections import defaultdict
from collections.abc import Mapping
from typing import Protocol

PlayerWeek = tuple[int, int]                # (player_id, week)


class _Tag(Protocol):
    @property
    def hard(self) -> bool: ...


def hard_tag_returns(injuries: Mapping[PlayerWeek, _Tag], actual: Mapping[PlayerWeek, float],
                     last_week: int) -> dict[PlayerWeek, int]:
    """{(player, first week of a hard-tag streak): first later week he really played, 0 = never}. A streak
    runs from a hard tag until his next game (byes and untagged weeks in between stay in it). Only the tag
    starts one: whether he played that week anyway is not known before it is played."""
    tagged: dict[int, list[int]] = defaultdict(list)
    for (player_id, week), injury in injuries.items():
        if injury.hard:
            tagged[player_id].append(week)
    out: dict[PlayerWeek, int] = {}
    for player_id, weeks in tagged.items():
        back = None                                     # the week the open streak ends; 0 = it never does
        for week in sorted(weeks):
            if back is None or 0 < back <= week:
                back = next((w for w in range(week + 1, last_week + 1) if (player_id, w) in actual), 0)
                out[(player_id, week)] = back
    return out
