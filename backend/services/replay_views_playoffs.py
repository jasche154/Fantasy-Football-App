"""Replay League: the playoff picture as a bracket (re-exported by services/replay_views_league.py).

GET .../playoffs   {weeks: int[], seeds_final, champion: TeamRef | null, title: Round[], pools: Pool[]}
  Round   {week, name: "Quarter-finals" | "Semi-finals" | "Final", projected, byes: Seed[], games: Game[]}
          every round of the title bracket, known or not. games: the round's slots in bracket order; a slot
          whose teams aren't decided yet has home and away null. The final round's games are the
          Championship, then the Third place game. projected: round one drawn from the current seeds while
          the regular season is still going (seeds_final false); byes: the top seeds who skip round one.
  Pool    {name, places: [first, last], first_week, size, teams: Seed[] | null, weeks: [{week, known, games: Game[]}]}
          the consolation pools that decide the places below the title games: the round-one losers (a
          three-round bracket only, places 5..P; teams null until round one is played) and the teams outside
          the playoffs (places P+1..N, by current seed until the seeds are final). known: that week's pairs
          are set (they are drawn week by week from the pool's own results).
  Game    {label, home: Side | null, away: Side | null, winner: team id | null}
  Seed    TeamRef + {seed}.  Side = Seed + {points, projected} (null until played / reached)
Uses only the state's played results and the current seeds, like the standings (no hindsight).
"""
from collections.abc import Sequence

from services import replay_bracket as rb, replay_season, replay_state as rs
from services.replay_views import Game, result_of, side, team_ref, winner

ROUND_NAMES = {2: ("Semi-finals", "Final"), 3: ("Quarter-finals", "Semi-finals", "Final")}
FINAL_LABELS = ("Championship", "Third place")


def _seed(game: Game, seeds: Sequence[int], team_id: int) -> dict:
    return {**team_ref(game, rs.team(game.state, team_id)), "seed": seeds.index(team_id) + 1}


def _side(game: Game, seeds: Sequence[int], team_id: int, week: int, real: bool) -> dict:
    line = side(game, team_id, week) if real else {"projected": None, "points": None}
    return {**_seed(game, seeds, team_id), "projected": line["projected"], "points": line["points"]}


def _game(game: Game, seeds: Sequence[int], pair: Sequence[int] | None, week: int, label: str,
          real: bool = True) -> dict:
    if pair is None:
        return {"label": label, "home": None, "away": None, "winner": None}
    home, away = (_side(game, seeds, team_id, week, real) for team_id in pair)
    return {"label": label, "home": home, "away": away, "winner": winner(game, pair, week) if real else None}


def _round(game: Game, seeds: Sequence[int], index: int, rounds: int) -> dict:
    settings, state = game.settings, game.state
    week, bye = settings.regular_season_weeks + 1 + index, 2 ** rounds - settings.playoff_teams
    pairs, projected = list(rb.title_games(state, settings, week)), False
    if not pairs and index == 0:            # the regular season isn't over: draw round one from today's seeds
        pairs, projected = list(rb.reseeded_pairs(seeds[bye:settings.playoff_teams])), True
    is_final = index == rounds - 1
    slots = 2 if is_final else (settings.playoff_teams - bye) // 2 if index == 0 else 2 ** (rounds - 1 - index)
    name = ROUND_NAMES[rounds][index]
    labels = FINAL_LABELS if is_final else (name[:-1],) * slots
    games = [_game(game, seeds, pairs[i] if i < len(pairs) else None, week, labels[i], real=not projected)
             for i in range(slots)]
    return {"week": week, "name": name, "projected": projected, "games": games,
            "byes": [_seed(game, seeds, team_id) for team_id in seeds[:bye]] if index == 0 else []}


def _pool_weeks(game: Game, seeds: Sequence[int], members: Sequence[int] | None, first_week: int) -> list[dict]:
    settings, state, weeks = game.settings, game.state, []
    for week in range(first_week, settings.end_week + 1):
        known = bool(rb.title_games(state, settings, week))
        pairs = replay_season.matchups_for_week(state, settings, week) if known and members else ()
        inside = set(members or ())
        games = [_game(game, seeds, pair, week, "Consolation") for pair in pairs if set(pair) <= inside]
        weeks.append({"week": week, "known": known and members is not None, "games": games})
    return weeks


def _pools(game: Game, seeds: Sequence[int], rounds: int) -> list[dict]:
    settings, state = game.settings, game.state
    first, p, n = settings.regular_season_weeks + 1, settings.playoff_teams, settings.teams
    pools = []
    if rounds == 3:
        losers = (pool for pool, start in rb.pools(state, settings) if start == first + 1)
        members = next(losers, None)
        bye = 2 ** rounds - p
        pools.append({"name": "Round-one losers", "places": [2 ** (rounds - 1) + 1, p], "first_week": first + 1,
                      "size": (p - bye) // 2,
                      "teams": [_seed(game, seeds, t) for t in members] if members is not None else None,
                      "weeks": _pool_weeks(game, seeds, members, first + 1)})
    if n > p:
        outside = seeds[p:]
        pools.append({"name": "Consolation", "places": [p + 1, n], "first_week": first, "size": n - p,
                      "teams": [_seed(game, seeds, t) for t in outside],
                      "weeks": _pool_weeks(game, seeds, outside, first)})
    return pools


def playoffs(game: Game) -> dict:
    settings, state = game.settings, game.state
    seeds, rounds = rb.playoff_seeds(state), rs.playoff_rounds(settings.playoff_teams)
    title = [_round(game, seeds, index, rounds) for index in range(rounds)]
    championship = title[-1]["games"][0]
    champion = next((team for team in (championship["home"], championship["away"])
                     if team and team["team_id"] == championship["winner"]), None)
    return {"weeks": list(range(settings.regular_season_weeks + 1, settings.end_week + 1)),
            "seeds_final": result_of(state, settings.regular_season_weeks) is not None,
            "champion": {k: champion[k] for k in ("team_id", "name", "is_user", "tendency")} if champion else None,
            "title": title, "pools": _pools(game, seeds, rounds)}
