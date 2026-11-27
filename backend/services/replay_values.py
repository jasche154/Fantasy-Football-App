"""Replay League: what everyone "knows" before week W, with no hindsight.

Inputs are only things that existed before that week's games: preseason ADP and the pre-game projections
for weeks 1..W (`knowledge` reads the season through `SeasonData.as_of(week)`, so it cannot see more). Real
results are used only for weeks already played: the `games` / `ppg` display fields, and `recent_points` /
`last_game_week`, which the CPU's waiver claims read (services/replay_claim_ai.py, the "hot" route).

- prior_ppg: rank players by ADP within a position; the Nth gets the Nth highest WEEK-1 projection at that
  position. Without ADP: his own week-1 projection. Week 0 (the draft) uses the prior only, also as the
  `projection` shown, so the draft board holds nothing the ADP did not know.
- ros_ppg (points per game from here on, IF HEALTHY): the weighted mean (4, 3, 2, 1, newest first) of his
  last four non-zero projections from any week up to W. The prior fills only the slots that do not exist
  yet, so a bye or an absence never pulls the prior back in.
- Out streak k: consecutive non-bye weeks ending at W with no projection. He is expected to miss a share of
  his remaining games (OUT_SHARE: 45% after one week out, 90% after six), never all of them.
  availability = the share he is expected to play.
- healthy_value = max(0, ros_ppg - replacement) x his team's games left; trade_value = healthy_value x
  availability. Kickers and defenses have neither. These drive waivers and lineups.
- Trade chart (services/replay_chart.py): `chart` on a 0-1000 rank-based scale from expert ranks and the
  model, after injury news (services/replay_news.py). rank / pos_rank sort by it.
  A hard-tagged player with a `returns` entry gets a news report; his availability comes from it.
- Injury tags (week >= 1; at the draft, week 1's preseason IR, PUP, SUS and EXE): a hard tag (O, IR, PUP, SUS, EXE) means
  out, and that week's projection counts as 0.
  Tagged IR or PUP, he is expected to miss at least IR_OUT_SHARE of his remaining games. Q and D change nothing.

`Knowledge` and `ReplayValue` duck-type `valuation.Valuation` / `PlayerValue` for services/lineup_gain.py and
services/trade.py. One difference matters: PlayerValue.ros_ppg is already discounted by availability, a
ReplayValue's is not, so always build the projector with `projector(know)` (a `ReplayProjector`).
"""
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType, SimpleNamespace

from services import (
    lineup_gain, my_team as mt, replay_chart, replay_chart_injury, replay_slots, replay_state, replay_superflex,
)
from services.league import LeagueSettings, replacement_levels
from services.replay_news import Report
from services.replay_pool import PRESEASON_TAGS, SKILL, Injury, PoolPlayer, SeasonData
from services.schedule_strength import NEUTRAL, DefenseStrength, Schedule

RECENCY_WEIGHTS = (4, 3, 2, 1)                        # newest first: the projections (or priors) behind ros_ppg
OUT_SHARE = {1: 0.45, 2: 0.55, 3: 0.60, 4: 0.65, 5: 0.75}    # share of remaining games missed, by weeks out so far
OUT_SHARE_LONG = 0.90                                 # six or more weeks out (measured, RB/WR/TE 2021-25)
IR_OUT_SHARE = replay_chart_injury.IR_OUT_SHARE      # least share missed when tagged IR or PUP (measured 2018-25)
FLEX_POSITIONS = replay_slots.FLEX_POSITIONS
eligible = replay_slots.eligible                      # slot -> the positions that can play there
BACKUPS = {"QB": 1, "RB": 3, "WR": 4, "TE": 1}        # most a CPU carries behind its starters (no spare K or DST)
RECENT_GAMES = 5                                      # recent_points: his last games before this week (the chart's form window;
                                                      # other readers slice their own: replay_claim_ai, replay_mood)


