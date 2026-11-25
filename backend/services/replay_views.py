"""Replay League: the JSON the frontend gets (mirror it in frontend/src/api/types/replay.ts).

This file holds the shared pieces and the game, draft, team and matchup screens; the league-wide screens
(standings, schedule, players, claims, trades, activity) are in services/replay_views_league.py.

No hindsight: a view reads the season only through `Game.data` (real points of weeks already played) and
`Game.know_at(week)` for weeks up to the current one. A number about a week that has not been played is a
pre-game projection or null, never a result. Paths below are under /api/replay; request bodies are the
pydantic models in api/replay_routes.py (create) and api/replay_action_routes.py.

Weeks are NFL weeks. A league that jumped in at settings.start_week K drafts in week K (Summary.week is K
during its draft, and the draft room shows what was known then) and plays weeks K on: schedule, scoreboard,
standings, matchup, team and playoffs only know weeks from K (asking for an earlier week is a 422), while a
player's page, the wire's stats and PlayerRow points show the real NFL weeks before K.

Shared pieces
  TeamRef    {team_id, name, is_user, tendency: string | null, mood: string | null}
      tendency      a one-line hint at how a CPU trades, from its hidden persona; null for the user and
                    before 3 league weeks are played (the persona's name is never shown)
      mood          who a CPU is down on and high on right now, e.g. "Worried about X. High on Y."
                    (services/replay_mood.py); null for the user, in the draft, before 3 NFL weeks of results are
                    known (replay_mood.moods_known: week 4 on, which is the first week of a league that jumped in at
                    week 4 or later), once the season is over, or with no
                    feelings
  Injury     {tag: Q | D | O | IR | PUP | SUS, practice: DNP | LP | FP | null, injury: string | null}
  PlayerRef  {id, name, position, team, injury: Injury | null, hype: {reason} | null}
      team          his NFL team as known before the week shown, or null
      hype          the trade market is hyping him: an opportunity shock (services/replay_shocks.py) lifted his
                    value this week (same week as `injury`). reason: public words for why, e.g. "Puka Nacua went
                    out for the season", "he won the backfield" (the UI shows "role jumped after <reason>").
                    Null otherwise, for a move down, and always in a preseason draft room.
      injury        his tag in the CURRENT week, wherever the ref appears (events, offers, verdicts, claims). Only a
                    PlayerRow built for another week (team / matchup with ?week=) carries that week's tag. Always null
                    in a preseason draft room (a jump-in league's draft room shows its start week's tags).
                    O, IR, PUP and SUS mean he does not play; O, IR and PUP may go on IR.
  PlayerRow  {player: PlayerRef, opponent, bye, out, projection, points, ros_ppg, value, rank, pos_rank, adp,
              pre_rank, expert_rank, news: {back, text} | null, buzz: string | null, games, ppg, trade_locked,
              defense}
      opponent      NFL team code, "BYE" or null (unknown team, or draft time)
      projection    that week's pre-game projection (0 on a bye or when out; at a preseason draft the preseason rate)
      points        real points in that week; null when the week is unplayed or he did not play
      ros_ppg       expected points a game from here on, if healthy
      value         his 0-1000 trade chart value this week (after injury), a whole number
      rank, pos_rank  by chart value, e.g. 14 and "RB7";  adp: preseason ADP or null
      pre_rank      overall preseason rank (experts interleaved on ADP, else ADP order) or null
      expert_rank   this week's expert position rank, null when unranked (or before 2020)
      news          the injury report on a player who is out: back = the week he is said to return
                    (null = out for the season), text = the report. Noisy: it can be wrong.
      buzz          a simulated season's public news line about his role ("Beat reporters say his role is growing;
                    expect more work."), dated that week or the one before and known before that week's games;
                    a rumour, right only some of the time. Null otherwise (always in a real replay).
      games, ppg    real games and points a game BEFORE that week
      out           no projection in his team's latest game week (this one, or the one before a bye), or an
                    O / IR / PUP / SUS tag this week
      trade_locked  traded in the last two weeks
      defense       DefenseRating (services/replay_defense.py) of that week's opponent vs his position, as known
                    before that week; null for K/DST, a bye, an unknown team or nothing known
  Side       TeamRef + {projected, points, record: Record}    projected: null for a week not reached yet
  Record     {wins, losses, ties, rank, points_for, points_against, place}    place: final finish, else null
  GameRow    {home: Side, away: Side, winner: team_id | null, label}
      label         null | "Quarter-final" | "Semi-final" | "Championship" | "Third place" | "Consolation"
                    (title bracket games by round; every other playoff-week game, for 5th place and down, is
                    "Consolation")
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

GET  /seasons                     {results: Readiness[], simulated: Simulated[], prepare: PrepareStatus | null}
      Readiness {season, ready, has_stats, has_schedule, has_kdst, adp_players, projection_weeks, kickers, defenses,
                 injury_weeks, expert_snapshots, last_week}      injury_weeks: weeks with injury tags stored (`ready`
                 does not need it); last_week: the season's last week with games (a week-18 final needs 18)
      Simulated {season, kind: live | future, through, ready, can_start_now, reason: string | null}
                    live: the season in progress, real through week `through`, simulated after it (prepare it like a
                    past season); future: the next season, simulated whole (through 0). can_start_now: a league may
                    use start "now" (draft today, play from week through + 1); reason: why it is not ready, else null
      PrepareStatus {season, status: running | done | failed, step, error, updated_at?}
                    step may be "game_results" | "snaps" | "adp" | "projections" | "injuries" | "experts"
POST /seasons/{season}/prepare?force=    PrepareStatus of the import now running (maybe another season's)
GET  /games                       {results: [{id, name, season, phase, week, teams, scoring, champion: name | null,
                                              kind, created_at, updated_at}]}, most recently played first
                                  kind: as Summary.kind
GET  /games/{id}                  Summary
      Summary {id, name, season, phase: draft | waivers | open | complete, week, end_week, user_team_id,
               settings: {teams, scoring, starters: {slot: n}, bench, roster_size, draft_slot, waiver_mode,
                          difficulty, regular_season_weeks, playoff_teams, trade_deadline_week,
                          scoring_rules: {reception, te_premium, pass_td, turnover} | null (null = the preset)},
               teams: TeamRef[], on_the_clock: team_id | null,
               next_action: {step: pick | wednesday | play | done, label},
               record: Record, matchup: {week, playoff, label, you: Side, opponent: Side} | null,
               pending: {claims, offers}, champion: TeamRef | null, created_at, updated_at,
               ir_blocked: PlayerRef[], has_injuries: bool, trades_open: bool, rank_source: experts | model,
               kind: replay | live | future, sim_from: week | null, start_week, chat: int}
      kind, sim_from  a past season replayed, or a simulated one: its NFL weeks from sim_from on are simulated
      start_week    the first week the league plays (1, or later for a live league started "now")
      chat          lines in the league chat so far (GET .../chat total), for an unread dot
      created_at    when the league was made (with its id, names this league even after an id is reused)
      ir_blocked    the USER's IR players who lost their tag (non-empty = adds, claims and trades refused)
      has_injuries  injury tags are loaded for this season
      trades_open   trades can be made now: through the deadline week, and its waiver phase (the deadline
                    passes at "Sim to Wednesday" after the deadline week's games)
      rank_source   what this week's chart values lean on: "experts" (expert ranks) or "model" (none loaded)
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
  POST .../trades/propose         verdict: Verdict, executed: bool, counter: Offer | null
      counter       the CPU's counter offer after a decline (pending, for the user to answer), or null
  POST .../trades/offers/{id}/counter   {give, get, drops?}: the user's counter to a CPU's pending offer (give =
      what the user sends); that offer becomes "countered". Answers like propose.
  POST .../trades/offers/{id}/respond   accepted, expired, message: string | null, verdict: Verdict | null
      an offer that no longer stands answers 200 with expired true and the reason in message

GET  .../draft?position=&search=&sort=&direction=&limit=&offset=
      Page of {player: PlayerRef, adp, pos_rank, rank, pre_rank, round, projection, buzz} (undrafted players; empty after
      the draft). sort: rank (pre_rank, default) | adp | projection; direction asc | desc (default asc, desc for
      projection); players without that number come last either way. round: the round pre_rank implies,
      ceil(pre_rank / teams), or null. projection: raw preseason points a game. buzz: as in PlayerRow, at the draft.
      + {phase, on_the_clock, is_user_turn, round, overall, rounds, total_picks, order: TeamRef[], picks: [{overall, round, team: TeamRef, player: PlayerRef}],
      roster: {position: PlayerRef[]}}     round / overall: the pick now on the clock, null when done
GET  .../team?team_id=&week=      Team {team: TeamRef, week, played, manual_lineup, projected_total, actual_total,
                                        record: Record, results: [PlayerRow + {slot, starting}],
                                        bench_total, bench_recorded,
                                        ir: [PlayerRow + {slot: "IR", starting: false}]}
      ir: the team's IR players, current week only (else []). results never holds an IR player.
      slot: QB | RB | WR | TE | FLEX | SUPERFLEX | K | DST | BN, starters first in slot order. A played week lists its bench as
      saved: bench_recorded false (no BN rows, bench_total null) for weeks played before benches were saved.
      bench_total: real points left on the bench, null unless played and recorded. An empty starting slot has
      no row: compare with settings.starters.
GET  .../matchup?week=&team_id=   {week, played, playoff, label, winner, home: Team, away: Team}
      without team_id: the user's game, else (a bye week) the week's first game; 422 when team_id has no game
GET  .../scoreboard?week=         {week, played, playoff, results: GameRow[]}    any week of the league (start_week on)
GET  .../standings                {results: [Record + TeamRef + {seed, in_playoffs}], regular_season_weeks,
                                   playoff_teams, playoff_weeks: int[], byes, seeds_final}
      rows are in final order once the season is over, else by seed. in_playoffs: seed <= playoff_teams.
      byes: how many top seeds skip round one (2 ** rounds - playoff_teams). The bracket: GET .../playoffs.
GET  .../schedule?team_id=        {team: TeamRef, results: [{week, playoff, played, idle, opponent: TeamRef | null,
                                   points, opponent_points, result: W | L | T | null}]}
      idle          the week's games are known and the team has none (a playoff bye, or the odd team out of
                    a pool): opponent, opponent_points and result are null
GET  .../activity?kind=&limit=&offset=     Page of Event, newest first; kind may list several ("add,drop,ir")
GET  .../chat?limit=&offset=      Page of Chat, newest first (services/replay_chat.py: the CPUs talk, the user reads)
      Chat {id, week, team: TeamRef, to_user: bool, kind, text, players: PlayerRef[]}
      kind          blowout_win | close_win | close_loss | blowout_loss | boom | bust | streak_win | streak_loss |
                    upset | clinch | eliminated | champion | runner_up | trade_reaction | shop
      to_user       addressed to the user (his opponent's line, a manager shopping him a player)
GET  .../players, .../players/{player_id}    the wire and a player's page: see services/replay_views_players.py
GET  .../claims                   {phase, can_claim, priority, waiver_order: TeamRef[],
                                   results: [{id, order, add: PlayerRow, drop: PlayerRow | null, bid}],
                                   faab: {budget, left, teams: [{team: TeamRef, left}]} | null}
      bid: the claim's whole-dollar bid (0 outside FAAB). faab: null unless waiver_mode is faab; left is the
      user's budget left, teams every team's (current waiver order). Other teams' pending bids are never shown.
GET  .../trades                   {open, deadline_week, max_players, offers: [Offer + {verdict, note}],
                                   results: Offer[], shops: [{team: TeamRef, player: PlayerRef, message}]}
      open          Summary.trades_open
      Offer {id, week, status: pending | accepted | declined | expired | countered, from_team: TeamRef,
             to_team: TeamRef, give: PlayerRef[] (what from_team sends), get: PlayerRef[], counter_to: id | null,
             message: string | null}
      counter_to    the offer this one answers (a counter), else null
      message       a CPU offer's one line to the user in his voice (why he offers it); null otherwise
      shops         players a worried CPU is shopping to the user this week (a little under his usual price; the
                    discount is never shown), with his message; empty once trades close
      offers: CPU offers the user can answer, each with a preview Verdict from the user's side (or null
      and a note saying why it cannot be accepted as is). results: every other offer and trade, newest first.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import cached_property

from services import (
    replay_chat, replay_defense, replay_draft, replay_ir, replay_mood, replay_personas, replay_season,
    replay_state as rs, replay_trades, replay_values as rv,
)
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
    seed: int = 0                           # the game's seed (injury-news noise); never shown
    created_at: str | None = None           # when the league was made: with its id, it names the league for good

    @property
    def played(self) -> int:
        """The last NFL week with real results known: the league's last played week, or in a league that
        jumped in at week K the week before K until it plays (0 before week 1 is played)."""
        return max(self.state.results[-1].week if self.state.results else 0, self.state.week - 1)

    @property
    def weeks_played(self) -> int:
        """League weeks played (a jump-in league's weeks before its start_week are not league weeks)."""
        return len(self.state.results)

    @cached_property
    def data(self) -> SeasonData:
        """The season as known now: real points of played weeks only."""
        return self.full_season.as_of(self.played + 1)

    def know_at(self, week: int) -> Knowledge:
        """What everyone knew before `week` (the object the engine used); never a week not reached yet."""
        if not 0 <= week <= self.state.week:
            raise ValueError(f"Week {week} has not been reached yet: this league is in week {self.state.week}.")
        return rv.knowledge(self.full_season.as_of(week), rv.league_settings(self.settings), week,
                            self.settings.end_week, self.seed, drafted=min(week, rs.draft_week(self.settings)))

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
    def moods(self) -> dict[int, str | None]:
        """Each CPU's mood line now (replay_mood.mood_line), worked out once per request: team_ref asks per row."""
        return {team.id: replay_mood.mood_line(self.know, team) for team in self.state.teams if not team.is_user}

    @cached_property
    def table(self) -> list[dict]:
        """replay_season.final_standings: the regular-season rows in final (or seed) order, with "place"."""
        return replay_season.final_standings(self.state, self.settings)


# ---------------- shared pieces ----------------

def team_ref(game: Game, team: Team) -> dict:
    """tendency and mood: hints at a CPU's (hidden) persona and feelings, once it has played enough league weeks."""
    tendency = None if team.is_user else replay_personas.tendency(team.persona, game.weeks_played)
    # A mood is about his players' form: it needs played NFL weeks, not league weeks (a jump-in league has them).
    feels = not team.is_user and game.state.phase not in ("draft", "complete") and replay_mood.moods_known(game.state.week)
    mood = game.moods.get(team.id) if feels else None
    return {"team_id": team.id, "name": team.name, "is_user": team.is_user, "tendency": tendency, "mood": mood}


def player_ref(value: ReplayValue) -> dict:
    tag = value.injury
    return {"id": value.player_id, "name": value.name, "position": value.position, "team": value.team,
            "injury": {"tag": tag.tag, "practice": tag.practice, "injury": tag.injury} if tag else None,
            "hype": {"reason": value.hype_reason} if value.hype_reason else None}


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
    """The week a screen shows: the current one by default, never one that has not been reached nor (in a
    league that jumped in) one before its start_week."""
    current = game.state.week
    chosen = current if week is None else week
    if not min(game.settings.start_week, current) <= chosen <= current:
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
            "value": round(value.chart), "rank": value.rank, "pos_rank": value.pos_rank, "adp": value.adp,
            "pre_rank": value.pre_rank, "expert_rank": value.expert_rank, "news": news(value),
            "buzz": buzz(game, player_id, week), "games": value.games, "ppg": value.ppg,
            "defense": replay_defense.ratings(game.full_season.as_of(week)).rating(opponent, value.position),
            "trade_locked": week == game.state.week and rs.retrade_locked(game.state, player_id)}


