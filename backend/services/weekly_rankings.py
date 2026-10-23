"""Week-X rankings: one projected score per player, blended from every source that covers them.

    projection = sum(weight * source points) / sum(weights of the sources that cover the player)
                 * sos_factor ** FINAL_SOS_TILT

Weights (WEIGHTS) put Vegas player props first and Josh Norris + Hayden Winks second.
Expert sources publish ranks, not points; a rank N is converted to the points of the Nth
best numeric projection (props / ESPN / model blend) at that position, so a ranker's
opinion moves a player exactly as far as their rank implies. Sources are read from the
weekly_source_projections snapshot (see services.weekly_refresh), so this module never
touches the network.
"""
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import WeeklySourceProjection
from integrations import espn, odds_api, yahoo_experts
from services.league import POSITIONS, LeagueSettings
from services.projections import PlayerSeason
from services.valuation import PlayerValue, Valuation, build_valuation
from services.weekly_model import Matchup, matchup_for, model_stats, points_from_stats, props_line

WEIGHTS = {"vegas_props": 0.35, "josh_hayden": 0.30, "model": 0.15, "yahoo_consensus": 0.10, "espn": 0.10}
ANCHOR_SOURCES = ("vegas_props", "espn", "model")
# Extra matchup tilt on the final blend. Props and experts already price in the matchup;
# this deliberately leans further on opponent-adjusted schedule strength.
FINAL_SOS_TILT = 0.25
FIRST_WEEK, LAST_WEEK = 1, 18
SOURCE_LABELS = {"vegas_props": "Vegas player props", "josh_hayden": "Josh & Hayden (Yahoo)",
                 "model": "Matchup model", "yahoo_consensus": "Yahoo expert consensus", "espn": "ESPN"}


@dataclass(frozen=True)
class SourceValue:
    points: float
    rank: float | None = None       # the positional rank behind expert-derived points


@dataclass(frozen=True)
class WeeklyPlayer:
    player_id: int
    name: str
    position: str
    team: str | None
    matchup: Matchup
    projection: float
    sources: Mapping[str, SourceValue]
    expert_ranks: Mapping[str, float]
    rank: int = 0
    pos_rank: str = ""


@dataclass(frozen=True)
class WeeklyRankings:
    season: int
    week: int
    scoring: str
    players: tuple[WeeklyPlayer, ...]
    source_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass
class _Candidate:
    player_id: int
    name: str
    position: str
    team: str | None
    props: dict | None = None
    espn: dict | None = None
    expert_ranks: dict = field(default_factory=dict)

    @property
    def has_outside_row(self) -> bool:
        """Some outside source lists the player this week (evidence they're active)."""
        return self.props is not None or self.espn is not None or bool(self.expert_ranks)


def _load_sources(session: Session, season: int, week: int, scoring: str) -> dict[int, _Candidate]:
    rows = session.scalars(select(WeeklySourceProjection).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.player_id.is_not(None),
        WeeklySourceProjection.position.in_(POSITIONS))).all()
    candidates: dict[int, _Candidate] = {}
    for row in rows:
        if row.source == yahoo_experts.SOURCE and row.scoring != scoring:
            continue
        candidate = candidates.setdefault(row.player_id, _Candidate(
            row.player_id, row.player_name, row.position, row.team))
        candidate.team = candidate.team or row.team             # any source can fill a missing team
        if row.source == odds_api.SOURCE:
            candidate.team = row.team or candidate.team       # props know this week's team
            candidate.props = json.loads(row.stats or "{}")
        elif row.source == espn.SOURCE:
            candidate.espn = json.loads(row.stats or "{}")
        elif row.source == yahoo_experts.SOURCE and row.rank is not None:
            candidate.expert_ranks[row.expert] = row.rank
    return candidates


def _add_model_players(candidates: dict[int, _Candidate], valuation: Valuation) -> None:
    for value in valuation.values:
        if value.position in POSITIONS and value.player_id not in candidates:
            candidates[value.player_id] = _Candidate(value.player_id, value.name, value.position, value.team)


def _weighted(values: Mapping[str, float]) -> float | None:
    total = sum(WEIGHTS[k] for k in values)
    return sum(WEIGHTS[k] * v for k, v in values.items()) / total if total else None


def _rank_to_points(curve: list[float], rank: float) -> float:
    """Points of the rank-th best projection, interpolating fractional ranks."""
    index = min(max(rank - 1, 0.0), len(curve) - 1)
    low = int(index)
    high = min(low + 1, len(curve) - 1)
    return curve[low] + (curve[high] - curve[low]) * (index - low)