@dataclass(frozen=True)
class ReplayValue:
    player_id: int
    name: str
    position: str
    team: str | None
    ros_ppg: float                 # expected points per game from here on, if healthy
    projection: float              # this week's pre-game projection (0 on a bye or when out)
    vorp_per_game: float
    trade_value: float             # healthy_value x availability. K and DST: always 0.0
    availability: float = 1.0      # share of his remaining games he is expected to play
    expected_missed: float = 0.0   # games; what lineup_gain's Projector sits him out for
    role_share: float = 1.0
    games: int = 0                 # real games before this week (display only)
    ppg: float = 0.0               # real points per game before this week (display only)
    rank: int = 0
    pos_rank: str = ""
    adp: float | None = None
    out: bool = False              # no projection this week and not on a bye
    healthy_value: float = 0.0     # vorp_per_game x games_left, no injury discount
    games_left: int = 0            # his team's games from this week to the league's last week
    last_projection: float = 0.0   # his most recent non-zero pre-game projection
    injury: Injury | None = None   # this week's tag; at week 0 only a preseason IR, PUP or SUS
    recent_points: tuple[float, ...] = ()   # real points of his last RECENT_GAMES games before this week, newest first
    last_game_week: int = 0        # the week of his last game before this week (0 = none)
    chart: float = 0.0             # 0-1000 trade chart value after injury (replay_chart)
    healthy_chart: float = 0.0     # chart value if healthy
    pre_chart: float = 0.0         # chart value at draft time
    expert_rank: int | None = None # this week's expert position rank (None = not ranked)
    pre_rank: int | None = None    # overall preseason rank: expert interleaved on ADP, else ADP order
    star: float = 0.0              # 0-1, replay_chart.star(pre_rank)
    form_chart: float = 0.0        # signed chart points from his recent real games
    news: Report | None = None     # injury report while he is out (replay_news)
    shock_chart: float = 0.0       # signed chart points from an opportunity shock (replay_shocks)
    shock: str | None = None       # its kind: teammate_out, teammate_left, starter_back, role_won, role_lost
    hype_reason: str | None = None  # why his role jumped (replay_shocks.reason), when a shock lifts him STAR_MIN+
    fill_in: float | None = None   # a fill-in QB: the share of his team's games left he is expected to start


@dataclass(frozen=True, eq=False)
class Knowledge:
    season: int
    week: int
    league: LeagueSettings
    values: tuple[ReplayValue, ...]                  # best first
    by_id: Mapping[int, ReplayValue]
    replacement: Mapping[str, float]
    schedule: Schedule
    defense: DefenseStrength                         # always neutral: no matchup adjustment in replay
    weeks_remaining: int
    through_week: int
    rank_source: str = "model"                       # "experts" | "model": what this week's chart leans on


class ReplayProjector(lineup_gain.Projector):
    """lineup_gain.Projector for ReplayValues, whose ros_ppg is already the healthy rate."""

    def healthy_rate(self, value) -> float:
        return value.ros_ppg


@lru_cache(maxsize=64)
def _priors(data: SeasonData) -> Mapping[int, float]:
    """{player_id: preseason points per game} from ADP order and the week-1 projection curve."""
    by_position: dict[str, list[PoolPlayer]] = defaultdict(list)
    for player in data.players.values():
        by_position[player.position].append(player)
    priors: dict[int, float] = {}
    for players in by_position.values():
        curve = sorted((data.projection(p.player_id, 1) for p in players), reverse=True)
        drafted = sorted((p for p in players if p.adp is not None), key=lambda p: (p.adp, p.player_id))
        priors.update({p.player_id: data.projection(p.player_id, 1) for p in players if p.adp is None})
        priors.update({p.player_id: curve[n] for n, p in enumerate(drafted)})
    return MappingProxyType(priors)


