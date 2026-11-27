"""Replay League injury freeze (services/replay_values.py, README "Injured players' charts"): a player out with
an injury is valued at his healthy chart of the week before the absence, built point in time from that week.
Re-exported by replay_values.

`chart_view(data, league)`: (the 1-QB season, the season the chart's value curve reads, the season whose ADP
sets the preseason picks): the superflex view for a superflex lineup, then under 12 teams
replay_league_size.sized for the curve only."""
from collections import defaultdict
from collections.abc import Mapping
from functools import lru_cache
from types import MappingProxyType

from services import replay_chart, replay_league_size, replay_pool, replay_slots, replay_superflex
from services.league import LeagueSettings
from services.replay_pool import SeasonData
from services.replay_rows import _priors, _row


def chart_view(data: SeasonData, league: LeagueSettings) -> tuple[SeasonData, SeasonData, SeasonData]:
    """(regular, curve, board) for `data` as `league` values it (module docstring)."""
    regular = replay_superflex.one_qb(data)                     # superflex: non-QBs as in a 1-QB league
    board = replay_chart.superflex_view(data) if replay_slots.two_qb(league.starters) else regular
    return regular, replay_league_size.sized(board, league), board


def _absences(data: SeasonData, week: int) -> dict[int, int]:
    """{player: the last week before his open injury absence} (0 = preseason: hurt before week 1); `data` is as
    of `week`. An absence starts with a hard tag and ends at the first earlier week he was available for a game:
    he played, or his team played and he carried no hard tag (a healthy scratch or a backup with no stat line
    is back too, so nobody stays frozen by never recording a stat again)."""
    tagged: dict[int, set[int]] = defaultdict(set)
    for (pid, w), tag in data.injuries.items():
        if tag.hard and w <= week:
            tagged[pid].add(w)
    found: dict[int, int] = {}
    for pid, weeks in tagged.items():
        start = None
        for w in range(min(weeks), week + 1):
            if w in weeks and start is None:
                start = w
            if w < week and ((pid, w) in data.actual or (w not in weeks and data.schedule.opponent(
                    data.team_at(pid, w), w) is not None)):
                start = None                                # available that week: the absence is over
        if start is not None:
            found[pid] = start - 1
    return found


def _frozen(data: SeasonData, league: LeagueSettings, week: int, end_week: int, drafted: int) -> dict[int, float]:
    """{player: healthy chart, without the bye bump} frozen at the last week before his open injury absence, until
    he is available for a game again (`_absences`): the expert and model ranks of a hurt player fall while he is out (the experts price the
    injury), and the chart's discount (replay_chart_injury) is the one price of it. Point in time: the frozen
    week's values are built from data.as_of(that week). A player listed nowhere that week keeps his live value."""
    by_week: dict[int, list[int]] = defaultdict(list)
    for pid, before in _absences(data, week).items():
        by_week[before].append(pid)
    found: dict[int, float] = {}
    for before, pids in by_week.items():
        then = data.as_of(before)
        positions = tuple(sorted({then.players[pid].position for pid in pids if pid in then.players}))
        live, teams = _live(then, league, before, end_week, min(drafted, before), positions)
        frozen_then = _absences(then, before)       # frozen that week too: `_base` kept his live value unrounded
        for pid in pids:
            if pid in live:
                bumped = replay_chart._bye_done(data, teams[pid], before)     # replay_chart re-applies today's bump
                value = live[pid] if pid in frozen_then else round(live[pid], 1)
                found[pid] = value / (replay_chart.BYE_BUMP if bumped else 1.0)
    return found


@lru_cache(maxsize=256)
def _live(data: SeasonData, league: LeagueSettings, week: int, end_week: int, drafted: int,
          positions: tuple[str, ...]) -> tuple[Mapping[int, float], Mapping[int, str | None]]:
    """({player: live healthy chart}, {player: team}) at `week` for every player at `positions`: what `_base` of
    that week has as each one's value before any freeze (a position's ranks read only its own players), without
    building the other positions, the news or that week's own freezes. `data` is as of `week`."""
    regular, curve, board = chart_view(data, league)
    priors = _priors(regular)
    teams = {pid: data.team_at(pid, week) for pid, p in data.players.items() if p.position in positions}
    rows = [_row(data, data.players[pid], priors[pid], week, team, 0) for pid, team in teams.items()]
    two_qb = replay_slots.two_qb(league.starters)
    live: dict[int, float] = {}
    replay_chart.base_charts(curve, rows, priors, week, end_week, drafted, regular if two_qb else None,
                             dict.fromkeys(teams, 0.0), live, board=board)
    return MappingProxyType(live), MappingProxyType(teams)


replay_pool.EVICT_HOOKS.append(_live.cache_clear)          # keyed on SeasonData: a dropped season must not stay alive
