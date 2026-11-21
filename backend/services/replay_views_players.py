"""Replay League: the waiver wire and a player's page (re-exported by services/replay_views_league.py). The
shared shapes (PlayerRow, Usage, DefenseRating ...) are documented at the top of services/replay_views.py; the
same no-hindsight rule holds (`Game.data` and `Game.know_at` only).

GET  .../players?position=&search=&status=&sort=&dir=&stats=&stat_week=&limit=&offset=
      Page of WireRow + {week, stats, stat_week}
      WireRow = PlayerRow + {status: rostered | waivers | free_agent, owner: TeamRef | null, season_points,
                last_points, usage: Usage | null, line: StatLine}
                season_points / last_points: real points in weeks already played
      usage         his last 3 games BEFORE the current week (games 1-3); null for K, DST or before his first game
      position: QB | RB | WR | TE | K | DST | FLEX. status: available (default) | waivers | free_agent |
      rostered | all. sort: value (default) | projection | ros_ppg | adp | points | name | ppg | last |
      line_points | line_projection | a BoxLine key. dir: asc | desc (default asc for name and adp, desc
      otherwise); players without the number come last either way. Sorting never changes `total`.
      stats: season (default) | last4 | week; stat_week: 1..current (default current, only with "week"; the
      response's stat_week is null otherwise). 422 on an unknown sort / dir / stats or a stat_week out of range.
  BoxLine   {pass_yds, pass_td, pass_int, carries, rush_yds, rush_td, targets, rec, rec_yds, rec_td}
  StatLine  {span: season | last4 | week, week: number | null, games, points, projection, box: BoxLine | null}
      season        games and points before now, box totals; projection null
      last4         his last 4 games played: games (<= 4), points and box per-game averages (1 decimal); null
                    points before his first game; projection null
      week          stat_week: points and box only once played and he played (games 1, else 0); projection =
                    that week's pre-game number (0 when out or on a bye), null when he wasn't in the pool yet
      box           null for K/DST and when he has no game in the span
GET  .../players/{player_id}      {player: WireRow, weeks: [{week, played, team, opponent, away, bye, out, projection,
                                   points, ros_ppg, value, rank, pos_rank, expert_rank, news, defense,
                                   injury: Injury | null, usage: Usage | null, box: BoxLine | null}],
                                   upcoming: [{week, opponent, away, bye, estimate, defense, out_expected}],
                                   defense_basis: {weeks, prior}}
      injury: that week's tag (weeks 1..current). usage / box: that week's own game once played, else null.
      weeks from the first one he was in the pool (a late arrival has no rows before his first projection
      or game) to the current one. points: null when the week is unplayed or he did not play. away: his team
      played that week's game on the road (false on a bye).
      upcoming      weeks current+1..end_week against his current team's schedule; defense as rated now;
                    estimate = ros_ppg x the defense factor (1 decimal; factor 1 without a rating), null on a
                    bye or when out_expected (the current injury news has him out that week or for the season)
      defense_basis what the ratings rest on now: played weeks this season, and whether last season is blended
"""
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from statistics import mean

from services import replay_box, replay_defense, replay_state as rs, replay_waivers
from services.replay_pool import SKILL, Usage
from services.replay_state import Team
from services.replay_values import ReplayValue
from services.replay_views import DEFAULT_PAGE, Game, page, player_row, positions_for, team_ref

AVAILABLE = "available"
STATUSES = (AVAILABLE, *replay_waivers.STATUS_FILTERS)
SORTS = ("value", "projection", "ros_ppg", "adp", "points", "name", "ppg", "last", "line_points",
         "line_projection", *replay_box.KEYS)
ASCENDING = ("name", "adp")             # the sorts whose default direction is asc
DIRECTIONS = ("asc", "desc")
SPANS = ("season", "last4", "week")
USAGE_GAMES = 3                         # games averaged in a WireRow's usage
LINE_GAMES = 4                          # games averaged in a "last4" StatLine


def _owners(game: Game) -> dict[int, Team]:
    return {pid: owner for owner in game.state.teams for pid in rs.held(owner)}


def _season_points(game: Game) -> dict[int, float]:
    """{player id: real points so far}: `Game.data` holds played weeks only."""
    totals: dict[int, float] = defaultdict(float)
    for (player_id, _), points in game.data.actual.items():
        totals[player_id] += points
    return totals


