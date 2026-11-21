"""Replay League: the JSON the frontend gets (mirror it in frontend/src/api/types/replay.ts).

This file holds the shared pieces and the game, draft, team and matchup screens; the league-wide screens
(standings, schedule, players, claims, trades, activity) are in services/replay_views_league.py.

No hindsight: a view reads the season only through `Game.data` (real points of weeks already played) and
`Game.know_at(week)` for weeks up to the current one. A number about a week that has not been played is a
pre-game projection or null, never a result. Paths below are under /api/replay; request bodies are the
pydantic models in api/replay_routes.py (create) and api/replay_action_routes.py.

Shared pieces
  TeamRef    {team_id, name, is_user}
  Injury     {tag: Q | D | O | IR | PUP | SUS, practice: DNP | LP | FP | null, injury: string | null}
  PlayerRef  {id, name, position, team, injury: Injury | null}
      team          his NFL team as known before the week shown, or null
      injury        his tag in the CURRENT week, wherever the ref appears (events, offers, verdicts, claims). Only a
                    PlayerRow built for another week (team / matchup with ?week=) carries that week's tag. Always null
                    in the draft room. O, IR, PUP and SUS mean he does not play; O, IR and PUP may go on IR.
  PlayerRow  {player: PlayerRef, opponent, bye, out, projection, points, ros_ppg, value, trade_value, rank,
              pos_rank, adp, games, ppg, trade_locked}
      opponent      NFL team code, "BYE" or null (unknown team, or draft time)
      projection    that week's pre-game projection (0 on a bye or when out; at draft time the preseason rate)
      points        real points in that week; null when the week is unplayed or he did not play
      ros_ppg       expected points a game from here on, if healthy
      value         what he counts for in a trade package; trade_value: value over replacement (K, DST: 0)
      rank, pos_rank  by trade_value, e.g. 14 and "RB7";  adp: preseason ADP or null
      games, ppg    real games and points a game BEFORE that week
      out           no projection in his team's latest game week (this one, or the one before a bye), or an
                    O / IR / PUP / SUS tag this week
      trade_locked  traded in the last two weeks
  Side       TeamRef + {projected, points, record: Record}    projected: null for a week not reached yet
  Record     {wins, losses, ties, rank, points_for, points_against, place}    place: final finish, else null
  GameRow    {home: Side, away: Side, winner: team_id | null, label}
      label         null | "Semi-final" | "Championship" | "Third place" | "Consolation" | "<N>th place"
                    (playoff week 2: the consolation games decide 5th, 7th, 9th... in pairs, e.g. "5th place")
  Event      {id, week, kind, team: TeamRef | null, text, players: PlayerRef[]}
      kind          draft | add | drop | claim | trade | ir | result | playoffs | champion
      an add or claim with a drop is ONE event (no separate "drop"): players = [added, dropped]
  Page       {total, limit, offset, results: [...]}
  Verdict    {accepted, reasons: string[], fair_label, value_give, value_get, percent, your_gain, their_gain,
              give: [{player: PlayerRef, value}], get: [same], your_drops: PlayerRef[], their_drops: PlayerRef[],
              suggestion: string | null}
      "give" is always what the USER sends. percent: (get - give) / the larger, a fraction; above 0 the
      user gets more. fair_label: fair | slight_win | big_win | slight_loss | big_loss (for the user).
      your_gain / their_gain: season-weighted lineup points each side gains.
  Usage      {games, snap_pct, targets, target_share, carries}    a skill player's role, averaged over `games`
      snap_pct      % of offensive snaps (1 decimal), null where snap counts are not loaded; target_share a 0-1
                    fraction (3 decimals), null when unknown; targets, carries per game (1 decimal)

GET  /seasons                     {results: Readiness[], prepare: PrepareStatus | null}
      Readiness {season, ready, has_stats, has_schedule, has_kdst, adp_players, projection_weeks, kickers, defenses,
                 injury_weeks}      injury_weeks: weeks with injury tags stored (`ready` does not need it)
      PrepareStatus {season, status: running | done | failed, step, error, updated_at?}
                    step may be "game_results" | "snaps" | "adp" | "projections" | "injuries"
POST /seasons/{season}/prepare?force=    PrepareStatus of the import now running (maybe another season's)
GET  /games                       {results: [{id, name, season, phase, week, teams, scoring, champion: name | null,
                                              created_at, updated_at}]}, most recently played first
GET  /games/{id}                  Summary
      Summary {id, name, season, phase: draft | waivers | open | complete, week, end_week, user_team_id,
               settings: {teams, scoring, starters: {slot: n}, bench, roster_size, draft_slot, waiver_mode,
                          difficulty, regular_season_weeks, playoff_teams, trade_deadline_week},
               teams: TeamRef[], on_the_clock: team_id | null,
               next_action: {step: pick | wednesday | play | done, label},
               record: Record, matchup: {week, playoff, label, you: Side, opponent: Side} | null,
               pending: {claims, offers}, champion: TeamRef | null, updated_at,
               ir_blocked: PlayerRef[], has_injuries: bool}
      ir_blocked    the USER's IR players who lost their tag (non-empty = adds, claims and trades refused)
      has_injuries  injury tags are loaded for this season
      waiver_mode   wednesday | faab | none (faab: blind bids from a $100 budget, see claims)
Every POST under /games answers {game: Summary, events: Event[], ...extras}: `events` is what the action
caused, oldest first (the CPUs' picks, the waiver run, the week's results). Extras:
  POST /games (create; season may be "random"; waiver_mode wednesday | faab | none), .../draft/pick,
       draft/auto?whole_draft=&overall=, claims {add, drop?, bid?}, claims/{id}/bid {bid},
       claims/{id}/cancel, claims/reorder, add-drop {add, drop?}, drop, ir/place {player_id},
       ir/activate {player_id, drop?, swap?}          none
       claims bid: whole dollars 0..100 and at most the budget left; required in FAAB leagues, refused
       otherwise. claims/{id}/bid changes a pending claim's bid (FAAB, before the waiver run). A FAAB
       claim's event text names the winning bid ("... claimed X for $12").
       draft/pick body {player_id, overall?} and draft/auto ?overall=: optional, the pick number the page
       shows; 409 when the draft is at another pick (a double click or retry never picks twice)
  POST /games/{id}/delete         instead of the above: {deleted: id}
  POST .../lineup                 team: Team (the user's, with the new lineup)
  POST .../advance                step, week (the week it acted on)
  POST .../trades/evaluate        instead of the above: {verdict: Verdict} (nothing changes)
  POST .../trades/propose         verdict: Verdict, executed: bool
  POST .../trades/offers/{id}/respond   accepted, expired, message: string | null, verdict: Verdict | null
      an offer that no longer stands answers 200 with expired true and the reason in message

GET  .../draft?position=&search=&limit=&offset=      Page of {player: PlayerRef, adp, pos_rank, rank, projection}
      (undrafted players in draft order; empty after the draft) + {phase, on_the_clock, is_user_turn, round,
      overall, rounds, total_picks, order: TeamRef[], picks: [{overall, round, team: TeamRef, player: PlayerRef}],
      roster: {position: PlayerRef[]}}     round / overall: the pick now on the clock, null when done
GET  .../team?team_id=&week=      Team {team: TeamRef, week, played, manual_lineup, projected_total, actual_total,
                                        record: Record, results: [PlayerRow + {slot, starting}],
                                        ir: [PlayerRow + {slot: "IR", starting: false}]}
      ir: the team's IR players, current week only (else []). results never holds an IR player.
      slot: QB | RB | WR | TE | FLEX | K | DST | BN, starters first in slot order. A past week lists only what
      was started (its bench is not kept). An empty starting slot has no row: compare with settings.starters.
GET  .../matchup?week=&team_id=   {week, played, playoff, label, winner, home: Team, away: Team}
GET  .../scoreboard?week=         {week, played, playoff, results: GameRow[]}    any week of the season
GET  .../standings                {results: [Record + TeamRef + {seed, in_playoffs}], regular_season_weeks,
                                   playoff_teams, seeds_final, bracket: Scoreboard[], champion: TeamRef | null}
      rows are in final order once the season is over, else by seed. bracket: one scoreboard per playoff
      week whose games are known.
GET  .../schedule?team_id=        {team: TeamRef, results: [{week, playoff, played, opponent: TeamRef | null,
                                   points, opponent_points, result: W | L | T | null}]}
GET  .../activity?kind=&limit=&offset=     Page of Event, newest first; kind may list several ("add,drop,ir")
GET  .../players?position=&search=&status=&sort=&limit=&offset=     Page of WireRow + {week}
      WireRow = PlayerRow + {status: rostered | waivers | free_agent, owner: TeamRef | null, season_points,
                last_points, usage: Usage | null}      season_points / last_points: real points in weeks already played
      usage         his last 3 games BEFORE the current week (games 1-3); null for K, DST or before his first game
      position: QB | RB | WR | TE | K | DST | FLEX. status: available (default) | waivers | free_agent |
      rostered | all. sort: value (default) | projection | ros_ppg | adp | points | name.
GET  .../players/{player_id}      {player: WireRow, weeks: [{week, played, team, opponent, bye, out, projection,
                                   points, ros_ppg, value, trade_value, rank, pos_rank, injury: Injury | null,
                                   usage: Usage | null}]}
      injury: that week's tag (weeks 1..current). usage: that week's own game (games 1) once played, else null.
      weeks from the first one he was in the pool (a late arrival has no rows before his first projection
      or game) to the current one. points: null when the week is unplayed or he did not play.
GET  .../claims                   {phase, can_claim, priority, waiver_order: TeamRef[],
                                   results: [{id, order, add: PlayerRow, drop: PlayerRow | null, bid}],
                                   faab: {budget, left, teams: [{team: TeamRef, left}]} | null}
      bid: the claim's whole-dollar bid (0 outside FAAB). faab: null unless waiver_mode is faab; left is the
      user's budget left, teams every team's (current waiver order). Other teams' pending bids are never shown.
GET  .../trades                   {open, deadline_week, max_players, out_discount, offers: [Offer + {verdict, note}],
                                   results: Offer[]}
      out_discount: what a CPU counts a player who is out this week at when it receives him (0.8)
      Offer {id, week, status: pending | accepted | declined | expired, from_team: TeamRef, to_team: TeamRef,
             give: PlayerRef[] (what from_team sends), get: PlayerRef[]}
      offers: CPU offers the user can answer, each with a preview Verdict from the user's side (or null
      and a note saying why it cannot be accepted as is). results: every other offer and trade, newest first.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property

from services import replay_draft, replay_ir, replay_season, replay_state as rs, replay_trades, replay_values as rv
from services.replay_pool import ALL_POSITIONS, SeasonData
from services.replay_state import Event, Settings, State, Team, WeekResult
from services.replay_values import Knowledge, ReplayValue

BENCH = "BN"
DEFAULT_PAGE, MAX_PAGE = 50, 200
NEXT_STEP = {"draft": "pick", "waivers": "wednesday", "open": "play", "complete": "done"}
SETTING_KEYS = ("teams", "scoring", "bench", "roster_size", "draft_slot", "waiver_mode", "difficulty",
                "regular_season_weeks", "playoff_teams", "trade_deadline_week")
RECORD_KEYS = ("wins", "losses", "ties", "rank", "points_for", "points_against")
Lineup = Mapping[str, Sequence[int]]


@dataclass(frozen=True)
class Game:
    """One saved game as the views see it."""
    id: int
    name: str
    settings: Settings
    state: State
    full_season: SeasonData                 # never read directly: use `data` and `know_at`
    updated_at: str | None = None

    @property
    def played(self) -> int:
        """The last week with a result (0 before week 1 is played)."""
        return self.state.results[-1].week if self.state.results else 0

    @cached_property
    def data(self) -> SeasonData:
        """The season as known now: real points of played weeks only."""
        return self.full_season.as_of(self.played + 1)

    def know_at(self, week: int) -> Knowledge:
        """What everyone knew before `week` (the object the engine used); never a week not reached yet."""
        if not 0 <= week <= self.state.week:
            raise ValueError(f"Week {week} has not been reached yet: this league is in week {self.state.week}.")
        return rv.knowledge(self.full_season.as_of(week), rv.league_settings(self.settings), week,
                            self.settings.end_week)

    @cached_property
    def know(self) -> Knowledge:
        return self.know_at(self.state.week)

    @property
    def has_injuries(self) -> bool:
        """Any tag stored for the season (says nothing about a week: the one permitted read of full_season)."""
        return bool(self.full_season.injuries)

    @cached_property
    def user(self) -> Team:
        return rs.user_team(self.state)

    @cached_property
    def table(self) -> list[dict]:
        """replay_season.final_standings: the regular-season rows in final (or seed) order, with "place"."""
        return replay_season.final_standings(self.state, self.settings)


# ---------------- shared pieces ----------------

def team_ref(team: Team) -> dict:
    return {"team_id": team.id, "name": team.name, "is_user": team.is_user}


def player_ref(value: ReplayValue) -> dict:
    tag = value.injury
    return {"id": value.player_id, "name": value.name, "position": value.position, "team": value.team,
            "injury": {"tag": tag.tag, "practice": tag.practice, "injury": tag.injury} if tag else None}


def refs(game: Game, player_ids: Sequence[int]) -> list[dict]:
    return [player_ref(game.know.by_id[pid]) for pid in player_ids if pid in game.know.by_id]


def page(items: Sequence, limit: int, offset: int, shape: Callable = lambda item: item) -> dict:
    return {"total": len(items), "limit": limit, "offset": offset,
            "results": [shape(item) for item in items[offset:offset + limit]]}


def positions_for(position: str | None) -> tuple[str, ...]:
    """The positions a filter means: one of the six, the FLEX group, or all of them."""
    if position is None:
        return ALL_POSITIONS
    if position == "FLEX":
        return rv.FLEX_POSITIONS
    if position not in ALL_POSITIONS:
        raise ValueError(f"Unknown position '{position}'. Valid: {[*ALL_POSITIONS, 'FLEX']}")
    return (position,)


def name_matches(search: str | None, name: str) -> bool:
    return (search or "").strip().lower() in name.lower()


def week_of(game: Game, week: int | None) -> int:
    """The week a screen shows: the current one by default, never one that has not been reached."""
    current = game.state.week
    chosen = current if week is None else week
    if not min(1, current) <= chosen <= current:
        raise ValueError(f"Week {chosen} can't be shown: this league is in week {current}.")
    return chosen


def result_of(state: State, week: int) -> WeekResult | None:
    return next((result for result in state.results if result.week == week), None)


def player_row(game: Game, know: Knowledge, player_id: int) -> dict:
    """A PlayerRow as of `know.week`."""
    value, week = know.by_id[player_id], know.week
    points = game.data.actual.get((player_id, week)) if 1 <= week <= game.played else None
    opponent = know.schedule.opponent(value.team, week) if week else None
    bye = bool(week) and value.team is not None and opponent is None
    return {"player": player_ref(value), "opponent": "BYE" if bye else opponent, "bye": bye, "out": value.out,
            "projection": round(value.projection, 2), "points": points, "ros_ppg": value.ros_ppg,
            "value": value.market_value, "trade_value": value.trade_value, "rank": value.rank,
            "pos_rank": value.pos_rank, "adp": value.adp, "games": value.games, "ppg": value.ppg,
            "trade_locked": week == game.state.week and rs.retrade_locked(game.state, player_id)}


def event_row(game: Game, event: Event) -> dict:
    owner = next((team for team in game.state.teams if team.id == event.team_id), None)
    return {"id": event.id, "week": event.week, "kind": event.kind, "team": team_ref(owner) if owner else None,
            "text": event.text, "players": refs(game, event.player_ids)}


def new_events(game: Game, before: State | None) -> list[dict]:
    """The events an action added, oldest first."""
    return [event_row(game, event) for event in game.state.events[len(before.events) if before else 0:]]


def record(game: Game, team_id: int) -> dict:
    row = next(row for row in game.table if row["team_id"] == team_id)
    return {**{key: row[key] for key in RECORD_KEYS},
            "place": row["place"] if game.state.phase == "complete" else None}


# ---------------- lineups and games ----------------

def lineup_of(game: Game, owner: Team, week: int) -> tuple[Lineup, list[int]]:
    """(starters by slot, bench) in a week: as played if it was, else what would start now. The bench is
    only known for the current week."""
    result = result_of(game.state, week)
    lineup = (result.starters[owner.id] if result is not None
              else replay_season.starters_for(game.state, game.settings, game.know_at(week), owner.id))
    started = {pid for ids in lineup.values() for pid in ids}
    by_id = game.know.by_id
    bench = sorted((pid for pid in owner.roster if pid not in started and week == game.state.week),
                   key=lambda pid: (ALL_POSITIONS.index(by_id[pid].position), -by_id[pid].projection, pid))
    return lineup, bench


def side(game: Game, team_id: int, week: int) -> dict:
    """One team's line on a scoreboard."""
    owner, result, projected = rs.team(game.state, team_id), result_of(game.state, week), None
    if week <= game.state.week:
        lineup, _ = lineup_of(game, owner, week)
        projected = rv.lineup_points(game.know_at(week), [pid for ids in lineup.values() for pid in ids])
    return {**team_ref(owner), "projected": projected, "points": result.scores[team_id] if result else None,
            "record": record(game, team_id)}


