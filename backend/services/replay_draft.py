"""Replay League: the snake draft and its CPU managers.

The draft is held in `state.week` (replay_state.draft_week): week 0, preseason, for a league that plays from
week 1, or week K for one that jumps in at week K. A CPU knows only `data.as_of(state.week)`: at week 0 the
preseason ADP, never a result or an in-season projection; at week K the results of weeks before K and the
tags and projections through K (a jump-in league's pool carries a current-market ADP, see replay_pool).
Its pick is the lowest `(adp + noise) x need x lean` among the best available players by ADP:

- noise: Normal(0, sigma x the team's chaos), sigma = the ADP's own spread (or 2 + 0.11 x adp);
- need: below 1 pulls a position earlier (an open starting spot), above 1 pushes it back (depth, backups);
- lean: the team's fixed taste for each position.

A player a hard tag rules out of the draft's first week (at a preseason draft week 1's IR, PUP or suspension,
known then: replay_pool's PRESEASON_TAGS; at a jump-in draft this week's tags) is drafted at his chart value
like anyone else: his ADP is the pick his chart (which carries the injury discount) is worth
(`replay_chart.chart_pick`), so a CPU waits on him as long as the trade market would (2021: Gus Edwards' ACL,
ADP 57), and a jump-in draft no longer skips injured players, whom the current market's ADP leaves out (a
superflex jump-in left Malik Willis, worth 674, on the wire).

Hard rules sit on top: a roster must always be able to end legal (every starting slot filled, K and DST
included), position caps, kickers and defenses only in the last rounds, no backup QB or TE before round
10 of 15, never a reach of more than a round of picks in rounds 1-4, a round and a half in rounds 5-9 and
25 picks later (never more than 25), and at least two bench backs and two bench receivers.
In a superflex league (replay_slots.two_qb: a SUPERFLEX slot or 2+ QB slots) a CPU drafts through
replay_chart.superflex_view (replay_superflex.view: RBs, WRs and TEs in the league-scoring order, each QB placed
among them where the real 2-QB list takes him, the fitted average without one), and counts the
SUPERFLEX slots as QB starters (so it drafts two QBs early and may back them up).
The user may pick anyone as long as the roster can still end legal. Players without an ADP come after
everyone who has one, by their own projection for the first week played (0 when a hard tag rules him out).

Every function takes a `State` and returns a new one. Randomness: `rng(seed, "teams")` for the managers,
`rng(seed, "draft", overall pick)` for each pick, so a reload can never re-roll a pick.
"""
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache

from services import replay_chart, replay_fans, replay_personas, replay_pool, replay_slots, replay_state as rs
from services.replay_pool import ALL_POSITIONS, PoolPlayer, SeasonData
from services.replay_state import Pick, Settings, State, Team
from services import replay_values as rv
from services.replay_values import lineup_holes, missing_slots, position_cap

log = logging.getLogger(__name__)

USER_TEAM_NAME = "Your Team"
CPU_TEAM_NAMES = (
    "Gridiron Goats", "Fourth and Long", "Blitz Brigade", "Hail Marys", "Red Zone Rangers", "Sunday Scaries",
    "End Zone Dancers", "Two-Minute Drill", "Pigskin Pirates", "The Audibles", "Goal Line Stand",
    "Monday Night Lights", "Touchdown Town", "Screen Pass Society", "Bye Week Blues", "The Checkdowns",
    "Waiver Wire Wizards", "Play Action Heroes", "Victory Formation", "Hard Count",
)
CHAOS_RANGE = (0.6, 1.4)                # how far a CPU strays from ADP (x replay_state.DRAFT_CHAOS_SCALE)
LEAN_RANGE = (0.90, 1.10)               # a CPU's taste per position; below 1 = takes it earlier
SIGMA_BASE, SIGMA_PER_PICK = 2.0, 0.11  # spread of a player's draft slot when his ADP carries none
MIN_DRAW = 0.5                          # a noisy ADP never drops below this (keeps the multipliers meaningful)
BAND_ENDS = (4, 9)                      # last round of the early and of the middle part of a draft
WINDOWS = (36, 60, 90)                  # how many of the best available (by ADP) a CPU looks at, per part
REACH_ROUNDS = (1.0, 1.5, None)         # furthest reach per part, in rounds of picks (late: REACH_LIMIT)
REACH_LIMIT = 25                        # never a player whose ADP is more than this many picks away
BENCH_FLOORS = {"RB": 2, "WR": 2}       # fewest bench players per position (4 RB and 4 WR by default)
BACKUP_GATE = (2, 3)                    # no second QB or TE before this share of the rounds (round 10 of 15)
OPEN_STARTER_NEED = (0.95, 0.85, 0.75)  # RB/WR with a starting spot still open, per part of the draft
STARTER_NEED_LATE = 0.7                 # QB1/TE1 still missing: 1.0 in round 1 down to this ...
STARTER_NEED_BY_ROUND = 9               # ... by this round
DEPTH_SLACK = 3                         # a team with this many bench RB (or WR) is deep there (tuned: 2 left
DEEP_NEED = 1.15                        # two teams in three with the minimum four backs)
BACKUP_NEED = 1.5                       # QB2/TE2 once the gate is open (tuned: about half the teams take each)


