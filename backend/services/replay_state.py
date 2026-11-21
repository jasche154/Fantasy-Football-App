"""Replay League: a game's settings and state as frozen data, their JSON form, seeded randomness and the store.

Everything here is immutable. Engine modules take a `State` and return a new one (`dataclasses.replace`);
mappings are read-only (`MappingProxyType`) and lists become tuples when a record is built, so a state can
be shared and cached safely. `to_json` / `state_from_json` round-trip exactly (tuples, int keys, None);
because the mappings are proxies, use them rather than `dataclasses.asdict`, `copy.deepcopy` or pickle.
"""
import json
import types
import typing
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from functools import lru_cache
from numbers import Integral, Real
from types import MappingProxyType

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import ReplayLeague
from database.models.base import utc_now
from services.scoring import POINTS_PER_RECEPTION

SLOTS = ("QB", "RB", "WR", "TE", "FLEX", "K", "DST")
DEFAULT_STARTERS = MappingProxyType({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1})
WAIVER_MODES = ("wednesday", "faab", "none")
FAAB_BUDGET = 100                         # whole dollars per team per season ("faab" waivers)
PLAYOFF_WEEKS = 2
VALID_TEAMS = (8, 10, 12, 14)
USER_TRADE_MULTIPLE = MappingProxyType({"normal": 1.00, "hard": 1.08})   # value a CPU needs back from the USER
DRAFT_CHAOS_SCALE = MappingProxyType({"normal": 1.0, "hard": 0.5})       # on a CPU's Team.chaos in the draft
DIFFICULTIES = tuple(USER_TRADE_MULTIPLE)
RETRADE_LOCK_WEEKS = 2                    # a traded player can't be traded again for this many weeks
STATE_VERSION = 1                         # the "v" in a saved state's JSON
Matchups = tuple[tuple[int, int], ...]    # one week: ((home team id, away team id), ...)
_FROZEN = (int, float, str, bool, type(None), tuple, MappingProxyType)


def _freeze(value):
    """Read-only copy: a mapping becomes a proxy over a private dict, a list a tuple (recursively). Tuples and
    proxies are taken as already frozen (cheap `replace`): build nested data from tuples, not lists in tuples."""
    if type(value) in _FROZEN:
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class _Record:
    """Base of every state dataclass: freezes its containers once, at construction."""

    def __post_init__(self) -> None:
        for spec in fields(self):
            object.__setattr__(self, spec.name, _freeze(getattr(self, spec.name)))


@dataclass(frozen=True)
class Settings(_Record):
    season: int
    teams: int = 12
    scoring: str = "half"
    starters: Mapping[str, int] = field(default_factory=lambda: DEFAULT_STARTERS, hash=False)
    bench: int = 6
    draft_slot: int = 1                      # the user's first-round pick, 1-based
    waiver_mode: str = "wednesday"           # "wednesday" | "faab" | "none" (always free agency)
    regular_season_weeks: int = 14
    playoff_teams: int = 4                   # playoffs are the 2 weeks after the regular season
    trade_deadline_week: int = 11            # the last week traded in, plus its waiver phase (`trades_open`)
    season_hidden: bool = False              # reserved; not used in v1
    difficulty: str = "normal"               # "normal" | "hard": see USER_TRADE_MULTIPLE, DRAFT_CHAOS_SCALE

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate(self)

    @property
    def roster_size(self) -> int:
        return sum(self.starters.values()) + self.bench

    rounds = roster_size                     # the draft: one pick per roster spot

    @property
    def end_week(self) -> int:
        return self.regular_season_weeks + PLAYOFF_WEEKS


def _validate(s: Settings) -> None:
    unknown = sorted(set(s.starters) - set(SLOTS))
    checks = (
        (s.scoring in POINTS_PER_RECEPTION, f"Unknown scoring '{s.scoring}'. Valid: {sorted(POINTS_PER_RECEPTION)}"),
        (s.waiver_mode in WAIVER_MODES, f"Unknown waiver mode '{s.waiver_mode}'. Valid: {list(WAIVER_MODES)}"),
        (s.difficulty in DIFFICULTIES, f"Unknown difficulty '{s.difficulty}'. Valid: {list(DIFFICULTIES)}"),
        (s.teams in VALID_TEAMS, f"A league has {', '.join(map(str, VALID_TEAMS[:-1]))} or {VALID_TEAMS[-1]} teams."),
        (1 <= s.draft_slot <= s.teams, f"Draft slot must be between 1 and {s.teams}."),
        (2 <= s.playoff_teams <= s.teams, f"Playoff teams must be between 2 and {s.teams}."),
        (not unknown, f"Unknown lineup slots {unknown}. Valid: {list(SLOTS)}"),
        (s.bench >= 0 and min(s.starters.values(), default=0) >= 0, "Starter and bench counts can't be negative."),
        (s.regular_season_weeks >= 1, "The regular season needs at least one week."),
    )
    for passed, message in checks:
        if not passed:
            raise ValueError(message)


