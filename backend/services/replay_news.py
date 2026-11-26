"""Replay League injury news: a noisy, point-in-time report of when an out player comes back.

Built from `SeasonData.returns` (the one sanctioned leak: the real return week), blurred by seeded noise
(`replay_state.rng(seed, "news", player_id, since)`) so it reads like a beat report, never the answer.
Pure: no database, no Knowledge.

- A suspension is exact.
- The commissioner's exempt list (EXE) is indefinite: the text never names a week, and the week valuations read
  (`back`) is a guess made without the real return (FAKE_RETURN_WEEKS after he went on the list, then always
  the next week).
- Otherwise, until a seeded FIRM_LEAD (1-7) weeks before the real return, the report reads "no timetable yet" and the
  week valuations use is a guess drawn from what the public can know: how much longer real absences with that
  tag group lasted given how long he has been out already (NEWS_LENGTHS, fitted on 2018-25 hard-tag streaks:
  `python -m services.replay_chart_eval --news-lengths`), never the real return; a guess that passes is drawn again
  the same way. From the firm week on it names the real week plus an error drawn afresh for each report week from
  NAMED_ERRORS, narrowing as the return nears (one week out right four times in five), so no revision pattern pins
  the date. A report never names a week he has already missed, nor (for a player who does return) one after the
  season's last week. At the draft a preseason IR or PUP move (replay_pool.PRESEASON_TAGS) reads "no timetable
  yet", valued on the preseason group's prior (streaks that began in week 1), never the real return; a suspension
  shows its length, which is public.
- A real return after the season's last week reads "out for the season" (IR / PUP only).
- A real absence of FALSE_SEASON_GAP or more weeks that starts in-season: FALSE_SEASON_ODDS of first reading "out for
  the season", corrected CORRECTION_LEAD weeks before he really returns (never a preseason move: the draft's
  "no timetable yet" must not flip to it in week 1).
- Truly season-ending: FAKE_RETURN_ODDS of first naming a return 4-8 weeks out, revised after 1-3 weeks.
- Only a SEASON_TAGS tag (IR, PUP) ever reads "out for the season". A plain O tag is week-to-week news: a
  truly season-ending absence names a return 4-8 weeks out, then always the next week, until he is moved to
  IR (as Cam Newton in 2019: O from week 3, IR in week 10); past the season's last week it reads "no timetable
  yet", never "out for the season".
"""
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services import replay_state
from services.replay_pool import PRESEASON_TAGS, SeasonData

FALSE_SEASON_GAP = 4
FALSE_SEASON_ODDS = 0.10
CORRECTION_LEAD = 2
FAKE_RETURN_ODDS = 0.35
FAKE_RETURN_WEEKS = (4, 8)                 # inclusive: how far out a wrong first report puts the return
FAKE_REVISED_AFTER = (1, 3)                # inclusive: weeks until that is revised to out for the season
SEASON_TAGS = ("IR", "PUP")                # the only tags a report calls season-ending
FIRM_LEAD = (1, 7)                         # inclusive: weeks before the real return the news firms up, seeded
NAMED_ERRORS = {                           # weeks to the real return (3 = 3+) -> (errors, odds), drawn per report week
    1: ((0, 1), (0.8, 0.2)),
    2: ((-1, 0, 1, 2), (0.2, 0.45, 0.25, 0.10)),
    3: ((-2, -1, 0, 1, 2, 3), (0.10, 0.20, 0.35, 0.20, 0.10, 0.05)),
}
NEWS_LENGTHS_PATH = Path(__file__).with_name("replay_news_lengths.json")
NO_RETURN_WEEKS = 99                       # a "never back" draw: the guess sits at the season's last week


@dataclass(frozen=True)
class Report:
    since: int                 # first week of the hard-tag streak
    back: int | None           # the week he is reported to return (a guess when `vague`); None = out for the season
    text: str
    vague: bool = False        # "no timetable yet": views show no week, valuations read the guessed `back`


def _text(tag: str, since: int, back: int | None, vague: bool = False) -> str:
    if tag == "EXE":
        return f"On the commissioner's exempt list since week {since}; out indefinitely."
    if vague:
        return f"Out ({tag}) since week {since}; no timetable yet."
    if back is None:
        return f"Out for the season ({tag} since week {since})."
    verb = "Suspended" if tag == "SUS" else f"Out ({tag})"
    return f"{verb} since week {since}; expected back week {back}."


@lru_cache(maxsize=1)
def news_lengths() -> Mapping[str, tuple[tuple[int, int], ...]]:
    """{"long" | "short": ((weeks out, absences), ...)} from NEWS_LENGTHS_PATH ("never" -> NO_RETURN_WEEKS)."""
    raw = json.loads(NEWS_LENGTHS_PATH.read_text(encoding="utf-8"))
    return MappingProxyType({group: tuple(sorted((NO_RETURN_WEEKS if k == "never" else int(k), n)
                                                 for k, n in counts.items()))
                             for group, counts in raw.items()})