def winner(game: Game, pair: Sequence[int], week: int) -> int | None:
    """The team id that won; None when unplayed or a regular-season tie (a playoff tie: the higher seed)."""
    result = result_of(game.state, week)
    if result is None:
        return None
    if result.scores[pair[0]] != result.scores[pair[1]]:
        return max(pair, key=lambda team_id: result.scores[team_id])
    return min(pair, key=replay_season.playoff_seeds(game.state).index) if result.playoff else None


def label(settings: Settings, week: int, index: int) -> str | None:
    """A playoff game's name. Week 2: game 0 decides 1st, game 1 3rd, then each pair of games 5th and 7th,
    9th and 11th... (the leftover pair of 10 and 14 teams too)."""
    if not replay_season.is_playoff_week(settings, week):
        return None
    if week == settings.regular_season_weeks + 1:
        return "Semi-final" if index < 2 else "Consolation"
    if index < 2:
        return ("Championship", "Third place")[index]
    return f"{4 * (index // 2) + 1 + 2 * (index % 2)}th place"


def game_row(game: Game, pair: Sequence[int], week: int, index: int) -> dict:
    return {"home": side(game, pair[0], week), "away": side(game, pair[1], week),
            "winner": winner(game, pair, week), "label": label(game.settings, week, index)}