def _numeric_sources(candidate: _Candidate, matchup: Matchup, value: PlayerValue | None,
                     season_line: PlayerSeason | None, league: LeagueSettings) -> dict[str, SourceValue]:
    scoring = league.scoring_settings()
    sources: dict[str, SourceValue] = {}
    model = (model_stats(season_line, value.base_ros_ppg, matchup, scoring)
             if value is not None and season_line is not None else {})
    if model:
        sources["model"] = SourceValue(points_from_stats(model, candidate.position, scoring))
    props = props_line(candidate.props or {}, candidate.position, model)
    if props is not None:
        sources["vegas_props"] = SourceValue(points_from_stats(props, candidate.position, scoring))
    if candidate.espn:
        sources["espn"] = SourceValue(points_from_stats(candidate.espn, candidate.position, scoring))
    return sources


def _expert_sources(candidate: _Candidate, curve: list[float]) -> dict[str, SourceValue]:
    if not curve:
        return {}
    sources = {}
    josh_hayden = [candidate.expert_ranks[e] for e in yahoo_experts.JOSH_AND_HAYDEN if e in candidate.expert_ranks]
    if josh_hayden:
        rank = sum(josh_hayden) / len(josh_hayden)
        sources["josh_hayden"] = SourceValue(round(_rank_to_points(curve, rank), 2), rank)
    consensus = candidate.expert_ranks.get(yahoo_experts.CONSENSUS)
    if consensus is not None:
        sources["yahoo_consensus"] = SourceValue(round(_rank_to_points(curve, consensus), 2), consensus)
    return sources


def _ranked(players: list[WeeklyPlayer]) -> tuple[WeeklyPlayer, ...]:
    ordered = sorted(players, key=lambda p: (-p.projection, p.name))
    counts: dict[str, int] = defaultdict(int)
    ranked = []
    for overall, player in enumerate(ordered, start=1):
        counts[player.position] += 1
        ranked.append(WeeklyPlayer(**{**player.__dict__, "rank": overall,
                                      "pos_rank": f"{player.position}{counts[player.position]}"}))
    return tuple(ranked)


def blend(candidates: Mapping[int, _Candidate], valuation: Valuation, week: int,
          league: LeagueSettings, require_outside_source: bool = False) -> tuple[WeeklyPlayer, ...]:
    """Pure blending step over already-loaded sources.

    With require_outside_source, players that no outside source projects or ranks are left
    out: when Yahoo/ESPN/props have published the week, absence usually means the player
    is injured or inactive, and the model alone can't know that.
    """
    seasons = {p.player_id: p for p in valuation.seasons}
    prepared = []
    for candidate in candidates.values():
        if require_outside_source and not candidate.has_outside_row:
            continue                                   # likely inactive; also kept off the rank curves
        matchup = matchup_for(candidate.team, candidate.position, week, valuation.schedule, valuation.defense)
        if matchup is None:
            continue                                   # bye week, or team unknown
        numeric = _numeric_sources(candidate, matchup, valuation.by_id.get(candidate.player_id),
                                   seasons.get(candidate.player_id), league)
        prepared.append((candidate, matchup, numeric))

    curves: dict[str, list[float]] = defaultdict(list)
    for candidate, _, numeric in prepared:
        anchor = _weighted({k: v.points for k, v in numeric.items() if k in ANCHOR_SOURCES})
        if anchor is not None:
            curves[candidate.position].append(anchor)
    for curve in curves.values():
        curve.sort(reverse=True)

    players = []
    for candidate, matchup, numeric in prepared:
        sources = {**numeric, **_expert_sources(candidate, curves[candidate.position])}
        combined = _weighted({k: v.points for k, v in sources.items()})
        if combined is None:
            continue
        expert_names = {**yahoo_experts.TRACKED_EXPERTS, yahoo_experts.CONSENSUS: "Yahoo consensus"}
        players.append(WeeklyPlayer(
            player_id=candidate.player_id, name=candidate.name, position=candidate.position,
            team=candidate.team, matchup=matchup,
            projection=round(combined * matchup.sos_factor ** FINAL_SOS_TILT, 2),
            sources=sources,
            expert_ranks={expert_names[e]: r for e, r in candidate.expert_ranks.items() if e in expert_names}))
    return _ranked(players)


def default_week(valuation: Valuation) -> int:
    """The first week with games still to play (falls back to the week after the latest stats)."""
    week = valuation.schedule.current_week() or (valuation.through_week or 0) + 1
    return min(max(week, FIRST_WEEK), LAST_WEEK)


def build_weekly_rankings(session: Session, league: LeagueSettings, week: int | None = None) -> WeeklyRankings:
    valuation = build_valuation(session, league)
    week = week or default_week(valuation)
    candidates = _load_sources(session, valuation.season, week, league.scoring)
    # Only Yahoo/ESPN cover the whole week; props alone (e.g. just Thursday's game) don't
    # say anything about who is active in the other games.
    has_outside_sources = any(c.espn is not None or c.expert_ranks for c in candidates.values())
    _add_model_players(candidates, valuation)
    players = blend(candidates, valuation, week, league, require_outside_source=has_outside_sources)
    counts = {key: sum(1 for p in players if key in p.sources) for key in WEIGHTS}
    return WeeklyRankings(season=valuation.season, week=week, scoring=league.scoring,
                          players=players, source_counts=counts)
