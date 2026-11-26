"""Replay League: the season. Lineups, playing a week with the real points, playoffs and final standings.

- Lineups: a CPU always starts its best lineup by this week's pre-game projection (so a player on a bye or
  out sits whenever anyone healthy can take the slot). The user does too unless `set_lineup` stored a manual
  one, which may leave a slot empty (it scores 0). A manual lineup is checked against the CURRENT roster
  when it is read: a starter who has left the roster is replaced by the best bench player for that slot.
- `play_week` is the only place in the engine that reads real points for the week being played, and it reads
  nothing else from the full season: every choice comes from the `Knowledge` it is given. It never makes a
  roster move; the orchestrator runs waivers and trades around it.
- Playoffs are the `rs.playoff_rounds` weeks after the regular season: the title bracket (byes in round one,
  reseeded every round, a third-place game), the round-one losers' pool and the consolation pool, all built
  in `replay_bracket` (re-exported here) along with the final places.
- Weeks are NFL weeks: a league that jumped in at `settings.start_week` K plays (and schedules) weeks K on,
  `state.schedule[0]` being week K; results, standings and the bracket only ever hold its own weeks.
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace

from services import replay_state as rs, replay_values as rv
from services.replay_bracket import (  # noqa: F401 - re-exported: import the playoffs from this module
    _decide, _result, byes, final_standings, game_label, is_playoff_week, playoff_matchups, playoff_seeds,
    title_games,
)
from services.replay_pool import SeasonData
from services.replay_state import Matchups, Settings, State, WeekResult

LINEUP_PHASES = ("waivers", "open")       # when the user may set a lineup
PLAY_PHASE = "open"
Lineup = dict[str, tuple[int, ...]]       # slot -> starters


# ---------------- lineups ----------------

def _slots(settings: Settings) -> list[str]:
    return [slot for slot in rs.SLOTS if settings.starters.get(slot, 0) > 0]


def _eligible(slot: str, position: str) -> bool:
    return position in rv.eligible(slot)


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
    return {slot: tuple(pid for pid in best.get(slot, ()) if slot not in ("K", "DST") or know.by_id[pid].projection > 0)
            for slot in _slots(settings)}      # a lone K or DST on a bye sits: the slot stays empty, and the screen says so


# ---------------- matchups ----------------

def matchups_for_week(state: State, settings: Settings, week: int) -> Matchups:
    """((home, away), ...) for a week: as played if it has been, else the schedule or the playoff games.

    In playoff weeks the title bracket's games come first (higher seed home; the final week's first game is
    the final), then the losers' pool, then the consolation pool. Empty while a playoff week's teams are not
    known yet. A team with no game that week (a bye, an odd one out in a pool) is in none of them. A league
    that jumped in at start_week has no week before it (ValueError)."""
    first = settings.start_week
    if not first <= week <= settings.end_week:
        raise ValueError(f"This league plays weeks {first} to {settings.end_week}, not week {week}.")
    played = _result(state, week)
    if played is not None:
        return played.matchups
    if is_playoff_week(settings, week):
        return playoff_matchups(state, settings, week)
    if week - first >= len(state.schedule):
        raise ValueError(f"This league has no schedule for week {week}.")
    return state.schedule[week - first]


def matchup_for(state: State, settings: Settings, team_id: int, week: int | None = None) -> tuple[int, int] | None:
    """That team's (home, away) game in a week (default: the current one); None while it is not known."""
    games = matchups_for_week(state, settings, state.week if week is None else week)
    return next((game for game in games if team_id in game), None)


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
    names = {t.id: t.name for t in state.teams}
    games = title_games(state, settings, upcoming)
    if upcoming == settings.end_week:
        games, title = games[:1], "Championship game"
    elif len(games) == 1:                                 # 5 playoff teams: one round-one game, three byes
        title = "Wild-card game"
    else:
        title = f"{game_label(state, settings, upcoming, games[0])}s"
    listed = ", ".join(f"{names[home]} v {names[away]}" for home, away in games)
    resting = byes(state, settings, upcoming)
    rest = f" ({'bye' if len(resting) == 1 else 'byes'}: {', '.join(names[t] for t in resting)})" if resting else ""
    return rs.add_event(state, "playoffs", None, f"{title}: {listed}{rest}")


def _crown(state: State, result: WeekResult) -> State:
    champion = _decide(result.matchups[0], result.scores, playoff_seeds(state))[0]
    crowned = rs.add_event(state, "champion", champion, f"{rs.team(state, champion).name} won the championship")
    return replace(crowned, champion=champion, phase="complete")


def play_week(state: State, settings: Settings, data: SeasonData, know: rv.Knowledge, seed: int) -> State:
    """Play `state.week` (phase "open" only): every team's starters, scored with the season's real points.

    `data` is the FULL season, read only for this week's points; `know` is the Knowledge for this week and
    makes every choice. Appends the WeekResult (every team's bench too) and its events, expires pending offers,
    then either crowns the champion (phase "complete") or moves to the next week with the lineup cleared: phase
    "waivers" in wednesday and faab mode, still "open" with free agency only. `seed` is unused: nothing here is random."""
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
    started = {pid for lineup in starters.values() for ids in lineup.values() for pid in ids}
    bench = {team.id: tuple(pid for pid in team.roster if pid not in started) for team in state.teams}
    result = WeekResult(week, matchups, scores, starters, points, is_playoff_week(settings, week), bench)
    offers = tuple(replace(o, status="expired") if o.status == "pending" else o for o in state.offers)
    played = _log_results(replace(state, results=(*state.results, result), offers=offers), result)
    if week == settings.end_week:
        return _crown(played, result)
    phase = "waivers" if settings.waiver_mode != "none" else PLAY_PHASE
    return replace(_log_bracket(played, settings), week=week + 1, lineup=None, phase=phase)