def _usage(games: Sequence[Usage]) -> dict | None:
    """The Usage shape: means over `games`; snap % and target share over the games that have them."""
    if not games:
        return None
    snaps = [g.snap_pct for g in games if g.snap_pct is not None]
    shares = [g.target_share for g in games if g.target_share is not None]
    return {"games": len(games), "snap_pct": round(mean(snaps), 1) if snaps else None,
            "targets": round(mean(g.targets for g in games), 1),
            "target_share": round(mean(shares), 3) if shares else None,
            "carries": round(mean(g.carries for g in games), 1)}


def _recent_usage(game: Game) -> dict[int, dict]:
    """{player id: Usage of his last USAGE_GAMES games}: `Game.data` holds played weeks only (skill games)."""
    games: dict[int, list[Usage]] = defaultdict(list)
    for (player_id, _), usage in sorted(game.data.usage.items(), key=lambda item: -item[0][1]):
        if len(games[player_id]) < USAGE_GAMES:
            games[player_id].append(usage)
    return {player_id: _usage(recent) for player_id, recent in games.items()}


def _played_weeks(game: Game) -> dict[int, list[int]]:
    """{player id: weeks he played, ascending}: `Game.data` holds played weeks only."""
    weeks: dict[int, list[int]] = defaultdict(list)
    for player_id, week in sorted(game.data.actual):
        weeks[player_id].append(week)
    return weeks


def _box(box: replay_box.BoxLine | None) -> dict | None:
    return asdict(box) if box is not None else None


def stat_line(game: Game, value: ReplayValue, weeks: Sequence[int], stats: str = "season",
              stat_week: int | None = None) -> dict:
    """The StatLine of one player; `weeks` are the weeks he played (from `_played_weeks`)."""
    data, pid = game.data, value.player_id
    if stats == "week":
        then = game.know_at(stat_week).by_id.get(pid)
        return {"span": stats, "week": stat_week, "games": int((pid, stat_week) in data.actual),
                "points": data.actual.get((pid, stat_week)), "box": _box(data.box.get((pid, stat_week))),
                "projection": round(then.projection, 2) if then else None}
    span = weeks[-LINE_GAMES:] if stats == "last4" else weeks
    points = [data.actual[(pid, week)] for week in span]
    boxes = [data.box[(pid, week)] for week in span if (pid, week) in data.box]
    if stats == "last4":
        shown, box = (round(mean(points), 2) if points else None), replay_box.mean(boxes)
    else:
        shown, box = round(sum(points), 2), replay_box.total(boxes) if boxes else None
    return {"span": stats, "week": None, "games": len(span), "points": shown, "projection": None,
            "box": _box(box if value.position in SKILL else None)}


def _wire_row(game: Game, player_id: int, owners: dict[int, Team], totals: dict[int, float],
              usage: dict[int, dict], line: dict) -> dict:
    owner = owners.get(player_id)
    return {**player_row(game, game.know, player_id),
            "status": replay_waivers.player_status(game.state, player_id),
            "owner": team_ref(game, owner) if owner else None, "season_points": round(totals.get(player_id, 0.0), 2),
            "last_points": game.data.actual.get((player_id, game.played)), "usage": usage.get(player_id),
            "line": line}


def _sort_key(game: Game, sort: str, totals: dict[int, float], lines: dict[int, dict], order: dict[int, int]):
    def key(v: ReplayValue):
        line = lines[v.player_id]
        if sort in replay_box.KEYS:
            return line["box"][sort] if line["box"] else None
        return {"value": lambda: -order[v.player_id], "projection": lambda: v.projection,
                "ros_ppg": lambda: v.ros_ppg, "adp": lambda: v.adp, "points": lambda: totals.get(v.player_id, 0.0),
                "name": lambda: v.name.lower(), "ppg": lambda: v.ppg,
                "last": lambda: game.data.actual.get((v.player_id, game.played)),
                "line_points": lambda: line["points"], "line_projection": lambda: line["projection"]}[sort]()
    return key


def _sorted(game: Game, values: list[ReplayValue], sort: str, direction: str | None, totals: dict[int, float],
            lines: dict[int, dict]) -> list[ReplayValue]:
    """`values` arrive best value first, so every sort breaks its ties that way; unknowns always last."""
    if sort not in SORTS:
        raise ValueError(f"Unknown sort '{sort}'. Valid: {list(SORTS)}")
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError(f"Unknown dir '{direction}'. Valid: {list(DIRECTIONS)}")
    descending = (direction or ("asc" if sort in ASCENDING else "desc")) == "desc"
    key = _sort_key(game, sort, totals, lines, {v.player_id: n for n, v in enumerate(values)})
    keyed = [(key(v), v) for v in values]
    known = sorted((pair for pair in keyed if pair[0] is not None), key=lambda pair: pair[0], reverse=descending)
    return [v for _, v in known] + [v for k, v in keyed if k is None]