@dataclass(frozen=True)
class Team(_Record):
    id: int                                   # 1..N = the round-1 draft slot; the user's is settings.draft_slot
    name: str
    is_user: bool
    roster: tuple[int, ...]
    chaos: float                              # how far a CPU strays from ADP
    lean: Mapping[str, float] = field(hash=False)      # position -> multiplier on a CPU's draft score
    ir: tuple[int, ...] = ()                  # players in the IR slot (not in `roster`); see replay_ir
    persona: str = "balanced"                # a CPU's trading style (replay_personas.PERSONAS)


@dataclass(frozen=True)
class Pick(_Record):
    overall: int
    round: int
    team_id: int
    player_id: int


@dataclass(frozen=True)
class WeekResult(_Record):
    week: int
    matchups: Matchups
    scores: Mapping[int, float] = field(hash=False)                              # team id -> points
    starters: Mapping[int, Mapping[str, tuple[int, ...]]] = field(hash=False)    # team id -> slot -> players
    points: Mapping[int, float] = field(hash=False)        # player id -> real points, every team's starters only
    playoff: bool = False


@dataclass(frozen=True)
class Claim(_Record):
    id: int
    team_id: int
    add: int
    drop: int | None
    bid: int = 0                              # whole dollars; always 0 outside FAAB


@dataclass(frozen=True)
class Offer(_Record):                         # every executed trade (CPU-CPU too) and CPU offer to the user is kept
    id: int
    week: int
    from_team: int
    to_team: int
    give: tuple[int, ...]                     # what from_team sends
    get: tuple[int, ...]
    status: str                               # "pending" | "accepted" | "declined" | "expired" | "countered"
    counter_to: int | None = None             # the Offer id this one answers with a counter


@dataclass(frozen=True)
class Event(_Record):
    id: int
    week: int
    kind: str          # "draft" | "add" | "drop" | "claim" | "ir" | "trade" | "result" | "playoffs" | "champion"
    team_id: int | None
    text: str
    player_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class State(_Record):
    phase: str                                # "draft" | "waivers" | "open" | "complete"
    week: int                                 # the next week to be played; 0 during the draft
    teams: tuple[Team, ...]
    draft_order: tuple[int, ...]              # team ids in round-1 order (snake reverses on even rounds)
    picks: tuple[Pick, ...]
    schedule: tuple[Matchups, ...]            # regular season, index 0 = week 1
    results: tuple[WeekResult, ...]
    waiver_order: tuple[int, ...]             # rolling priority, first = next claim wins
    claims: tuple[Claim, ...]                 # the user's pending claims, in preference order
    locked: tuple[int, ...]                   # dropped players: on waivers until the next start of week
    lineup: Mapping[str, tuple[int, ...]] | None = field(hash=False)     # the user's manual starters; None = auto
    offers: tuple[Offer, ...]
    events: tuple[Event, ...]
    traded: Mapping[int, int] = field(hash=False)          # player id -> week he was last traded
    champion: int | None = None
    next_id: int = 1                          # the next Claim / Offer / Event id
    faab_spent: Mapping[int, int] = field(default_factory=dict, hash=False)   # team id -> FAAB dollars spent


# ---------------- JSON ----------------

def _encode(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {spec.name: _encode(getattr(value, spec.name)) for spec in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Integral):           # numpy ints included
        return int(value)
    if isinstance(value, Real):
        return float(value)
    raise TypeError(f"Can't store a {type(value).__name__} in replay state.")


def to_json(obj) -> str:
    """Compact JSON for a Settings or State (which carries "v": STATE_VERSION). NaN and infinity: ValueError."""
    payload = {"v": STATE_VERSION, **_encode(obj)} if isinstance(obj, State) else _encode(obj)
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


_hints = lru_cache(maxsize=None)(typing.get_type_hints)


def _decode(kind, value):
    """Rebuild `value` (parsed JSON) as the annotated type `kind`."""
    if value is None:
        return None
    origin, args = typing.get_origin(kind), typing.get_args(kind)
    if origin in (typing.Union, types.UnionType):
        return _decode(next(arg for arg in args if arg is not type(None)), value)
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in value)
        return tuple(_decode(arg, item) for arg, item in zip(args, value, strict=True))
    if origin is not None and issubclass(origin, Mapping):
        key_kind, item_kind = args
        return {key_kind(key): _decode(item_kind, item) for key, item in value.items()}
    if is_dataclass(kind):
        hints = _hints(kind)
        return kind(**{spec.name: _decode(hints[spec.name], value[spec.name])
                       for spec in fields(kind) if spec.name in value})      # a missing key keeps its default
    return kind(value)