def buzz(game: Game, player_id: int, week: int) -> str | None:
    """His news line known before `week`: the one dated `week`, else the week before's (as_of drops later ones);
    none while a hard tag has him out that week (last week's role rumour is moot once he is on IR)."""
    known = game.full_season.as_of(week)
    tag = known.injuries.get((player_id, week))
    if tag is not None and tag.hard:
        return None
    return known.notes.get((player_id, week)) or known.notes.get((player_id, week - 1))


def news(value: ReplayValue) -> dict | None:
    """The injury report on a player who is out: when he is said to be back (None = the season) and the text."""
    report = value.news
    return {"back": report.back, "text": report.text} if report else None


def event_row(game: Game, event: Event) -> dict:
    owner = next((team for team in game.state.teams if team.id == event.team_id), None)
    return {"id": event.id, "week": event.week, "kind": event.kind,
            "team": team_ref(game, owner) if owner else None, "text": event.text,
            "players": refs(game, event.player_ids)}


def new_events(game: Game, before: State | None) -> list[dict]:
    """The events an action added, oldest first."""
    return [event_row(game, event) for event in game.state.events[len(before.events) if before else 0:]]


def record(game: Game, team_id: int) -> dict:
    row = next(row for row in game.table if row["team_id"] == team_id)
    return {**{key: row[key] for key in RECORD_KEYS},
            "place": row["place"] if game.state.phase == "complete" else None}