def _span(game: Game, stats: str | None, stat_week: int | None) -> tuple[str, int | None]:
    """(stats, stat_week) checked: stat_week defaults to the current week and is kept only for "week"."""
    stats = stats or "season"
    if stats not in SPANS:
        raise ValueError(f"Unknown stats '{stats}'. Valid: {list(SPANS)}")
    current = game.state.week
    if stat_week is not None and not 1 <= stat_week <= current:
        raise ValueError(f"Week {stat_week} can't be shown: pick a week from 1 to {current}.")
    if stats != "week":
        return stats, None
    if current < 1:
        raise ValueError("There are no weeks to show before the season starts.")
    return stats, stat_week or current


def players(game: Game, position: str | None = None, search: str | None = None, status: str | None = None,
            sort: str = "value", limit: int = DEFAULT_PAGE, offset: int = 0, direction: str | None = None,
            stats: str | None = None, stat_week: int | None = None) -> dict:
    """The waiver wire: the pool filtered by position (FLEX = RB/WR/TE), name and status, sorted, one page of
    it. The default status "available" is everyone not on a roster."""
    status = status or AVAILABLE
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'. Valid: {list(STATUSES)}")
    stats, stat_week = _span(game, stats, stat_week)
    wanted, totals, owners, usage = positions_for(position), _season_points(game), _owners(game), _recent_usage(game)
    found = replay_waivers.available_players(game.state, game.know, None, search,
                                             None if status == AVAILABLE else status)
    values, played = [value for value in found if value.position in wanted], _played_weeks(game)
    lines = {v.player_id: stat_line(game, v, played.get(v.player_id, ()), stats, stat_week) for v in values}
    ordered = _sorted(game, values, sort, direction, totals, lines)
    rows = page(ordered, limit, offset, lambda v: _wire_row(game, v.player_id, owners, totals, usage,
                                                            lines[v.player_id]))
    return {**rows, "week": game.state.week, "stats": stats, "stat_week": stat_week}


def _away(game: Game, team: str | None, week: int) -> bool:
    found = game.data.schedule.game(team, week) if team else None
    return found is not None and found.away_team == team


def _history_row(game: Game, player_id: int, week: int) -> dict:
    row, usage = player_row(game, game.know_at(week), player_id), game.data.usage.get((player_id, week))
    return {"week": week, "played": week <= game.played, "team": row["player"]["team"],
            "injury": row["player"]["injury"], "away": _away(game, row["player"]["team"], week),
            **{key: row[key] for key in ("opponent", "bye", "out", "projection", "points", "ros_ppg", "value",
                                         "rank", "pos_rank", "expert_rank", "news", "defense")},
            "usage": _usage([usage] if usage else []),           # played weeks only: `Game.data`
            "box": _box(game.data.box.get((player_id, week)))}


def _upcoming(game: Game, value: ReplayValue) -> list[dict]:
    """Weeks after the current one against his current team's schedule, rated as known now."""
    ratings = replay_defense.ratings(game.full_season.as_of(game.know.week))
    schedule, report = game.know.schedule, value.news
    rows = []
    for week in range(game.state.week + 1, game.settings.end_week + 1):
        opponent = schedule.opponent(value.team, week)
        bye = value.team is not None and opponent is None
        out = report is not None and (report.back is None or report.back > week)
        defense = ratings.rating(opponent, value.position)
        estimate = None if bye or out else round(value.ros_ppg * (defense["factor"] if defense else 1.0), 1)
        rows.append({"week": week, "opponent": "BYE" if bye else opponent, "away": _away(game, value.team, week),
                     "bye": bye, "estimate": estimate, "defense": defense, "out_expected": out})
    return rows


def player(game: Game, player_id: int) -> dict:
    """One player: where he stands now, his weeks so far (projection against result, value by week) from the
    week he entered the pool (a late arrival has no earlier weeks), and an estimate for the weeks after."""
    if player_id not in game.know.by_id:
        raise rs.ReplayNotFound(f"There is no player {player_id} in the {game.settings.season} player pool.")
    value = game.know.by_id[player_id]
    line = stat_line(game, value, _played_weeks(game).get(player_id, ()))
    return {"player": _wire_row(game, player_id, _owners(game), _season_points(game), _recent_usage(game), line),
            "weeks": [_history_row(game, player_id, week) for week in range(1, game.state.week + 1)
                      if player_id in game.know_at(week).by_id],
            "upcoming": _upcoming(game, value),
            "defense_basis": replay_defense.basis(game.full_season.as_of(game.know.week))}