def _from_json(kind: type, text: str, version: int | None = None):
    try:
        saved = json.loads(text)
        found = saved.get("v") if version is not None else None
        if found == version:
            return _decode(kind, saved)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Not a valid replay {kind.__name__.lower()}: {type(exc).__name__}") from exc
    raise ValueError(f"This replay league was saved in format version {found}; this app reads version {version}.")


def settings_from_json(text: str) -> Settings:
    return _from_json(Settings, text)


def state_from_json(text: str) -> State:
    return _from_json(State, text, STATE_VERSION)


# ---------------- randomness ----------------

def _word(value: int | str) -> int:
    """A stable 32-bit word for one seed part (Python's own hash() is salted per process)."""
    if isinstance(value, bool) or not isinstance(value, (str, Integral)):
        raise ValueError(f"Random streams are named by ints and strings, not {type(value).__name__}.")
    return zlib.crc32((f"s:{value}" if isinstance(value, str) else f"i:{int(value)}").encode())


def rng(seed: int, *stream: int | str) -> np.random.Generator:
    """The generator for one decision, e.g. rng(seed, "draft", pick): same inputs, same numbers, in any process."""
    return np.random.default_rng([_word(part) for part in (seed, *stream)])


# ---------------- reading and updating a state ----------------

def team(state: State, team_id: int) -> Team:
    found = next((t for t in state.teams if t.id == team_id), None)
    if found is None:
        raise ValueError(f"No team {team_id} in this league.")
    return found


def user_team(state: State) -> Team:
    found = next((t for t in state.teams if t.is_user), None)
    if found is None:
        raise ValueError("This league has no user team.")
    return found


def held(team: Team) -> tuple[int, ...]:
    """Everyone the team owns: its active roster, then its IR."""
    return (*team.roster, *team.ir)


def rostered(state: State) -> frozenset[int]:
    return frozenset(pid for t in state.teams for pid in held(t))


def with_team(state: State, team: Team) -> State:
    """The state with that team replaced (matched by id)."""
    if all(t.id != team.id for t in state.teams):
        raise ValueError(f"No team {team.id} in this league.")
    return replace(state, teams=tuple(team if t.id == team.id else t for t in state.teams))


def add_event(state: State, kind: str, team_id: int | None, text: str, player_ids: Sequence[int] = ()) -> State:
    event = Event(id=state.next_id, week=state.week, kind=kind, team_id=team_id, text=text,
                  player_ids=tuple(player_ids))
    return replace(state, events=(*state.events, event), next_id=state.next_id + 1)


def trades_open(state: State, settings: Settings) -> bool:
    """Can trades be made now? Through the deadline week, plus the waiver phase right after its games
    (week = deadline + 1, phase "waivers"); with no waivers it closes once the deadline week is played."""
    deadline = settings.trade_deadline_week
    return state.week <= deadline or (state.week == deadline + 1 and state.phase == "waivers"
                                      and settings.waiver_mode != "none")


def faab_left(state: State, team_id: int) -> int:
    return FAAB_BUDGET - state.faab_spent.get(team_id, 0)


def has_traded_this_week(state: State, team_id: int) -> bool:
    """The team is on either side of an accepted Offer made this week."""
    return any(o.status == "accepted" and o.week == state.week and team_id in (o.from_team, o.to_team)
               for o in state.offers)


def retrade_locked(state: State, player_id: int) -> bool:
    """He was traded less than RETRADE_LOCK_WEEKS weeks ago, so he can't be traded again yet."""
    return player_id in state.traded and state.week - state.traded[player_id] < RETRADE_LOCK_WEEKS