# ---------------- lineups and games ----------------

def lineup_of(game: Game, owner: Team, week: int) -> tuple[Lineup, list[int]]:
    """(starters by slot, bench) in a week: as played if it was (the bench as saved, none in old saves), else
    what would start now (a bench only for the current week)."""
    result = result_of(game.state, week)
    lineup = (result.starters[owner.id] if result is not None
              else replay_season.starters_for(game.state, game.settings, game.know_at(week), owner.id))
    started = {pid for ids in lineup.values() for pid in ids}
    # A played week's bench in that week's projection order; values known then (or now, for anyone not yet in the pool).
    then, now = (game.know_at(week) if result is not None else game.know).by_id, game.know.by_id
    held = result.bench.get(owner.id, ()) if result is not None else owner.roster if week == game.state.week else ()
    bench = sorted((pid for pid in held if pid not in started),
                   key=lambda pid: (ALL_POSITIONS.index((v := then.get(pid) or now[pid]).position), -v.projection, pid))
    return lineup, bench


def side(game: Game, team_id: int, week: int) -> dict:
    """One team's line on a scoreboard."""
    owner, result, projected = rs.team(game.state, team_id), result_of(game.state, week), None
    if week <= game.state.week:
        lineup, _ = lineup_of(game, owner, week)
        projected = rv.lineup_points(game.know_at(week), [pid for ids in lineup.values() for pid in ids])
    return {**team_ref(game, owner), "projected": projected, "points": result.scores[team_id] if result else None,
            "record": record(game, team_id)}