def _projection(data: SeasonData, player_id: int, week: int) -> float:
    """That week's projection, or 0 when he carries a hard tag that week (a stale projection is not a plan)."""
    tag = data.injuries.get((player_id, week))
    return 0.0 if tag is not None and tag.hard else data.projection(player_id, week)


def _recent(data: SeasonData, player_id: int, week: int) -> list[float]:
    """His last non-zero projections up to `week`, newest first (at most one per weight)."""
    found: list[float] = []
    for w in range(week, 0, -1):
        if (points := _projection(data, player_id, w)) > 0:
            found.append(points)
            if len(found) == len(RECENCY_WEIGHTS):
                break
    return found


def _ros_ppg(recent: Sequence[float], prior: float) -> float:
    samples = [*recent, *[prior] * (len(RECENCY_WEIGHTS) - len(recent))]
    return sum(weight * points for weight, points in zip(RECENCY_WEIGHTS, samples)) / sum(RECENCY_WEIGHTS)


def _out_streak(data: SeasonData, player_id: int, prior: float, week: int) -> int:
    """Consecutive non-bye weeks ending at `week` with no projection, for a player who had one or a prior."""
    streak = 0
    for w in range(week, 0, -1):
        if _projection(data, player_id, w) > 0:
            return streak
        if not data.on_bye(player_id, w):
            streak += 1
    return streak if prior > 0 else 0


def _row(data: SeasonData, player: PoolPlayer, prior: float, week: int, team: str | None,
         games_left: int) -> dict:
    """Everything about a player except his value over replacement and his rank."""
    pid = player.player_id
    streak = _out_streak(data, pid, prior, week)
    injury = data.injuries.get((pid, max(1, week)))     # draft time: a preseason IR/PUP/SUS (replay_pool.as_of)
    if not week and (injury is None or injury.tag not in PRESEASON_TAGS):
        injury = None
    share = OUT_SHARE.get(streak, OUT_SHARE_LONG) if streak else 0.0
    if injury is not None and injury.tag in ("IR", "PUP"):
        share = max(share, IR_OUT_SHARE)
    recent = _recent(data, pid, week)
    weeks = [w for w in range(1, week) if (pid, w) in data.actual]
    played = [data.actual[(pid, w)] for w in weeks]
    return {
        "player_id": pid, "name": player.name, "position": player.position, "adp": player.adp, "team": team,
        "ros_ppg": round(_ros_ppg(recent, prior), 2),
        "projection": _projection(data, pid, week) if week else prior,   # draft time: only what ADP knew
        "last_projection": recent[0] if recent else 0.0, "injury": injury,
        "out": streak > 0 or (injury is not None and injury.hard), "games_left": games_left,
        "expected_missed": round(games_left * share, 2), "availability": round(1.0 - share, 3),
        "games": len(played), "ppg": round(sum(played) / len(played), 2) if played else 0.0,
        "recent_points": tuple(played[::-1][:RECENT_GAMES]), "last_game_week": weeks[-1] if weeks else 0,
    }


def _valued(row: dict, replacement: Mapping[str, float]) -> dict:
    """Adds the values over replacement (skill only; K and DST have none) and the trade chart value."""
    if row["position"] not in SKILL:
        return {**row, "vorp_per_game": 0.0, "healthy_value": 0.0, "trade_value": 0.0}
    over = max(0.0, row["ros_ppg"] - replacement[row["position"]])
    healthy = over * row["games_left"]
    return {**row, "vorp_per_game": round(over, 2), "healthy_value": round(healthy, 1),
            "trade_value": round(healthy * row["availability"], 1)}


def _ranked(rows: Iterable[dict]) -> tuple[ReplayValue, ...]:
    ordered = sorted(rows, key=lambda r: (-r["chart"], -r["ros_ppg"], r["name"], r["player_id"]))
    counts: dict[str, int] = defaultdict(int)
    values = []
    for rank, row in enumerate(ordered, start=1):
        counts[row["position"]] += 1
        values.append(ReplayValue(**row, rank=rank, pos_rank=f"{row['position']}{counts[row['position']]}"))
    return tuple(values)


