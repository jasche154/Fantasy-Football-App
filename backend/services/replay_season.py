"""Replay League: the season. Lineups, playing a week with the real points, playoffs and final standings.

- Lineups: a CPU always starts its best lineup by this week's pre-game projection (so a player on a bye or
  out sits whenever anyone healthy can take the slot). The user does too unless `set_lineup` stored a manual
  one, which may leave a slot empty (it scores 0). A manual lineup is checked against the CURRENT roster
  when it is read: a starter who has left the roster is replaced by the best bench player for that slot.
- `play_week` is the only place in the engine that reads real points for the week being played, and it reads
  nothing else from the full season: every choice comes from the `Knowledge` it is given. It never makes a
  roster move; the orchestrator runs waivers and trades around it.
- Playoffs are the two weeks after the regular season. Seeds are the regular-season standings. Seeds 1-4 play
  the championship bracket (1 v 4, 2 v 3, then the final and a third-place game); every further group of four
  seeds plays a consolation ladder the same way and a leftover pair plays each other twice, so every team has
  a scored game every week. A tied playoff game goes to the higher seed.
- Final places, once the last week is played, come from those games: each group's winners' game decides its
  top two places and the losers' game the next two; a leftover pair places by wins, then points, then seed.
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace

from services import replay_state as rs, replay_values as rv
from services.replay_pool import SeasonData
from services.replay_state import Matchups, Settings, State, WeekResult

BRACKET_SIZE = 4                          # seeds per bracket: two playoff weeks = semi-finals, then placing games
LINEUP_PHASES = ("waivers", "open")       # when the user may set a lineup
PLAY_PHASE = "open"
Lineup = dict[str, tuple[int, ...]]       # slot -> starters


# ---------------- lineups ----------------

def _slots(settings: Settings) -> list[str]:
    return [slot for slot in rs.SLOTS if settings.starters.get(slot, 0) > 0]


def _eligible(slot: str, position: str) -> bool:
    return position in rv.FLEX_POSITIONS if slot == "FLEX" else slot == position


def _check_starter(know: rv.Knowledge, team: rs.Team, slot: str, player_id: int, seen: set[int]) -> None:
    value = know.by_id.get(player_id)
    if value is not None and player_id in team.ir:
        raise ValueError(f"{value.name} is on IR: activate him before starting him.")
    if value is None or player_id not in team.roster:
        raise ValueError(f"{value.name if value else f'Player {player_id}'} is not on your roster.")
    if player_id in seen:
        raise ValueError(f"{value.name} is in the lineup twice.")
    if not _eligible(slot, value.position):
        raise ValueError(f"{value.name} is a {value.position} and can't play in the {slot} slot.")


def set_lineup(state: State, settings: Settings, know: rv.Knowledge,
               starters: Mapping[str, Sequence[int]]) -> State:
    """The state with the user's manual starters for the coming week ({slot: player ids}).

    A slot may be left short or empty (it scores 0) but never over-filled; every player must be on the
    user's roster, fit his slot and appear once. `play_week` clears it for the next week."""
    if state.phase not in LINEUP_PHASES:
        raise ValueError("A lineup can only be set during the season.")
    lineup = {slot: tuple(int(pid) for pid in starters.get(slot, ())) for slot in _slots(settings)}
    unknown = sorted(slot for slot, ids in starters.items() if slot not in lineup and ids)
    if unknown:
        raise ValueError(f"This league has no {unknown[0]} slot.")
    user, seen = rs.user_team(state), set()
    for slot, ids in lineup.items():
        if len(ids) > settings.starters[slot]:
            raise ValueError(f"The {slot} slot takes {settings.starters[slot]}, not {len(ids)} players.")
        for player_id in ids:
            _check_starter(know, user, slot, player_id, seen)
            seen.add(player_id)
    return replace(state, lineup=lineup)


def clear_lineup(state: State) -> State:
    """Back to the automatic best lineup for the user."""
    return replace(state, lineup=None)


def _repaired(lineup: Mapping[str, Sequence[int]], roster: Sequence[int], settings: Settings,
              know: rv.Knowledge) -> Lineup:
    """The manual lineup against today's roster: starters who left are replaced by the best bench player
    for the slot (this week's projection, then rest-of-season rate). Slots left empty on purpose stay empty."""
    kept = {slot: tuple(pid for pid in lineup.get(slot, ()) if pid in roster) for slot in _slots(settings)}
    used = {pid for ids in kept.values() for pid in ids}
    bench = sorted((know.by_id[pid] for pid in roster if pid not in used),
                   key=lambda v: (-v.projection, -v.ros_ppg, v.player_id))
    fixed: Lineup = {}
    for slot, ids in kept.items():
        gone = len(lineup.get(slot, ())) - len(ids)
        fill = [v.player_id for v in bench if v.player_id not in used and _eligible(slot, v.position)][:gone]
        used.update(fill)
        fixed[slot] = (*ids, *fill)
    return fixed