def winner(game: Game, pair: Sequence[int], week: int) -> int | None:
    """The team id that won; None when unplayed or a regular-season tie (a playoff tie: the higher seed)."""
    result = result_of(game.state, week)
    if result is None:
        return None
    if result.scores[pair[0]] != result.scores[pair[1]]:
        return max(pair, key=lambda team_id: result.scores[team_id])
    return min(pair, key=replay_season.playoff_seeds(game.state).index) if result.playoff else None


def label(game: Game, week: int, pair: Sequence[int]) -> str | None:
    """A playoff game's name (replay_season.game_label); None in the regular season."""
    return replay_season.game_label(game.state, game.settings, week, pair)


def game_row(game: Game, pair: Sequence[int], week: int) -> dict:
    return {"home": side(game, pair[0], week), "away": side(game, pair[1], week),
            "winner": winner(game, pair, week), "label": label(game, week, pair)}


def scoreboard(game: Game, week: int | None = None) -> dict:
    """Every game of a week (default: the current one). A week not reached yet has no projections."""
    week = max(1, game.state.week) if week is None else week
    pairs = replay_season.matchups_for_week(game.state, game.settings, week)
    return {"week": week, "played": result_of(game.state, week) is not None,
            "playoff": replay_season.is_playoff_week(game.settings, week),
            "results": [game_row(game, pair, week) for pair in pairs]}