def scoreboard(game: Game, week: int | None = None) -> dict:
    """Every game of a week (default: the current one). A week not reached yet has no projections."""
    week = max(1, game.state.week) if week is None else week
    pairs = replay_season.matchups_for_week(game.state, game.settings, week)
    return {"week": week, "played": result_of(game.state, week) is not None,
            "playoff": replay_season.is_playoff_week(game.settings, week),
            "results": [game_row(game, pair, week, index) for index, pair in enumerate(pairs)]}


def team(game: Game, team_id: int | None = None, week: int | None = None) -> dict:
    """A team's roster for a week (default: the user's, now): starters in slot order, then the bench."""
    owner = rs.team(game.state, game.user.id if team_id is None else team_id)
    week = week_of(game, week)
    know, result = game.know_at(week), result_of(game.state, week)
    lineup, bench = lineup_of(game, owner, week)
    rows = [{"slot": slot, "starting": True, **player_row(game, know, pid)}
            for slot, ids in lineup.items() for pid in ids]
    rows += [{"slot": BENCH, "starting": False, **player_row(game, know, pid)} for pid in bench]
    injured = owner.ir if week == game.state.week else ()
    return {"team": team_ref(owner), "week": week, "played": result is not None,
            "manual_lineup": owner.is_user and result is None and game.state.lineup is not None,
            "projected_total": rv.lineup_points(know, [pid for ids in lineup.values() for pid in ids]),
            "actual_total": result.scores[owner.id] if result else None, "record": record(game, owner.id),
            "results": rows,
            "ir": [{"slot": "IR", "starting": False, **player_row(game, know, pid)} for pid in injured]}