# ---------------- setting up ----------------

def _names(settings: Settings, order: Sequence[int], team_names: Sequence[str] | None) -> list[str]:
    """Team names by id: the caller's, or the user's default plus shuffled built-in CPU names."""
    if team_names is not None:
        if len(team_names) != settings.teams or not all(str(name).strip() for name in team_names):
            raise ValueError(f"A {settings.teams}-team league needs {settings.teams} team names.")
        return [str(name).strip() for name in team_names]
    cpu_names = (CPU_TEAM_NAMES[i] for i in order)
    return [USER_TEAM_NAME if team_id == settings.draft_slot else next(cpu_names)
            for team_id in range(1, settings.teams + 1)]


def new_game(settings: Settings, seed: int, team_names: Sequence[str] | None = None) -> State:
    """A league about to draft: teams 1..N in round-1 order, the user at `settings.draft_slot`.

    `team_names`, when given, names all N teams by id. The user's team has chaos 0 and no lean, which is
    what `autopick_user` drafts with. Each CPU gets its trading persona here, once (kept hidden from views),
    and a fan his favourite NFL team (replay_fans.pick_team)."""
    draw = rs.rng(seed, "teams")
    names = _names(settings, [int(i) for i in draw.permutation(len(CPU_TEAM_NAMES))], team_names)
    chaos = draw.uniform(*CHAOS_RANGE, settings.teams)
    lean = draw.uniform(*LEAN_RANGE, (settings.teams, len(ALL_POSITIONS)))
    personas = replay_personas.assign(seed, [t for t in range(1, settings.teams + 1) if t != settings.draft_slot])
    teams = []
    for index, name in enumerate(names):
        is_user = index + 1 == settings.draft_slot
        taste = {position: 1.0 if is_user else round(float(lean[index, n]), 3)
                 for n, position in enumerate(ALL_POSITIONS)}
        persona = personas.get(index + 1, "balanced")
        teams.append(Team(index + 1, name, is_user, (), 0.0 if is_user else round(float(chaos[index]), 3), taste,
                          persona=persona, hype=replay_personas.hype_for(persona),
                          fan_of=replay_fans.pick_team(seed, index + 1) if persona == "fan" and not is_user else None))
    ids = tuple(team.id for team in teams)
    return State(phase="draft", week=rs.draft_week(settings), teams=tuple(teams), draft_order=ids, picks=(),
                 schedule=(), results=(),
                 waiver_order=(), claims=(), locked=(), lineup=None, offers=(), events=(), traded={})


# ---------------- whose pick, and who is left ----------------

def _total_picks(state: State, settings: Settings) -> int:
    return len(state.draft_order) * settings.rounds


def _slot(state: State, overall: int) -> tuple[int, int]:
    """(round, team id) of the 1-based overall pick in a snake draft."""
    round_index, spot = divmod(overall - 1, len(state.draft_order))
    order = state.draft_order[::-1] if round_index % 2 else state.draft_order
    return round_index + 1, order[spot]


