"""Replay League: the playoffs. Pure functions of the settings and the played results (re-exported by
replay_season, which is the module to import).

- Seeds are the regular-season standings. The top `playoff_teams` (P) play the title bracket over
  `rs.playoff_rounds(P)` weeks (R): 2 ** R - P top seeds get a bye in round one only, and every round is
  reseeded (the best seed left plays the lowest). The semi-final losers play for third place in the final week.
- With three rounds, the round-one losers play on for places 5..P and the teams outside the playoffs for
  P+1..N, each group as a pool: every week the pool is ranked by wins in its games, then points in its games,
  then seed, and paired off 1v2, 3v4 (an odd one out is idle). A pool of two simply plays every week.
- A tied playoff game goes to the higher seed.
- Final places, once the last week is played: the final, the third-place game, the losers' pool table,
  then the consolation pool table. Before that: seed order.
"""
from collections.abc import Sequence

from services import replay_state as rs
from services.replay_state import Matchups, Settings, State, WeekResult


def is_playoff_week(settings: Settings, week: int) -> bool:
    return settings.regular_season_weeks < week <= settings.end_week


def playoff_seeds(state: State) -> tuple[int, ...]:
    """Every team id, best seed first (regular-season standings; provisional until that season is over)."""
    return tuple(row["team_id"] for row in rs.standings(state))


def _result(state: State, week: int) -> WeekResult | None:
    return next((r for r in state.results if r.week == week), None)


def _decide(pair: Sequence[int], scores, seeds: Sequence[int]) -> tuple[int, int]:
    """(winner, loser) of a game; a tie goes to the higher seed."""
    high, low = sorted(pair, key=seeds.index)
    return (high, low) if scores[high] >= scores[low] else (low, high)


def _bracket_round(alive: Sequence[int]) -> Matchups:
    """Reseeded pairs of the teams left (best seed first): best v lowest, and so on inwards."""
    return tuple((alive[i], alive[-1 - i]) for i in range(len(alive) // 2))


def _title_rounds(state: State, settings: Settings) -> list[Matchups]:
    """The title bracket's games round by round, as far as the played weeks decide them."""
    if _result(state, settings.regular_season_weeks) is None:
        return []
    seeds, p = playoff_seeds(state), settings.playoff_teams
    rounds, bye = rs.playoff_rounds(p), 2 ** rs.playoff_rounds(p) - p
    games, survivors = _bracket_round(seeds[bye:p]), seeds[:bye]
    played = [games]
    for index in range(1, rounds):
        result = _result(state, settings.regular_season_weeks + index)
        if result is None:
            break
        decided = [_decide(game, result.scores, seeds) for game in games]
        alive = sorted((*survivors, *(w for w, _ in decided)), key=seeds.index)
        if index == rounds - 1:                                    # the final, then the third-place game
            games = (tuple(alive), tuple(sorted((loser for _, loser in decided), key=seeds.index)))
        else:
            games = _bracket_round(alive)
        survivors = ()
        played.append(games)
    return played


def _round_index(settings: Settings, week: int) -> int:
    return week - settings.regular_season_weeks - 1


def title_games(state: State, settings: Settings, week: int) -> Matchups:
    """The title bracket's games in a playoff week, higher seed first; () while unknown. In the final week:
    (final, third place)."""
    if not is_playoff_week(settings, week):
        return ()
    rounds = _title_rounds(state, settings)
    index = _round_index(settings, week)
    return rounds[index] if index < len(rounds) else ()


def byes(state: State, settings: Settings, week: int) -> tuple[int, ...]:
    """Seeds with a first-round bye this week (round one only: 2 ** rounds - playoff_teams top seeds)."""
    if week != settings.regular_season_weeks + 1 or _result(state, settings.regular_season_weeks) is None:
        return ()
    return playoff_seeds(state)[:2 ** rs.playoff_rounds(settings.playoff_teams) - settings.playoff_teams]


def game_label(state: State, settings: Settings, week: int, pair: Sequence[int]) -> str | None:
    """None (regular season) | "Quarter-final" | "Semi-final" | "Championship" | "Third place" | "Consolation".
    A 2-round bracket's first round is "Semi-final"."""
    if not is_playoff_week(settings, week):
        return None
    titles = [set(game) for game in title_games(state, settings, week)]
    if set(pair) not in titles:
        return "Consolation"
    if week == settings.end_week:
        return ("Championship", "Third place")[titles.index(set(pair))]
    return "Semi-final" if settings.end_week - week == 1 else "Quarter-final"


# ---------------- the pools ----------------

def _pools(state: State, settings: Settings) -> list[tuple[tuple[int, ...], int]]:
    """[(teams, first week)]: the round-one losers (three-round brackets, once round one is played), then
    the teams outside the playoffs."""
    seeds, first = playoff_seeds(state), settings.regular_season_weeks + 1
    pools = []
    if rs.playoff_rounds(settings.playoff_teams) == 3 and _result(state, first) is not None:
        opening = _result(state, first)
        losers = sorted((_decide(g, opening.scores, seeds)[1] for g in _title_rounds(state, settings)[0]),
                        key=seeds.index)
        pools.append((tuple(losers), first + 1))
    pools.append((seeds[settings.playoff_teams:], first))
    return pools


def _pool_table(state: State, members: Sequence[int], weeks: range, seeds: Sequence[int]) -> list[int]:
    """The pool ranked by wins in its games, then points over its weeks (an idle week's score counts), then seed."""
    inside, wins = set(members), dict.fromkeys(members, 0)
    results = [_result(state, week) for week in weeks]
    for result in results:
        for game in result.matchups:
            if set(game) <= inside:
                wins[_decide(game, result.scores, seeds)[0]] += 1
    points = {t: round(sum(result.scores[t] for result in results), 2) for t in members}
    return sorted(members, key=lambda t: (-wins[t], -points[t], seeds.index(t)))


def _pool_games(state: State, members: Sequence[int], start: int, week: int, seeds: Sequence[int]) -> Matchups:
    table = _pool_table(state, members, range(start, week), seeds)
    return tuple((table[i], table[i + 1]) for i in range(0, len(table) - 1, 2))


def playoff_matchups(state: State, settings: Settings, week: int) -> Matchups:
    """An unplayed playoff week's games: title bracket, losers' pool, consolation pool; () while unknown."""
    titles = title_games(state, settings, week)
    if not titles:
        return ()
    seeds = playoff_seeds(state)
    pooled = (game for members, start in _pools(state, settings) if start <= week
              for game in _pool_games(state, members, start, week, seeds))
    return (*titles, *pooled)


def final_standings(state: State, settings: Settings) -> list[dict]:
    """The regular-season table rows with a "place". Once the final week is played: the final (1-2) and the
    third-place game (3-4), which are that week's first two games, then the losers' pool table (5..P) and the
    consolation pool table (P+1..N). Before that: seed order."""
    rows = {row["team_id"]: row for row in rs.standings(state)}
    seeds = tuple(rows)
    final = _result(state, settings.end_week)
    order = list(seeds)
    if final is not None:
        order = [t for game in final.matchups[:2] for t in _decide(game, final.scores, seeds)]
        order += [t for members, start in _pools(state, settings)
                  for t in _pool_table(state, members, range(start, settings.end_week + 1), seeds)]
    return [{**rows[team_id], "place": place} for place, team_id in enumerate(order, start=1)]