@dataclass(frozen=True, eq=False)
class _Base:
    """One week's values before the seeded injury news: shared by every game (seed) on that season."""
    rows: Mapping[int, dict]                        # valued rows by id (never mutated)
    chart_data: SeasonData                          # the season the chart reads (superflex: replay_superflex.view)
    parts: replay_chart.Parts                       # what replay_chart.with_news needs, hard-tagged players only
    replacement: Mapping[str, float]
    source: str
    live: Mapping[int, float]                       # healthy chart before the freeze, frozen players only


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
    regular = replay_superflex.one_qb(data)
    priors = _priors(regular)
    teams = {pid: data.team_at(pid, week) for pid, p in data.players.items() if p.position in positions}
    rows = [_row(data, data.players[pid], priors[pid], week, team, 0) for pid, team in teams.items()]
    two_qb = replay_slots.two_qb(league.starters)
    chart_data = replay_chart.superflex_view(data) if two_qb else regular
    live: dict[int, float] = {}
    replay_chart.base_charts(chart_data, rows, priors, week, end_week, drafted, regular if two_qb else None,
                             dict.fromkeys(teams, 0.0), live)
    return MappingProxyType(live), MappingProxyType(teams)


@lru_cache(maxsize=128)
def _base(data: SeasonData, league: LeagueSettings, week: int, end_week: int, drafted: int = 0) -> _Base:
    regular = replay_superflex.one_qb(data)                     # superflex: non-QBs as in a 1-QB league
    priors = _priors(regular)
    weeks_left = max(0, end_week - max(1, week) + 1)
    by_team: dict[str | None, int] = {None: weeks_left}          # unknown team: every week left

    def games_left(team: str | None) -> int:
        if team not in by_team:
            by_team[team] = len(data.schedule.remaining_opponents(team, week - 1, end_week))
        return by_team[team]

    teams = {pid: data.team_at(pid, week) for pid in data.players}
    rows = [_row(data, player, priors[pid], week, teams[pid], games_left(teams[pid]))
            for pid, player in data.players.items()]
    two_qb = replay_slots.two_qb(league.starters)
    chart_data = replay_chart.superflex_view(data) if two_qb else regular        # QBs go where 2-QB drafts take them
    frozen = _frozen(data, league, week, end_week, drafted)
    since = {pid: before + 1 for pid, before in _absences(data, week).items() if pid in frozen}
    live: dict[int, float] = {}
    charts, parts, source = replay_chart.base_charts(chart_data, rows, priors, week, end_week, drafted,
                                                     regular if two_qb else None, frozen, live, since)
    rows = [{**row, **charts[row["player_id"]]} for row in rows]
    replacement = replacement_levels(((r["position"], r["ros_ppg"]) for r in rows if r["position"] in SKILL), league)
    return _Base(MappingProxyType({r["player_id"]: _valued(r, replacement) for r in rows}), chart_data, parts,
                 MappingProxyType(replacement), source, MappingProxyType(live))


@lru_cache(maxsize=32)
def _knowledge(data: SeasonData, league: LeagueSettings, week: int, end_week: int, seed: int,
               drafted: int) -> Knowledge:
    """`_base` plus this seed's news (which may set availability) on the hard-tagged players only."""
    base = _base(data, league, week, end_week, drafted)
    news = replay_chart.with_news(base.chart_data, base.rows, base.rows, base.parts, week, end_week, seed)
    values = _ranked(_valued(news[pid], base.replacement) if pid in news else row for pid, row in base.rows.items())
    through = max(0, week - 1)
    return Knowledge(season=data.season, week=week, league=league, values=values,
                     by_id=MappingProxyType({v.player_id: v for v in values}),
                     replacement=base.replacement, schedule=data.schedule, defense=NEUTRAL,
                     weeks_remaining=max(1, end_week - through), through_week=through, rank_source=base.source)