def on_the_clock(state: State, settings: Settings) -> int | None:
    """The team id whose pick it is; None when the draft is done."""
    if state.phase != "draft" or len(state.picks) >= _total_picks(state, settings):
        return None
    return _slot(state, len(state.picks) + 1)[1]


@lru_cache(maxsize=8)
def _board(known: SeasonData) -> tuple[PoolPlayer, ...]:
    """The whole pool in draft order: by ADP, then players without one by their own projection for the first
    week played (week 1 at a preseason draft, week K at a week-K one; 0 under a hard tag)."""
    week = max(1, known.known_before or 0)

    def projected(player_id: int) -> float:
        tag = known.injuries.get((player_id, week))
        return 0.0 if tag is not None and tag.hard else known.projection(player_id, week)

    def key(player: PoolPlayer) -> tuple:
        if player.adp is not None:
            return False, player.adp, 0.0, player.player_id
        return True, 0.0, -projected(player.player_id), player.player_id
    return tuple(sorted(known.players.values(), key=key))


replay_pool.EVICT_HOOKS.append(_board.cache_clear)


def available(state: State, data: SeasonData) -> list[PoolPlayer]:
    """Undrafted players, best ADP first; those without an ADP last, by projection (as known at the draft)."""
    taken = rs.rostered(state)
    return [player for player in _board(data.as_of(state.week)) if player.player_id not in taken]


# ---------------- what a CPU wants ----------------

def _band(round_: int) -> int:
    """0 = early rounds, 1 = middle, 2 = late."""
    return sum(round_ > end for end in BAND_ENDS)