def _length(group: str, done: int, u: float) -> int:
    """Weeks from the first tag to the return, drawn (quantile `u`) from the absences that lasted longer than `done`
    and ended that season: a vague report is not a season-ending one, so the public expects him back."""
    longer = ([(weeks, n) for weeks, n in news_lengths()[group] if done < weeks < NO_RETURN_WEEKS]
              or [(NO_RETURN_WEEKS, 1)])
    target, total = u * sum(n for _, n in longer), 0
    for weeks, n in longer:
        total += n
        if total >= target:
            return weeks
    return longer[-1][0]


def _guess(seed: int, player_id: int, since: int, week: int, tag: str, last_week: int | None) -> int:
    """The vague report's valuation week: a public draw, moved on by a fresh draw each time it passes."""
    rng = replay_state.rng(seed, "news-guess", player_id, since)
    group = "preseason" if since == 1 and "preseason" in news_lengths() else "long" if tag in SEASON_TAGS else "short"
    guess = since + _length(group, 0, float(rng.random()))
    while guess <= week:                    # passed: he has been out longer, and not "next week" twice in a row
        guess = since + _length(group, max(guess, week + 1) - since, float(rng.random()))
    return guess if last_week is None else min(guess, max(last_week, week + 1))


def _named(seed: int, player_id: int, since: int, true_return: int, week: int) -> int:
    """The firm report's week: the real return plus an error drawn for this report week, never one that would name
    a week already played or say "next week" when he is not back next week."""
    table = NAMED_ERRORS[min(true_return - week, 3)]
    allowed = [(e, w) for e, w in zip(*table) if true_return + e > week + 1 or e == 0] or [(0, 1.0)]
    total = sum(w for _, w in allowed)
    errors, odds = [e for e, _ in allowed], [w / total for _, w in allowed]
    return true_return + int(replay_state.rng(seed, "news-named", player_id, since, week).choice(errors, p=odds))


def report(seed: int, player_id: int, since: int, true_return: int, week: int, tag: str,
           last_week: int | None = None) -> Report:
    """What the news says before `week` about a player out since `since` who really returns at
    `true_return` (0 = never). Never names a week <= `week`; a returning player's week stays within the season."""
    rng = replay_state.rng(seed, "news", player_id, since)
    # every draw is made, always in this order, so a report never depends on which branch an earlier week took
    false_season = rng.random() < FALSE_SEASON_ODDS
    fake_return = rng.random() < FAKE_RETURN_ODDS
    fake_weeks = int(rng.integers(FAKE_RETURN_WEEKS[0], FAKE_RETURN_WEEKS[1] + 1))
    revised_after = int(rng.integers(FAKE_REVISED_AFTER[0], FAKE_REVISED_AFTER[1] + 1))
    firm_lead = int(rng.integers(FIRM_LEAD[0], FIRM_LEAD[1] + 1))

    long_tag, vague = tag in SEASON_TAGS, False
    if tag == "SUS":
        back = true_return or None
    elif tag == "EXE":
        back = since + fake_weeks
    elif week < since:                                  # the draft, before a preseason move's season starts:
        back, vague = _guess(seed, player_id, since, week, tag, last_week), True    # he starts on IR, never how long
    elif not true_return:
        hopeful = not long_tag or (fake_return and week < since + revised_after)
        back = since + fake_weeks if hopeful else None
    elif (false_season and long_tag and since > 1 and true_return - since >= FALSE_SEASON_GAP  # a preseason move's first
          and week < true_return - CORRECTION_LEAD):                                         # report is the draft's
        back = None
    elif week < true_return - firm_lead:
        back, vague = _guess(seed, player_id, since, week, tag, last_week), True
    else:
        named = _named(seed, player_id, since, true_return, week)
        back = named if last_week is None else min(named, max(last_week, true_return))
    if back is not None:
        back = max(back, week + 1)
    if back is not None and last_week is not None and back > last_week:
        back, vague = None, vague or tag not in (*SEASON_TAGS, "SUS")   # an O tag never reads "out for the season"
    return Report(since=since, back=back, text=_text(tag, since, back, vague), vague=vague)


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
    tag = data.injuries.get((player_id, max(week, 1)))          # the draft (week 0) knows week 1's preseason move
    if tag is None or not tag.hard or (not week and tag.tag not in PRESEASON_TAGS):
        return None
    open_ = [(since, back) for since, back in _streaks(data).get(player_id, ())
             if since <= max(week, 1) and (back == 0 or back > week)]
    return (*open_[-1], tag.tag) if open_ else None