def knowledge(data: SeasonData, league: LeagueSettings, week: int, end_week: int, seed: int = 0,
              drafted: int = 0) -> Knowledge:
    """Values as of just before `week` is played (0 = draft time), looking ahead to `end_week`. `drafted`:
    the league's draft week (replay_state.draft_week; > 0 for a league that jumped in mid-season), which
    sets the "preseason" pre_rank (replay_chart.pre_picks); never later than `week`.

    Built from `data.as_of(week)` only, and cached: the full season and its as_of(week) copy give the
    same object."""
    if week < 0 or end_week < 1 or drafted > max(week, 0):
        raise ValueError(f"Week {week} to {end_week} (drafted in week {drafted}) is not a valid span.")
    if data.known_before is not None and max(1, data.known_before) < week:
        raise ValueError(f"This season data stops before week {data.known_before}: it can't describe week {week}.")
    return _knowledge(data.as_of(week), league, week, end_week, seed, drafted)      # seed: the game's news noise


def clear_cache() -> None:
    _knowledge.cache_clear()
    _base.cache_clear()
    _live.cache_clear()
    _priors.cache_clear()
    projector.cache_clear()
    replay_chart.clear_cache()
    from services import replay_claim_ai                # its hot_rates cache holds Knowledge objects too
    replay_claim_ai.hot_rates.cache_clear()


def league_settings(settings: "replay_state.Settings") -> LeagueSettings:
    """The app's LeagueSettings for a replay league (K and DST slots included, the playoff weeks set)."""
    playoffs = tuple(range(settings.regular_season_weeks + 1, settings.end_week + 1))
    return LeagueSettings(scoring=settings.scoring, teams=settings.teams, starters=settings.starters,
                          playoff_weeks=playoffs, bench=settings.bench,
                          superflex=replay_slots.superflex(settings.starters) > 0)


@lru_cache(maxsize=16)       # x lineup_gain.MAX_LINEUPS solves each: tens of MB at most
def projector(know: Knowledge) -> lineup_gain.Projector:
    """Week-by-week points for lineup_gain: this week's projection now, the healthy rate afterwards.

    One per Knowledge, so every team's lineup maths shares its point cache."""
    weekly = {v.player_id: SimpleNamespace(projection=v.projection) for v in know.values}
    return ReplayProjector(know, {}, weekly, max(1, know.week))


def horizon(know: Knowledge, settings: "replay_state.Settings") -> lineup_gain.Horizon:
    playoffs = range(settings.regular_season_weeks + 1, settings.end_week + 1)
    return lineup_gain.horizon(max(1, know.week), settings.end_week, playoffs, None)


def lineup_holes(positions: Sequence[str], settings) -> int:
    """How many starting slots (QB/RB/WR/TE/FLEX/SUPERFLEX/K/DST) players at these positions cannot fill.

    `settings` is anything with `.starters`. A roster can still become legal while the holes are no more
    than the picks or roster spots it has left."""
    return len(missing_slots(positions, settings))


def missing_slots(positions: Sequence[str], settings) -> list[str]:
    """The starting slots players at these positions cannot fill, once per missing starter (FLEX, then
    SUPERFLEX last). FLEX takes the spare backs, receivers and tight ends first; SUPERFLEX what is left of
    them plus the spare quarterbacks (FLEX's positions are a subset of SUPERFLEX's, so this is optimal)."""
    have, starters = Counter(positions), settings.starters
    missing = [slot for slot, wanted in starters.items() if slot not in replay_slots.ELIGIBLE
               for _ in range(wanted - have[slot])]
    flex, superflex = starters.get("FLEX", 0), starters.get("SUPERFLEX", 0)
    spare = sum(max(0, have[position] - starters.get(position, 0)) for position in FLEX_POSITIONS)
    left = max(0, spare - flex) + max(0, have["QB"] - starters.get("QB", 0))
    return [*missing, *["FLEX"] * max(0, flex - spare), *["SUPERFLEX"] * max(0, superflex - left)]