def release(state: State, settings: Settings, team_id: int, player_id: int, name: str | None = None,
            log: bool = True) -> State:
    """THE way a player leaves a roster without a trade partner: off the roster, onto waivers (`state.locked`,
    in both waiver modes) until the next start of week, with a "drop" event (`name` is for its text only).
    log=False skips the event, for a caller that logs one event for the whole move (add or claim)."""
    owner = team(state, team_id)
    if player_id not in held(owner):
        raise ValueError(f"{owner.name} can't drop a player who isn't on the roster.")
    smaller = replace(owner, roster=tuple(pid for pid in owner.roster if pid != player_id),
                      ir=tuple(pid for pid in owner.ir if pid != player_id))
    dropped = replace(with_team(state, smaller), locked=(*(p for p in state.locked if p != player_id), player_id))
    if not log:
        return dropped
    return add_event(dropped, "drop", team_id, f"{owner.name} dropped {name or 'a player'}", (player_id,))


def _round(order: Sequence[int], turn: int, flip: int) -> Matchups:
    """One round of the circle method: order[0] stays, the rest rotate; home and away alternate."""
    rest = order[1:]
    circle = [order[0], *rest[turn:], *rest[:turn]]
    pairs = [(circle[i], circle[-1 - i]) for i in range(len(circle) // 2)]
    return tuple(pair if (i + turn + flip) % 2 else pair[::-1] for i, pair in enumerate(pairs))


def round_robin(team_ids: Sequence[int], weeks: int, seed: int) -> tuple[Matchups, ...]:
    """A regular season: every team plays exactly once a week and any two teams meet as evenly as the weeks
    allow (their counts never differ by more than one). The same seed gives the same schedule."""
    ids = [int(team_id) for team_id in team_ids]
    if len(ids) < 2 or len(ids) % 2 or len(set(ids)) != len(ids) or weeks < 1:
        raise ValueError("A schedule needs an even number of different teams and at least one week.")
    shuffle = rng(seed, "schedule")
    order = [ids[i] for i in shuffle.permutation(len(ids))]
    turns = [int(turn) for turn in shuffle.permutation(len(ids) - 1)]       # the order the rounds are played in
    return tuple(_round(order, turns[week % len(turns)], week // len(turns)) for week in range(weeks))


def standings(state: State) -> list[dict]:
    """Regular-season table, best first (playoff seeds): wins (a tie = half), points for, the lower team id."""
    rows = {t.id: {"team_id": t.id, "name": t.name, "wins": 0, "losses": 0, "ties": 0,
                   "points_for": 0.0, "points_against": 0.0} for t in state.teams}
    for result in (r for r in state.results if not r.playoff):
        for home, away in result.matchups:
            for mine, theirs in ((home, away), (away, home)):
                scored, allowed = result.scores.get(mine, 0.0), result.scores.get(theirs, 0.0)
                row = rows[mine]
                row["points_for"] += scored
                row["points_against"] += allowed
                row["wins" if scored > allowed else "losses" if scored < allowed else "ties"] += 1
    ordered = sorted(rows.values(), key=lambda r: (-(r["wins"] + 0.5 * r["ties"]), -r["points_for"], r["team_id"]))
    return [{**row, "points_for": round(row["points_for"], 2), "points_against": round(row["points_against"], 2),
             "rank": rank} for rank, row in enumerate(ordered, start=1)]


# ---------------- store (the only database code here) ----------------

class ReplayNotFound(LookupError):
    pass


def create(session: Session, name: str, settings: Settings, state: State, seed: int) -> ReplayLeague:
    row = ReplayLeague(name=name, season=settings.season, seed=seed, settings=to_json(settings), state=to_json(state))
    session.add(row)
    session.commit()
    return row


def _row(session: Session, league_id: int) -> ReplayLeague:
    row = session.get(ReplayLeague, league_id)
    if row is None:
        raise ReplayNotFound(f"No replay league {league_id}.")
    return row


def load(session: Session, league_id: int) -> tuple[ReplayLeague, Settings, State]:
    row = _row(session, league_id)
    return row, settings_from_json(row.settings), state_from_json(row.state)


def save(session: Session, row: ReplayLeague, state: State) -> None:
    row.state = to_json(state)
    row.updated_at = utc_now()
    session.commit()


def list_leagues(session: Session) -> list[ReplayLeague]:
    """Saved games, most recently played first."""
    return list(session.scalars(select(ReplayLeague).order_by(ReplayLeague.updated_at.desc(), ReplayLeague.id.desc())))


def delete(session: Session, league_id: int) -> None:
    session.delete(_row(session, league_id))
    session.commit()
