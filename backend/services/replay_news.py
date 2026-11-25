"""Replay League injury news: a noisy, point-in-time report of when an out player comes back.

Built from `SeasonData.returns` (the one sanctioned leak: the real return week), blurred by seeded noise
(`replay_state.rng(seed, "news", player_id, since)`) so it reads like a beat report, never the answer.
Pure: no database, no Knowledge.

- A suspension is exact.
- The commissioner's exempt list (EXE) is indefinite: the text never names a week, and the week valuations read
  (`back`) is a guess made without the real return (FAKE_RETURN_WEEKS after he went on the list, then always
  the next week).
- Otherwise the first report misses the real week by -2..+2 (ERRORS / ERROR_ODDS), and the miss shrinks to
  zero as the real return nears. A report never names a week he has already missed (<= the current week).
- A return after the season's last week reads "out for the season".
- A real absence of FALSE_SEASON_GAP or more weeks: FALSE_SEASON_ODDS of first reading "out for the season",
  corrected CORRECTION_LEAD weeks before he really returns.
- Truly season-ending: FAKE_RETURN_ODDS of first naming a return 4-8 weeks out, revised after 1-3 weeks.
- Only a SEASON_TAGS tag (IR, PUP) ever reads "out for the season". A plain O tag is week-to-week news: a
  truly season-ending absence names a return 4-8 weeks out, then always the next week, until he is moved to
  IR (as Cam Newton in 2019: O from week 3, IR in week 10), so "out for the season" on an O tag is no tell.
"""
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from services import replay_state
from services.replay_pool import SeasonData

ERRORS = (-2, -1, 0, 1, 2)                 # weeks the first report is off by
ERROR_ODDS = (0.15, 0.25, 0.35, 0.15, 0.10)
FALSE_SEASON_GAP = 4
FALSE_SEASON_ODDS = 0.10
CORRECTION_LEAD = 2
FAKE_RETURN_ODDS = 0.35
FAKE_RETURN_WEEKS = (4, 8)                 # inclusive: how far out a wrong first report puts the return
FAKE_REVISED_AFTER = (1, 3)                # inclusive: weeks until that is revised to out for the season
SEASON_TAGS = ("IR", "PUP")                # the only tags a report calls season-ending


@dataclass(frozen=True)
class Report:
    since: int                 # first week of the hard-tag streak
    back: int | None           # the week he is reported to return; None = out for the season
    text: str


def _text(tag: str, since: int, back: int | None) -> str:
    if tag == "EXE":
        return f"On the commissioner's exempt list since week {since}; out indefinitely."
    if back is None:
        return f"Out for the season ({tag} since week {since})."
    verb = "Suspended" if tag == "SUS" else f"Out ({tag})"
    return f"{verb} since week {since}; expected back week {back}."


def report(seed: int, player_id: int, since: int, true_return: int, week: int, tag: str,
           last_week: int | None = None) -> Report:
    """What the news says before `week` about a player out since `since` who really returns at
    `true_return` (0 = never). Never names a week <= `week`, nor one after `last_week` (the season's last week):
    that reads out for the season."""
    rng = replay_state.rng(seed, "news", player_id, since)
    # every draw is made, always in this order, so a report never depends on which branch an earlier week took
    false_season = rng.random() < FALSE_SEASON_ODDS
    error = int(rng.choice(ERRORS, p=ERROR_ODDS))
    fake_return = rng.random() < FAKE_RETURN_ODDS
    fake_weeks = int(rng.integers(FAKE_RETURN_WEEKS[0], FAKE_RETURN_WEEKS[1] + 1))
    revised_after = int(rng.integers(FAKE_REVISED_AFTER[0], FAKE_REVISED_AFTER[1] + 1))

    long_tag = tag in SEASON_TAGS
    if tag == "SUS":
        back = true_return or None
    elif tag == "EXE":
        back = since + fake_weeks
    elif not true_return:
        hopeful = not long_tag or (fake_return and week < since + revised_after)
        back = since + fake_weeks if hopeful else None
    elif (false_season and long_tag and true_return - since >= FALSE_SEASON_GAP
          and week < true_return - CORRECTION_LEAD):
        back = None
    else:
        left = max(0, true_return - week) / max(1, true_return - since)
        back = true_return + round(error * min(1.0, left))
    if back is not None:
        back = max(back, week + 1)
    if back is not None and last_week is not None and back > last_week:
        back = None
    return Report(since=since, back=back, text=_text(tag, since, back))


def share_played(report: Report, team_game_weeks: Sequence[int]) -> float:
    """The share of his team's remaining games (`team_game_weeks`) he plays if the report is right."""
    if report.back is None:
        return 0.0
    if not team_game_weeks:
        return 1.0
    return sum(w >= report.back for w in team_game_weeks) / len(team_game_weeks)


@lru_cache(maxsize=64)
def _streaks(data: SeasonData) -> Mapping[int, tuple[tuple[int, int], ...]]:
    """{player: ((streak start, real return), ...)} from data.returns, oldest first."""
    found: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for (pid, since), back in data.returns.items():
        found[pid].append((since, back))
    return MappingProxyType({pid: tuple(sorted(streaks)) for pid, streaks in found.items()})


def clear_cache() -> None:
    _streaks.cache_clear()


def open_streak(data: SeasonData, player_id: int, week: int) -> tuple[int, int, str] | None:
    """(since, real return, tag) for a player hard-tagged this week whose streak (a `returns` entry started
    by `week` and not yet over) is known, else None. Seed-free: only `report` adds the noise."""
    tag = data.injuries.get((player_id, week)) if week else None
    if tag is None or not tag.hard:
        return None
    open_ = [(since, back) for since, back in _streaks(data).get(player_id, ())
             if since <= week and (back == 0 or back > week)]
    return (*open_[-1], tag.tag) if open_ else None