def can_field_lineup(positions: Sequence[str], settings) -> bool:
    """Can players at these positions fill every starting slot? Shared by the draft, trades and waivers."""
    return lineup_holes(positions, settings) == 0


def position_cap(position: str, settings) -> int:
    """The most a CPU carries at a position, the limits its draft keeps: its starting slots (a FLEX counts
    for backs and receivers, a SUPERFLEX for quarterbacks) plus BACKUPS. 2 QB, 6 RB, 7 WR, 2 TE, 1 K and
    1 DST on the default lineup."""
    flex = settings.starters.get("FLEX", 0) if position in ("RB", "WR") else 0
    flex += settings.starters.get("SUPERFLEX", 0) if position == "QB" else 0
    return settings.starters.get(position, 0) + flex + BACKUPS.get(position, 0)


def crowded(kept: Sequence[ReplayValue], added: Sequence[ReplayValue], position: str, settings) -> bool:
    """Would a CPU that keeps `kept` and takes on `added` be over its limit at `position`? One over is fine
    for each starting slot there that the players it keeps cannot fill this week (bye or out) and a
    newcomer can: he is then the only way to field a lineup."""
    old, new = ([v for v in group if v.position == position] for group in (kept, added))
    idle = settings.starters.get(position, 0) - sum(v.projection > 0 for v in old)
    needed = max(0, min(idle, sum(v.projection > 0 for v in new)))
    return len(old) + len(new) - position_cap(position, settings) > needed


def _values(know: Knowledge, player_ids: Iterable[int]) -> list[ReplayValue]:
    ids = list(player_ids)
    unknown = [pid for pid in ids if pid not in know.by_id]
    if unknown:
        raise ValueError(f"Unknown player ids: {unknown}")
    return [know.by_id[pid] for pid in ids]


def best_lineup(know: Knowledge, player_ids: Iterable[int], settings) -> dict[str, list[int]]:
    """{slot: [player ids]} for the best lineup this week over all six positions.

    Ranked by this week's projection, ties going to the higher rest-of-season rate. `settings` is anything
    with `.starters` (replay Settings or LeagueSettings)."""
    players = sorted(_values(know, player_ids), key=lambda v: (-v.projection, -v.ros_ppg, v.player_id))
    return mt.best_lineup(players, SimpleNamespace(starters=settings.starters, superflex=False))


def starters(know: Knowledge, player_ids: Iterable[int], settings) -> frozenset[int]:
    """Who starts in `best_lineup`, as a set."""
    return frozenset(pid for ids in best_lineup(know, player_ids, settings).values() for pid in ids)


def idle_slots(know: Knowledge, player_ids: Iterable[int], settings) -> list[str]:
    """The starting slots (FLEX, K and DST included) this roster cannot fill with a projected player this
    week (bye, out or nobody there; a K or DST counts whenever his team plays), once per missing starter."""
    lineup = best_lineup(know, player_ids, settings)
    return [slot for slot, wanted in settings.starters.items()
            for _ in range(wanted - sum(_fills(know, pid, slot) for pid in lineup.get(slot, ())))]


def _fills(know: Knowledge, player_id: int, slot: str) -> bool:
    """Does he fill his slot this week? A projection, or a K or DST whose team plays (replay_season.starters_for
    starts him: a late signing has no projection yet)."""
    value = know.by_id[player_id]
    if value.projection > 0:
        return True
    return (slot in ("K", "DST") and value.team is not None and not (value.injury is not None and value.injury.hard)
            and know.schedule.opponent(value.team, know.week) is not None)


def lineup_points(know: Knowledge, player_ids: Iterable[int]) -> float:
    """Projected total this week for those starters (skill, K and DST)."""
    return round(sum(v.projection for v in _values(know, player_ids)), 2)