def starters_for(state: State, settings: Settings, know: rv.Knowledge, team_id: int) -> Lineup:
    """{slot: player ids} that team starts this week; every slot of the league is a key (maybe empty).

    The user's manual lineup if one is set (repaired against the current roster), otherwise the best
    lineup by this week's pre-game projection."""
    team = rs.team(state, team_id)
    if team.is_user and state.lineup is not None:
        return _repaired(state.lineup, team.roster, settings, know)
    best = rv.best_lineup(know, team.roster, settings)
    return {slot: tuple(best.get(slot, ())) for slot in _slots(settings)}


# ---------------- matchups and playoffs ----------------

def is_playoff_week(settings: Settings, week: int) -> bool:
    return settings.regular_season_weeks < week <= settings.end_week


def playoff_seeds(state: State) -> tuple[int, ...]:
    """Every team id, best seed first (regular-season standings; provisional until that season is over).
    The first BRACKET_SIZE play for the title."""
    return tuple(row["team_id"] for row in rs.standings(state))


def _result(state: State, week: int) -> WeekResult | None:
    return next((r for r in state.results if r.week == week), None)


def _decide(pair: Sequence[int], scores: Mapping[int, float], seeds: Sequence[int]) -> tuple[int, int]:
    """(winner, loser) of a game; a tie goes to the higher seed."""
    high, low = sorted(pair, key=seeds.index)
    return (high, low) if scores[high] >= scores[low] else (low, high)