def team(game: Game, team_id: int | None = None, week: int | None = None) -> dict:
    """A team's roster for a week (default: the user's, now): starters in slot order, then the bench."""
    owner = rs.team(game.state, game.user.id if team_id is None else team_id)
    week = week_of(game, week)
    know, result = game.know_at(week), result_of(game.state, week)
    lineup, bench = lineup_of(game, owner, week)
    rows = [{"slot": slot, "starting": True, **player_row(game, know, pid)}
            for slot, ids in lineup.items() for pid in ids]
    rows += [{"slot": BENCH, "starting": False, **player_row(game, know, pid)} for pid in bench]
    injured, saved = owner.ir if week == game.state.week else (), result is not None and owner.id in result.bench
    return {"team": team_ref(game, owner), "week": week, "played": result is not None,
            "manual_lineup": owner.is_user and result is None and game.state.lineup is not None,
            "projected_total": rv.lineup_points(know, [pid for ids in lineup.values() for pid in ids]),
            "actual_total": result.scores[owner.id] if result else None, "record": record(game, owner.id),
            "results": rows, "bench_recorded": saved,
            "bench_total": round(sum(game.data.points(pid, week) for pid in bench), 2) if saved else None,
            "ir": [{"slot": "IR", "starting": False, **player_row(game, know, pid)} for pid in injured]}


