"""Your imported Yahoo teams: roster with projections, best lineup, league waivers and trades.

Everything uses the league's own rules (imported from Yahoo): scoring (std / half / PPR),
team count, and starter slots (QB, RB, WR, TE, FLEX, superflex). Kickers and defenses
aren't modeled, so they're left out of the lineup check.

- Best lineup: fill each starter slot with the highest projected player who can play it
  (fixed positions first, then FLEX, then superflex). Players who are in the best lineup but
  sitting are paired with the starters they should replace.
- League waivers: the usual waiver scoring, limited to players on no roster in this league.
- League trades: offers with each real team, built from their actual roster. A team's needs
  are the positions where its starters rank in the bottom third of the league by
  rest-of-season points; offers that send them a player at a position they need come first.
"""
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import League, Roster, Team
from integrations.weekly_sources import normalize_name
from services.league import DEFAULT_STARTERS, LeagueSettings
from services.trade_finder import TradeOffer, find_trades
from services import game_locks
from services.valuation import Valuation, build_valuation
from services.kdst_rankings import weekly_players
from services.waivers import WaiverCandidate, waiver_candidates
from services.weekly_rankings import WeeklyPlayer, WeeklyRankings, build_weekly_rankings

MODELED = ("QB", "RB", "WR", "TE")          # trade values and waivers; K / DST are lineup-only
FLEX_POSITIONS = ("RB", "WR", "TE")
SLOT_ORDER = ("QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DST")
NEED_SHARE = 1 / 3                 # bottom third of the league at a position = a need
OFFERS_PER_TEAM = 3
CANDIDATES_PER_TEAM = 15          # value-fair offers per team handed to lineup-gain scoring
INACTIVE_SLOTS = ("IR", "IR+", "NA")


class LeagueNotFound(LookupError):
    pass


@dataclass(frozen=True)
class RosterSpot:
    player_id: int
    slot: str                      # Yahoo slot: QB, RB, WR, TE, W/R/T, BN, IR, ...

    @property
    def starting(self) -> bool:
        return self.slot not in ("BN", "IR", "IR+", "NA")


@dataclass(frozen=True)
class LineupChange:
    start: WeeklyPlayer
    bench_id: int | None               # the starter to sit (None: an empty slot)
    bench_name: str | None
    bench_projection: float            # 0 when he has no projection (bye, out, not covered)
    slot: str
    gain: float

    @property
    def toss_up(self) -> bool:
        """Projections this close pick the better player only about half the time."""
        return self.gain < TOSS_UP_POINTS

    @property
    def confidence(self) -> str:
        """'toss_up' (under 1.5 points), 'lean' (1.5-3) or 'clear'."""
        return "toss_up" if self.toss_up else "lean" if self.gain < LEAN_POINTS else "clear"


@dataclass(frozen=True)
class TeamOffers:
    team: Team
    needs: tuple[str, ...]
    offers: tuple[TradeOffer, ...]


@dataclass
class MyTeam:
    league: League
    team: Team
    settings: LeagueSettings
    rankings: WeeklyRankings
    valuation: Valuation
    spots: list[RosterSpot]
    best: dict[str, list[int]] = field(default_factory=dict)
    changes: list[LineupChange] = field(default_factory=list)
    extra: dict[int, WeeklyPlayer] = field(default_factory=dict)     # rostered kickers and defenses
    locked: set[int] = field(default_factory=set)                    # games already kicked off
    kickoff: dict[int, datetime] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)                  # risky starters: inactives timing, backups

    def weekly(self) -> dict[int, WeeklyPlayer]:
        """Every rostered player's projection this week, kickers and defenses included."""
        return {**{p.player_id: p for p in self.rankings.players}, **self.extra}


SCORING_PRESETS = ("ppr", "half", "std")
MAX_SLOTS = 10                     # per position; anything larger is corrupt data
# Start/sit accuracy by projection gap (2021-26): 51% under 1 point, 56% at 1-2, 58% at 2-3, 75% over 5.
TOSS_UP_POINTS = 1.5
LEAN_POINTS = 3.0                  # 1.5-3 points apart: lean (58%), not a lock