def _first_round(group: Sequence[int]) -> list[tuple[int, int]]:
    """A group of seeds, best first: 1 v 4 and 2 v 3. A leftover pair just plays each other."""
    return [(group[i], group[-1 - i]) for i in range(len(group) // 2)]


def _second_round(group: Sequence[int], first: WeekResult, seeds: Sequence[int]) -> list[tuple[int, int]]:
    """Winners' game, then losers' game (higher seed listed first). A leftover pair plays again."""
    games = _first_round(group)
    if len(games) < 2:
        return games
    decided = [_decide(game, first.scores, seeds) for game in games]
    return [tuple(sorted(side, key=seeds.index)) for side in zip(*decided)]


def _playoff_matchups(state: State, settings: Settings, week: int) -> Matchups:
    first_week = settings.regular_season_weeks + 1
    opening = _result(state, first_week)
    if _result(state, settings.regular_season_weeks) is None or (week > first_week and opening is None):
        return ()                                             # not decided yet
    seeds = playoff_seeds(state)
    groups = [seeds[i:i + BRACKET_SIZE] for i in range(0, len(seeds), BRACKET_SIZE)]
    if week == first_week:
        return tuple(game for group in groups for game in _first_round(group))
    return tuple(game for group in groups for game in _second_round(group, opening, seeds))


def matchups_for_week(state: State, settings: Settings, week: int) -> Matchups:
    """((home, away), ...) for a week: as played if it has been, else the schedule or the playoff games.

    In playoff weeks the title bracket's games come first (the higher seed is home), then each consolation
    group, then a leftover pair. Empty while a playoff week's teams are not known yet."""
    if not 1 <= week <= settings.end_week:
        raise ValueError(f"This league plays weeks 1 to {settings.end_week}, not week {week}.")
    played = _result(state, week)
    if played is not None:
        return played.matchups
    if is_playoff_week(settings, week):
        return _playoff_matchups(state, settings, week)
    if week > len(state.schedule):
        raise ValueError(f"This league has no schedule for week {week}.")
    return state.schedule[week - 1]


def matchup_for(state: State, settings: Settings, team_id: int, week: int | None = None) -> tuple[int, int] | None:
    """That team's (home, away) game in a week (default: the current one); None while it is not known."""
    games = matchups_for_week(state, settings, state.week if week is None else week)
    return next((game for game in games if team_id in game), None)


def _pair_order(pair: Sequence[int], games: Sequence[WeekResult], seeds: Sequence[int]) -> list[int]:
    """A leftover pair that played twice: won both, else more points over the two, else the higher seed."""
    winners = {_decide(pair, game.scores, seeds)[0] for game in games}
    points = {team_id: round(sum(game.scores[team_id] for game in games), 2) for team_id in pair}
    return sorted(pair, key=lambda t: (len(winners) > 1 or t not in winners, -points[t], seeds.index(t)))


def final_standings(state: State, settings: Settings) -> list[dict]:
    """The regular-season table rows with a "place". Once the final week is played, every group of four seeds
    places by its second-week games (winners' game: 4k+1 and 4k+2, losers' game: 4k+3 and 4k+4) and a
    leftover pair by `_pair_order`. Before that: regular-season order throughout."""
    rows = {row["team_id"]: row for row in rs.standings(state)}
    seeds = tuple(rows)
    opening, final = _result(state, settings.regular_season_weeks + 1), _result(state, settings.end_week)
    order = list(seeds)
    if final is not None and opening is not None:
        groups = [seeds[i:i + BRACKET_SIZE] for i in range(0, len(seeds), BRACKET_SIZE)]
        order = [team_id for group in groups for team_id in (
            _pair_order(group, (opening, final), seeds) if len(group) == 2 else
            [t for game in _second_round(group, opening, seeds) for t in _decide(game, final.scores, seeds)])]
    return [{**rows[team_id], "place": place} for place, team_id in enumerate(order, start=1)]


# ---------------- playing a week ----------------

def _check_playable(state: State, settings: Settings, data: SeasonData, know: rv.Knowledge) -> None:
    week = state.week
    if state.phase != PLAY_PHASE:
        raise ValueError(f"Week {week} can't be played while the league is in its '{state.phase}' phase.")
    if know.week != week:
        raise ValueError(f"Week {week} can't be played with what was known before week {know.week}.")
    if week > min(settings.end_week, data.last_week):
        raise ValueError(f"The {data.season} season has no week {week} to play.")
    if data.known_before is not None and data.known_before <= week:
        raise ValueError(f"Scoring week {week} needs the full season, not a copy that stops before it.")


def _log_results(state: State, result: WeekResult) -> State:
    """One "result" event per game, on the winner's team (nobody's when a regular-season game is tied)."""
    names, seeds = {t.id: t.name for t in state.teams}, playoff_seeds(state)
    for home, away in result.matchups:
        winner, loser = _decide((home, away), result.scores, seeds)
        tied = result.scores[home] == result.scores[away]
        score = f"{result.scores[winner]:.2f}-{result.scores[loser]:.2f}"
        if tied and not result.playoff:
            state = rs.add_event(state, "result", None, f"{names[home]} and {names[away]} tied {score}")
        else:
            note = " as the higher seed" if tied else ""
            state = rs.add_event(state, "result", winner, f"{names[winner]} beat {names[loser]} {score}{note}")
    return state


def _log_bracket(state: State, settings: Settings) -> State:
    """A "playoffs" event when the week just played decides next week's title-bracket games."""
    upcoming = state.week + 1
    if not is_playoff_week(settings, upcoming):
        return state
    final = upcoming == settings.end_week
    names = {t.id: t.name for t in state.teams}
    games = matchups_for_week(state, settings, upcoming)[:1 if final else BRACKET_SIZE // 2]
    listed = ", ".join(f"{names[home]} v {names[away]}" for home, away in games)
    return rs.add_event(state, "playoffs", None, f"{'Championship game' if final else 'Playoffs'}: {listed}")


def _crown(state: State, result: WeekResult) -> State:
    champion = _decide(result.matchups[0], result.scores, playoff_seeds(state))[0]
    crowned = rs.add_event(state, "champion", champion, f"{rs.team(state, champion).name} won the championship")
    return replace(crowned, champion=champion, phase="complete")


def play_week(state: State, settings: Settings, data: SeasonData, know: rv.Knowledge, seed: int) -> State:
    """Play `state.week` (phase "open" only): every team's starters, scored with the season's real points.

    `data` is the FULL season, read only for this week's points; `know` is the Knowledge for this week and
    makes every choice. Appends the WeekResult and its events, expires pending offers, then either crowns
    the champion (phase "complete") or moves to the next week with the lineup cleared: phase "waivers" in
    wednesday and faab mode, still "open" with free agency only. `seed` is unused: nothing here is random."""
    _check_playable(state, settings, data, know)
    week = state.week
    matchups = matchups_for_week(state, settings, week)
    if not matchups:
        raise ValueError(f"Week {week} has no games yet: an earlier week has not been played.")
    starters = {team.id: starters_for(state, settings, know, team.id) for team in state.teams}
    points = {pid: float(data.points(pid, week))
              for lineup in starters.values() for ids in lineup.values() for pid in ids}
    scores = {team_id: round(sum(points[pid] for ids in lineup.values() for pid in ids), 2)
              for team_id, lineup in starters.items()}
    result = WeekResult(week, matchups, scores, starters, points, is_playoff_week(settings, week))
    offers = tuple(replace(o, status="expired") if o.status == "pending" else o for o in state.offers)
    played = _log_results(replace(state, results=(*state.results, result), offers=offers), result)
    if week == settings.end_week:
        return _crown(played, result)
    phase = "waivers" if settings.waiver_mode != "none" else PLAY_PHASE
    return replace(_log_bracket(played, settings), week=week + 1, lineup=None, phase=phase)
