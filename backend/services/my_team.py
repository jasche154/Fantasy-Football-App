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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import League, Roster, Team
from services.league import DEFAULT_STARTERS, LeagueSettings
from services.trade_finder import TradeOffer, find_trades
from services.valuation import Valuation, build_valuation
from services.waivers import WaiverCandidate, waiver_candidates
from services.weekly_rankings import WeeklyPlayer, WeeklyRankings, build_weekly_rankings

MODELED = ("QB", "RB", "WR", "TE")
FLEX_POSITIONS = ("RB", "WR", "TE")
SLOT_ORDER = ("QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX")
NEED_SHARE = 1 / 3                 # bottom third of the league at a position = a need
OFFERS_PER_TEAM = 3


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


def league_rules(league: League) -> dict:
    return json.loads(league.scoring_settings or "{}")


def league_settings(league: League) -> LeagueSettings:
    """The app's league settings from the imported Yahoo rules (defaults where missing)."""
    rules = league_rules(league)
    starters = rules.get("starters") or dict(DEFAULT_STARTERS)
    return LeagueSettings(scoring=rules.get("scoring", "ppr"), teams=int(rules.get("num_teams") or 12),
                          superflex=bool(rules.get("superflex")), bench=int(rules.get("bench") or 6),
                          starters=MappingProxyType({k: int(v) for k, v in starters.items()}))


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
                available: set[int] | None = None) -> dict[str, list[int]]:
    """{slot: [player ids]} filling each slot with the best remaining projection that can play it."""
    pool = sorted((p for p in players if available is None or p.player_id in available), key=lambda p: -p.projection)
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
    by_id = {p.player_id: p for p in rankings.players}
    available = {s.player_id for s in spots if s.slot not in ("IR", "IR+", "NA")}
    best = best_lineup([by_id[s.player_id] for s in spots if s.player_id in by_id], settings, available)
    names = {v.player_id: v.name for v in valuation.values}
    return MyTeam(league, team, settings, rankings, valuation, spots, best, lineup_changes(spots, best, by_id, names))


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
    """Offers with each real team in the league, best fits for their needs first."""
    league, mine = find_league(session, reference)
    settings = league_settings(league)
    valuation = build_valuation(session, settings)
    team_rosters = rosters(session, league)
    my_ids = list(give_ids) if give_ids else [s.player_id for s in team_rosters.get(mine.id, [])
                                              if s.slot not in ("IR", "IR+", "NA")]
    needs = team_needs(valuation, settings, team_rosters)
    teams = {t.id: t for t in session.scalars(select(Team).where(Team.league_id == league.id))}
    results = []
    for team_id, spots in team_rosters.items():
        if team_id == mine.id or team_id not in teams:
            continue
        offers = find_trades(valuation, my_ids, want_position, limit=10,
                             target_ids={s.player_id for s in spots})
        their_needs = needs.get(team_id, ())
        offers.sort(key=lambda o: -sum(p.position in their_needs for p in o.give))
        if offers:
            results.append(TeamOffers(teams[team_id], their_needs, tuple(offers[:OFFERS_PER_TEAM])))
    results.sort(key=lambda r: -max(sum(p.position in r.needs for p in o.give) for o in r.offers))
    return league, results