def league_rules(league: League) -> dict:
    """The stored rules as a dict; {} when missing or unreadable."""
    try:
        rules = json.loads(league.scoring_settings or "{}")
    except ValueError:
        return {}
    return rules if isinstance(rules, dict) else {}


def _int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def league_settings(league: League) -> LeagueSettings:
    """The app's league settings from the imported Yahoo rules; anything missing or odd uses the default."""
    rules = league_rules(league)
    raw_starters = rules.get("starters")
    if not isinstance(raw_starters, dict) or not raw_starters:
        raw_starters = dict(DEFAULT_STARTERS)
    starters = {str(k): _int(v, 0, 0, MAX_SLOTS) for k, v in raw_starters.items()}
    scoring = rules.get("scoring") if rules.get("scoring") in SCORING_PRESETS else "ppr"
    weeks = rules.get("playoff_weeks")
    playoff = tuple(w for w in weeks if isinstance(w, int) and 1 <= w <= 18) if isinstance(weeks, list) else ()
    return LeagueSettings(scoring=scoring, teams=_int(rules.get("num_teams"), 12, 2, 32),
                          superflex=rules.get("superflex") is True, bench=_int(rules.get("bench"), 6, 0, 30),
                          starters=MappingProxyType(starters), playoff_weeks=playoff or None)


def my_leagues(session: Session) -> list[tuple[League, Team]]:
    rows = session.execute(select(League, Team).join(Team, Team.league_id == League.id)
                           .where(Team.is_users_team.is_(True)).order_by(League.name)).all()
    return [(league, team) for league, team in rows]