def matchup(game: Game, week: int | None = None, team_id: int | None = None) -> dict:
    """Both lineups of one game, side by side (default: the user's game this week)."""
    week = week_of(game, week)
    wanted = rs.team(game.state, game.user.id if team_id is None else team_id).id
    pairs = replay_season.matchups_for_week(game.state, game.settings, week)
    index = next((i for i, pair in enumerate(pairs) if wanted in pair), None)
    if index is None:
        raise ValueError(f"There is no game for that team in week {week}.")
    home, away = pairs[index]
    return {"week": week, "played": result_of(game.state, week) is not None,
            "playoff": replay_season.is_playoff_week(game.settings, week),
            "label": label(game.settings, week, index), "winner": winner(game, pairs[index], week),
            "home": team(game, home, week), "away": team(game, away, week)}


# ---------------- the game ----------------

def _next_action(game: Game) -> dict:
    week, labels = game.state.week, {
        "pick": "Make your pick", "wednesday": "Sim to Wednesday", "done": "Season complete"}
    step = NEXT_STEP[game.state.phase]
    return {"step": step, "label": labels.get(step, f"Play week {week}")}


def _headline(game: Game) -> dict | None:
    """The user's game this week (the final once the season is over); None during the draft."""
    if game.state.phase == "draft":
        return None
    week, settings = game.state.week, game.settings
    pairs = replay_season.matchups_for_week(game.state, settings, week)
    index = next((i for i, pair in enumerate(pairs) if game.user.id in pair), None)
    if index is None:
        return None
    you, opponent = sorted(pairs[index], key=lambda team_id: team_id != game.user.id)
    return {"week": week, "playoff": replay_season.is_playoff_week(settings, week),
            "label": label(settings, week, index), "you": side(game, you, week),
            "opponent": side(game, opponent, week)}