def matchup(game: Game, week: int | None = None, team_id: int | None = None) -> dict:
    """Both lineups of one game, side by side (default: the user's game this week, else the week's first game
    when the user has none, e.g. a playoff bye)."""
    week = week_of(game, week)
    wanted = rs.team(game.state, game.user.id if team_id is None else team_id).id
    pairs = replay_season.matchups_for_week(game.state, game.settings, week)
    pair = next((pair for pair in pairs if wanted in pair), pairs[0] if team_id is None and pairs else None)
    if pair is None:
        raise ValueError(f"There is no game for that team in week {week}.")
    home, away = pair
    return {"week": week, "played": result_of(game.state, week) is not None,
            "playoff": replay_season.is_playoff_week(game.settings, week),
            "label": label(game, week, pair), "winner": winner(game, pair, week),
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
    pair = next((pair for pair in pairs if game.user.id in pair), None)
    if pair is None:
        return None
    you, opponent = sorted(pair, key=lambda team_id: team_id != game.user.id)
    return {"week": week, "playoff": replay_season.is_playoff_week(settings, week),
            "label": label(game, week, pair), "you": side(game, you, week),
            "opponent": side(game, opponent, week)}


def league_kind(sim_from: int | None) -> str:
    """Summary.kind: a real replay, the live season (simulated from a later week) or a future one (all simulated)."""
    return "replay" if sim_from is None else "future" if sim_from == 1 else "live"


def summary(game: Game) -> dict:
    """The game at a glance: returned by GET game and inside every POST's answer."""
    state, settings = game.state, game.settings
    champion = next((t for t in state.teams if t.id == state.champion), None)
    return {
        "id": game.id, "name": game.name, "season": settings.season, "phase": state.phase, "week": state.week,
        "end_week": settings.end_week, "user_team_id": game.user.id,
        "settings": {**{key: getattr(settings, key) for key in SETTING_KEYS}, "starters": dict(settings.starters),
                     "ir_slots": replay_ir.IR_SLOTS,
                     "scoring_rules": asdict(settings.scoring_rules) if settings.scoring_rules else None},
        "teams": [team_ref(game, t) for t in state.teams],
        "on_the_clock": replay_draft.on_the_clock(state, settings), "next_action": _next_action(game),
        "record": record(game, game.user.id), "matchup": _headline(game),
        "pending": {"claims": len(state.claims), "offers": len(replay_trades.offers_for_user(state))},
        "champion": team_ref(game, champion) if champion else None,
        "created_at": game.created_at, "updated_at": game.updated_at,
        "ir_blocked": refs(game, replay_ir.ineligible(state, game.know, game.user.id)),
        "has_injuries": game.has_injuries,
        "trades_open": state.phase in replay_trades.TRADE_PHASES and rs.trades_open(state, settings),
        "rank_source": game.know.rank_source,
        "kind": league_kind(settings.sim_from),
        "sim_from": settings.sim_from, "start_week": settings.start_week, "chat": len(replay_chat.chat_for(state)),
    }


DRAFT_SORTS = {"rank": lambda v: v.pre_rank, "adp": lambda v: v.adp, "projection": lambda v: v.projection}


def _board_row(value: ReplayValue, teams: int, rounds: int, buzz_line: str | None = None) -> dict:
    round_ = -(-value.pre_rank // teams) if value.pre_rank else None
    return {"player": player_ref(value), "adp": value.adp, "pos_rank": value.pos_rank, "rank": value.rank,
            "pre_rank": value.pre_rank, "round": round_ if round_ and round_ <= rounds else None,   # past the draft: none
            "projection": round(value.projection, 2), "buzz": buzz_line}


def sorted_board(board: Sequence[ReplayValue], sort: str, direction: str | None) -> list[ReplayValue]:
    """The board by `sort` (rank | adp | projection), unknowns last whatever the direction; ties keep draft order.
    direction None: asc for rank and adp, desc for projection."""
    if sort not in DRAFT_SORTS:
        raise ValueError(f"Unknown sort '{sort}'. Valid: {list(DRAFT_SORTS)}")
    key = DRAFT_SORTS[sort]
    descending = (direction or ("desc" if sort == "projection" else "asc")) == "desc"
    known = [v for v in board if key(v) is not None]
    return [*sorted(known, key=key, reverse=descending), *(v for v in board if key(v) is None)]


def draft(game: Game, position: str | None = None, search: str | None = None, limit: int = DEFAULT_PAGE,
          offset: int = 0, sort: str = "rank", direction: str | None = None) -> dict:
    """The draft room: picks so far, the order, the user's roster, and who is left (by `sorted_board`), all as
    known at the draft (week 0, or start_week in a league that jumped in)."""
    drafted = rs.draft_week(game.settings)
    state, settings, know = game.state, game.settings, game.know_at(drafted)
    wanted, teams = positions_for(position), {t.id: team_ref(game, t) for t in state.teams}
    drafting = replay_draft.on_the_clock(state, settings) is not None
    left = replay_draft.available(state, game.full_season.as_of(drafted)) if drafting else []
    found = [know.by_id[p.player_id] for p in left if p.position in wanted and name_matches(search, p.name)]
    board = sorted_board(found, sort, direction)
    overall = len(state.picks) + 1 if drafting else None
    mine = [pick.player_id for pick in state.picks if pick.team_id == game.user.id]
    return {
        **page(board, limit, offset, lambda value: _board_row(
            value, settings.teams, settings.rounds, buzz(game, value.player_id, drafted))), "phase": state.phase,
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