def find_league(session: Session, reference: int | str | None) -> tuple[League, Team]:
    """By id, Yahoo league key or (partial) name; with one league, None picks it."""
    leagues = my_leagues(session)
    if not leagues:
        raise LeagueNotFound("No Yahoo league imported yet. Connect Yahoo in Settings and import a league.")
    if reference is None or reference == "":
        if len(leagues) == 1:
            return leagues[0]
        names = ", ".join(league.name for league, _ in leagues)
        raise LeagueNotFound(f"You have several leagues ({names}); say which one.")
    text = str(reference).strip().lower()
    for league, team in leagues:
        if text in (str(league.id), league.platform_league_id.lower(), league.name.lower()):
            return league, team
    matches = [(league, team) for league, team in leagues if text in league.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise LeagueNotFound(f"No imported league matches '{reference}'.")


def rosters(session: Session, league: League) -> dict[int, list[RosterSpot]]:
    """{team_id: spots} from each team's most recent imported week."""
    latest = dict(session.execute(select(Roster.team_id, func.max(Roster.week))
                                  .join(Team, Team.id == Roster.team_id)
                                  .where(Team.league_id == league.id).group_by(Roster.team_id)).all())
    rows = session.execute(select(Roster.team_id, Roster.player_id, Roster.roster_slot, Roster.week)
                           .join(Team, Team.id == Roster.team_id).where(Team.league_id == league.id)).all()
    out: dict[int, list[RosterSpot]] = defaultdict(list)
    for team_id, player_id, slot, week in rows:
        if week == latest.get(team_id):
            out[team_id].append(RosterSpot(player_id, slot))
    return out


def _slots(settings: LeagueSettings) -> list[str]:
    slots = []
    for key in SLOT_ORDER:
        count = settings.starters.get(key, 0) if key != "SUPERFLEX" else (1 if settings.superflex else 0)
        slots.extend([key] * count)
    return slots


def _eligible(slot: str, position: str) -> bool:
    if slot == "FLEX":
        return position in FLEX_POSITIONS
    if slot == "SUPERFLEX":
        return position in MODELED
    return slot == position


def best_lineup(players: Sequence[WeeklyPlayer], settings: LeagueSettings,
                available: set[int] | None = None,
                key: Callable[[WeeklyPlayer], float] | None = None) -> dict[str, list[int]]:
    """{slot: [player ids]} filling each slot with the best remaining player who can play it.

    `key` ranks players (default: projection); the matchup page also tries upside and safe keys.
    """
    key = key or (lambda p: p.projection)
    pool = sorted((p for p in players if available is None or p.player_id in available), key=lambda p: -key(p))
    used: set[int] = set()
    lineup: dict[str, list[int]] = defaultdict(list)
    for slot in _slots(settings):
        pick = next((p for p in pool if p.player_id not in used and _eligible(slot, p.position)), None)
        if pick is not None:
            used.add(pick.player_id)
            lineup[slot].append(pick.player_id)
    return dict(lineup)


def lineup_changes(spots: Sequence[RosterSpot], best: Mapping[str, list[int]],
                   by_id: Mapping[int, WeeklyPlayer], names: Mapping[int, str]) -> list[LineupChange]:
    """Bench players who belong in the best lineup, each paired with a starter to sit.

    A starter with no projection (bye, ruled out, not covered) counts as 0, so he's the
    first one sat.
    """
    best_ids = {pid for ids in best.values() for pid in ids}
    slot_of = {pid: slot for slot, ids in best.items() for pid in ids}
    starting = {s.player_id for s in spots if s.starting}
    projection = {pid: by_id[pid].projection if pid in by_id else 0.0 for pid in starting}
    promote = sorted((by_id[pid] for pid in best_ids - starting if pid in by_id), key=lambda p: -p.projection)
    demote = sorted(starting - best_ids, key=lambda pid: projection[pid])
    changes = []
    for index, player in enumerate(promote):
        bench = demote[index] if index < len(demote) else None
        bench_points = projection[bench] if bench is not None else 0.0
        changes.append(LineupChange(player, bench, names.get(bench) if bench is not None else None, bench_points,
                                    slot_of[player.player_id], round(player.projection - bench_points, 1)))
    return changes


def build_my_team(session: Session, reference: int | str | None) -> MyTeam:
    league, team = find_league(session, reference)
    settings = league_settings(league)
    valuation = build_valuation(session, settings)
    rankings = build_weekly_rankings(session, settings)
    spots = rosters(session, league).get(team.id, [])
    rules = league_rules(league)
    extra = weekly_players(session, rankings.season, rankings.week, [s.player_id for s in spots],
                           rules.get("k_scoring"), rules.get("dst_scoring"))
    by_id = {**{p.player_id: p for p in rankings.players}, **extra}
    mine = [by_id[s.player_id] for s in spots if s.player_id in by_id]
    kickoff = game_locks.kickoffs(mine, valuation.schedule, rankings.week)
    locked = game_locks.locked_ids(mine, kickoff)
    starting = {s.player_id for s in spots if s.starting}
    # Locked bench players can't come in; locked starters stay in.
    available = {s.player_id for s in spots if s.slot not in ("IR", "IR+", "NA")} - (locked - starting)
    best = best_lineup(mine, settings, available, key=game_locks.lock_key(locked & starting))
    best = game_locks.prefer_late_flex(best, {p.player_id: p.position for p in mine}, kickoff, locked)
    names = {v.player_id: v.name for v in valuation.values}
    names.update({pid: p.name for pid, p in extra.items()})
    best_ids = {pid for ids in best.values() for pid in ids}
    warnings = game_locks.alerts([by_id[pid] for pid in best_ids if pid in by_id],
                                 [p for p in mine if p.player_id not in best_ids], kickoff, locked)
    return MyTeam(league, team, settings, rankings, valuation, spots, best, lineup_changes(spots, best, by_id, names),
                  extra, locked=locked, kickoff=kickoff, alerts=warnings)


def player_name(session: Session, player_id: int) -> str:
    from database.models import Player

    player = session.get(Player, player_id)
    return player.full_name if player and player.full_name else f"Player {player_id}"


def lineup_total(ids: Sequence[int], by_id: Mapping[int, WeeklyPlayer]) -> float:
    return round(sum(by_id[pid].projection for pid in ids if pid in by_id), 1)


def league_waivers(session: Session, reference: int | str | None, position: str | None = None,
                   limit: int = 25) -> tuple[League, WeeklyRankings, list[WaiverCandidate]]:
    """Waiver targets that are actually free in this league (on no team's roster)."""
    league, _ = find_league(session, reference)
    settings = league_settings(league)
    valuation = build_valuation(session, settings)
    rankings = build_weekly_rankings(session, settings)
    rostered = {s.player_id for spots in rosters(session, league).values() for s in spots}
    # Rostered players the import couldn't match by id or name still aren't free agents.
    unmatched = {normalize_name(str(n)) for n in league_rules(league).get("unmatched") or ()}
    rostered |= {p.player_id for p in rankings.players if normalize_name(p.name) in unmatched}
    return league, rankings, waiver_candidates(rankings, valuation, position, limit=limit, rostered_ids=rostered)


def team_needs(valuation: Valuation, settings: LeagueSettings,
               team_rosters: Mapping[int, Sequence[RosterSpot]]) -> dict[int, tuple[str, ...]]:
    """{team_id: positions where its starters rank in the league's bottom third (rest of season)}."""
    strength: dict[str, dict[int, float]] = defaultdict(dict)
    for team_id, spots in team_rosters.items():
        values = [valuation.by_id[s.player_id] for s in spots if s.player_id in valuation.by_id]
        for position in MODELED:
            count = max(settings.starters.get(position, 0), 1)
            best = sorted((v.ros_ppg for v in values if v.position == position), reverse=True)[:count]
            strength[position][team_id] = sum(best)
    needs: dict[int, list[str]] = defaultdict(list)
    for position, by_team in strength.items():
        ranked = sorted(by_team, key=lambda t: by_team[t])
        for team_id in ranked[:max(1, round(len(ranked) * NEED_SHARE))]:
            needs[team_id].append(position)
    return {team_id: tuple(positions) for team_id, positions in needs.items()}


def league_trades(session: Session, reference: int | str | None, want_position: str | None = None,
                  give_ids: Sequence[int] | None = None) -> tuple[League, list[TeamOffers]]:
    """Candidate offers with each real team (up to CANDIDATES_PER_TEAM, best fits for their needs first).

    Not cut to OFFERS_PER_TEAM here: services/league_trades.score_trades scores every candidate by
    both lineups' gains and keeps the best, so a strong deal isn't lost to a closer-to-fair one.
    Players in IR/NA slots aren't offered or targeted: they aren't in either lineup's gain.
    """
    league, mine = find_league(session, reference)
    settings = league_settings(league)
    valuation = build_valuation(session, settings)
    team_rosters = rosters(session, league)
    my_ids = list(give_ids) if give_ids else [s.player_id for s in team_rosters.get(mine.id, [])
                                              if s.slot not in INACTIVE_SLOTS]
    needs = team_needs(valuation, settings, team_rosters)
    teams = {t.id: t for t in session.scalars(select(Team).where(Team.league_id == league.id))}
    results = []
    for team_id, spots in team_rosters.items():
        if team_id == mine.id or team_id not in teams:
            continue
        offers = find_trades(valuation, my_ids, want_position, limit=CANDIDATES_PER_TEAM,
                             target_ids={s.player_id for s in spots if s.slot not in INACTIVE_SLOTS})
        their_needs = needs.get(team_id, ())
        offers.sort(key=lambda o: -sum(p.position in their_needs for p in o.give))
        if offers:
            results.append(TeamOffers(teams[team_id], their_needs, tuple(offers)))
    results.sort(key=lambda r: -max(sum(p.position in r.needs for p in o.give) for o in r.offers))
    return league, results