def _caps(settings: Settings) -> dict[str, int]:
    """Most players a CPU takes per position: QB 2, RB 6, WR 7, TE 2, K 1, DST 1 on the default roster.
    A deeper bench than the caps can fill goes to backs and receivers."""
    caps = {position: position_cap(position, settings) for position in ALL_POSITIONS}
    # shortcut: waivers and trades (position_cap) do not share this spare; it only exists for a bench above
    # 10, move it into position_cap when bench becomes configurable
    spare = max(0, settings.rounds - sum(caps.values()))
    return {**caps, "RB": caps["RB"] + spare // 2, "WR": caps["WR"] + spare - spare // 2}


def _starting(position: str, settings: Settings) -> int:
    """Starting slots a CPU fills with this position (replay_slots.own_slots)."""
    return replay_slots.own_slots(position, settings.starters)


def _floors(settings: Settings) -> dict[str, int]:
    """Fewest players a CPU ends with per position: its starters plus two bench backs and receivers."""
    return {position: _starting(position, settings) + BENCH_FLOORS.get(position, 0) for position in ALL_POSITIONS}


def _gated(position: str, counts: Counter, round_: int, settings: Settings) -> bool:
    """Too early for this position: K and DST before the last rounds, a backup QB or TE before round 10."""
    starters = settings.starters
    if position in ("K", "DST"):
        return round_ <= settings.rounds - starters.get("K", 0) - starters.get("DST", 0)
    if position in ("QB", "TE") and counts[position] >= _starting(position, settings):
        share, of = BACKUP_GATE
        return round_ < -(-settings.rounds * share // of)             # ceiling
    return False


def _positions(mine: Sequence[str], round_: int, settings: Settings) -> list[str]:
    """Positions the CPU on the clock may take, strictest rule that leaves a choice first.

    Always: the roster can still end legal. Then caps and round gates. Then, once the picks left only
    just cover what the roster still lacks, nothing but those positions (forced fill)."""
    counts, left_after = Counter(mine), settings.rounds - len(mine) - 1
    legal = list(ALL_POSITIONS)                       # a pick never opens a slot, so usually every position
    if lineup_holes(mine, settings) > left_after:
        legal = [p for p in ALL_POSITIONS if lineup_holes([*mine, p], settings) <= left_after]
    caps, floors = _caps(settings), _floors(settings)
    wanted = [p for p in legal if counts[p] < caps[p] and not _gated(p, counts, round_, settings)] or legal
    lacking = sum(max(0, floors[p] - counts[p]) for p in ALL_POSITIONS)
    if lacking > left_after:
        return [p for p in wanted if counts[p] < floors[p]] or wanted
    return wanted


def _need(position: str, counts: Counter, round_: int, settings: Settings) -> float:
    """Multiplier on a player's ADP: below 1 the team wants the position sooner, above 1 later."""
    have, starting = counts[position], _starting(position, settings)
    if position in ("RB", "WR"):
        if have < starting:
            return OPEN_STARTER_NEED[_band(round_)]
        return DEEP_NEED if have >= starting + DEPTH_SLACK else 1.0
    if position in ("QB", "TE"):
        if have >= starting:
            return BACKUP_NEED
        progress = min(1.0, (round_ - 1) / (STARTER_NEED_BY_ROUND - 1))
        return 1.0 - (1.0 - STARTER_NEED_LATE) * progress
    return 1.0


def _candidates(state: State, known: SeasonData, allowed: Sequence[str], round_: int,
                overall: int, team: Team | None = None,
                priced: Mapping[int, PoolPlayer] | None = None) -> tuple[list[PoolPlayer], bool]:
    """(players to choose from, whether they have an ADP). Within reach when anyone is; with no ADP player
    left at an allowed position, the one with the best preseason projection. A fan (`team`) skips his rival's
    players while anyone else fits (replay_fans). `priced`: players whose ADP is their chart's pick (`_out_priced`)."""
    taken = rs.rostered(state)
    pool = [(priced or {}).get(p.player_id, p) for p in _board(known) if p.position in allowed and p.player_id not in taken]
    if priced:
        pool.sort(key=lambda p: (p.adp is None, p.adp or 0.0))       # stable: the board's order otherwise
    if team is not None and team.fan_of:
        pool = [p for p in pool if not replay_fans.banned(team, known.team_at(p.player_id, state.week))] or pool
    if not pool:
        raise ValueError("No player is left who fits this roster.")
    ranked = [p for p in pool if p.adp is not None][:WINDOWS[_band(round_)]]
    if not ranked:
        return pool[:1], False
    rounds = REACH_ROUNDS[_band(round_)]
    reach = REACH_LIMIT if rounds is None else min(REACH_LIMIT, round(rounds * len(state.draft_order)))
    return [p for p in ranked if p.adp <= overall + reach] or ranked, True


def _scores(team: Team, players: Sequence[PoolPlayer], counts: Counter, round_: int, settings: Settings,
            noise: Sequence[float]) -> list[float]:
    chaos = 0.0 if team.is_user else team.chaos * rs.DRAFT_CHAOS_SCALE[settings.difficulty]
    scores = []
    for player, luck in zip(players, noise):
        sigma = player.adp_stdev or SIGMA_BASE + SIGMA_PER_PICK * player.adp
        draw = max(MIN_DRAW, player.adp + float(luck) * sigma * chaos)
        scores.append(draw * _need(player.position, counts, round_, settings) * team.lean.get(player.position, 1.0))
    return scores


def _out_priced(state: State, settings: Settings, known: SeasonData, seed: int) -> dict[int, PoolPlayer]:
    """{id: him with his chart's pick as ADP} for every player a hard tag rules out of the draft's first week."""
    week = max(1, state.week)
    out = [p for p in known.players.values() if (tag := known.injuries.get((p.player_id, week))) is not None and tag.hard]
    if not out:
        return {}
    know = rv.knowledge(known, rv.league_settings(settings), state.week, settings.end_week, seed,
                        drafted=state.week)
    return {p.player_id: replace(p, adp=replay_chart.chart_pick(know.by_id[p.player_id].chart), adp_stdev=None)
            for p in out if p.player_id in know.by_id}


def cpu_pick(state: State, settings: Settings, data: SeasonData, seed: int) -> int:
    """The player id the team on the clock takes with CPU logic (the user's team: no noise, no lean).
    Reads only `data.as_of(state.week)` (the draft's week)."""
    team_id = on_the_clock(state, settings)
    if team_id is None:
        raise ValueError("The draft is over.")
    team, known, overall = rs.team(state, team_id), data.as_of(state.week), len(state.picks) + 1
    priced = _out_priced(state, settings, known, seed)
    if replay_slots.two_qb(settings.starters):
        known = replay_chart.superflex_view(known)
    round_ = _slot(state, overall)[0]
    mine = [known.players[pid].position for pid in team.roster]
    players, ranked = _candidates(state, known, _positions(mine, round_, settings), round_, overall, team, priced)
    if not ranked:
        return players[0].player_id
    noise = rs.rng(seed, "draft", overall).standard_normal(len(players))
    scores = _scores(team, players, Counter(mine), round_, settings, noise)
    return players[min(range(len(players)), key=lambda i: (scores[i], i))].player_id


# ---------------- making picks ----------------

def _picker(state: State, on_clock: Team, team_id: int | None) -> None:
    """ValueError unless the team making the pick (None = the user's) is the one on the clock."""
    if team_id is None:
        if rs.user_team(state).id != on_clock.id:
            raise ValueError(f"It is not your turn to pick: {on_clock.name} is on the clock.")
    elif team_id != on_clock.id:
        raise ValueError(f"{rs.team(state, team_id).name} is not on the clock: {on_clock.name} is.")


def make_pick(state: State, settings: Settings, data: SeasonData, player_id: int,
              team_id: int | None = None) -> State:
    """The team on the clock drafts that player. `team_id` says who is picking: None is the user.

    ValueError when the draft is over, it is another team's turn, the player is unknown or taken, or the
    pick would leave more starting slots open than the team has picks left. The last pick leaves the phase
    at "draft" with nobody on the clock: `run_cpu_picks` (or `autopick_user`) then finishes the draft."""
    on_clock = on_the_clock(state, settings)
    if on_clock is None:
        raise ValueError("The draft is over.")
    team = rs.team(state, on_clock)
    _picker(state, team, team_id)
    player = data.players.get(player_id)
    if player is None:
        raise ValueError(f"There is no player {player_id} in the {settings.season} player pool.")
    if player_id in rs.rostered(state):
        raise ValueError(f"{player.name} has already been drafted.")
    positions = [*(data.players[pid].position for pid in team.roster), player.position]
    left, still_open = settings.rounds - len(positions), missing_slots(positions, settings)
    if len(still_open) > left:
        raise ValueError(f"{team.name} can't draft {player.name}: {len(still_open)} starting spots would still "
                         f"be open ({', '.join(still_open)}) with only {left} picks left.")
    overall = len(state.picks) + 1
    round_ = _slot(state, overall)[0]
    drafted = rs.with_team(state, replace(team, roster=(*team.roster, player_id)))
    drafted = replace(drafted, picks=(*state.picks, Pick(overall, round_, team.id, player_id)))
    text = f"{team.name} drafted {player.name} ({player.position}) in round {round_}, pick {overall} overall"
    return rs.add_event(drafted, "draft", team.id, text, (player_id,))


def finish_draft(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """After the last pick: the regular-season schedule (weeks start_week..regular_season_weeks), waiver
    priority in reverse draft order, phase "open", week start_week. (`data` is not read; it is in the
    signature every draft step shares.)"""
    if state.phase != "draft" or len(state.picks) < _total_picks(state, settings):
        raise ValueError("The draft can only be finished once, after its last pick.")
    weeks = settings.regular_season_weeks - settings.start_week + 1
    schedule = rs.round_robin(state.draft_order, weeks, seed)
    log.info("Replay draft finished: %d picks, %d weeks scheduled", len(state.picks), len(schedule))
    return replace(state, schedule=schedule, waiver_order=state.draft_order[::-1], phase="open",
                   week=settings.start_week)


def _finished(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    done = state.phase == "draft" and len(state.picks) >= _total_picks(state, settings)
    return finish_draft(state, settings, data, seed) if done else state


def run_cpu_picks(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """CPUs pick until the user is on the clock or the draft ends; a completed draft is finished."""
    while (team_id := on_the_clock(state, settings)) is not None and not rs.team(state, team_id).is_user:
        state = make_pick(state, settings, data, cpu_pick(state, settings, data, seed), team_id)
    return _finished(state, settings, data, seed)


def autopick_user(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """The user's pick made with CPU logic and no noise. Finishes the draft when it was the last pick."""
    if on_the_clock(state, settings) != rs.user_team(state).id:
        raise ValueError("It is not your turn to pick.")
    return _finished(make_pick(state, settings, data, cpu_pick(state, settings, data, seed)), settings, data, seed)
