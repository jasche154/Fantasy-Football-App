"""Replay League injury news: a noisy, point-in-time report of when an out player comes back.

Built from `SeasonData.returns` (the one sanctioned leak: the real return week), blurred by seeded noise
(`replay_state.rng(seed, "news", player_id, since)`) so it reads like a beat report, never the answer.
Pure: no database, no Knowledge. See .plans/pr1-plan.md section C step 9 for the noise rules.
"""
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Report:
    since: int                 # first week of the hard-tag streak
    back: int | None           # the week he is reported to return; None = out for the season
    text: str


def report(seed: int, player_id: int, since: int, true_return: int, week: int, end_week: int, tag: str) -> Report:
    """What the news says before `week` about a player out since `since` who really returns at
    `true_return` (0 = never). Never names a week <= `week`."""
    raise NotImplementedError


def share_played(report: Report, team_game_weeks: Sequence[int]) -> float:
    """The share of his team's remaining games (`team_game_weeks`) he plays if the report is right."""
    raise NotImplementedError