def summary(game: Game) -> dict:
    """The game at a glance: returned by GET game and inside every POST's answer."""
    state, settings = game.state, game.settings
    champion = next((t for t in state.teams if t.id == state.champion), None)
    return {
        "id": game.id, "name": game.name, "season": settings.season, "phase": state.phase, "week": state.week,
        "end_week": settings.end_week, "user_team_id": game.user.id,
        "settings": {**{key: getattr(settings, key) for key in SETTING_KEYS}, "starters": dict(settings.starters)},
        "teams": [team_ref(t) for t in state.teams],
        "on_the_clock": replay_draft.on_the_clock(state, settings), "next_action": _next_action(game),
        "record": record(game, game.user.id), "matchup": _headline(game),
        "pending": {"claims": len(state.claims), "offers": len(replay_trades.offers_for_user(state))},
        "champion": team_ref(champion) if champion else None, "updated_at": game.updated_at,
        "ir_blocked": refs(game, replay_ir.ineligible(state, game.know, game.user.id)),
        "has_injuries": game.has_injuries,
    }


def _board_row(value: ReplayValue) -> dict:
    return {"player": player_ref(value), "adp": value.adp, "pos_rank": value.pos_rank, "rank": value.rank,
            "projection": round(value.projection, 2)}


def draft(game: Game, position: str | None = None, search: str | None = None, limit: int = DEFAULT_PAGE,
          offset: int = 0) -> dict:
    """The draft room: picks so far, the order, the user's roster, and who is left (in draft order)."""
    state, settings, know = game.state, game.settings, game.know_at(0)
    wanted, teams = positions_for(position), {t.id: team_ref(t) for t in state.teams}
    drafting = replay_draft.on_the_clock(state, settings) is not None
    left = replay_draft.available(state, game.full_season.as_of(0)) if drafting else []
    board = [know.by_id[p.player_id] for p in left if p.position in wanted and name_matches(search, p.name)]
    overall = len(state.picks) + 1 if drafting else None
    mine = [pick.player_id for pick in state.picks if pick.team_id == game.user.id]
    return {
        **page(board, limit, offset, _board_row), "phase": state.phase,
        "on_the_clock": replay_draft.on_the_clock(state, settings),
        "is_user_turn": replay_draft.on_the_clock(state, settings) == game.user.id,
        "round": (overall - 1) // settings.teams + 1 if overall else None, "overall": overall,
        "rounds": settings.rounds, "total_picks": settings.rounds * settings.teams,
        "order": [teams[team_id] for team_id in state.draft_order],
        "picks": [{"overall": pick.overall, "round": pick.round, "team": teams[pick.team_id],
                   "player": player_ref(know.by_id[pick.player_id])} for pick in state.picks],
        "roster": {pos: [player_ref(know.by_id[pid]) for pid in mine if know.by_id[pid].position == pos]
                   for pos in ALL_POSITIONS},
    }
