"""Week-X rankings: one projected score per player, blended from every source that covers them.

    projection = sum(weight * group points) / sum(weights of the groups that cover the player)
                 * sos_factor ** FINAL_SOS_TILT

Sources blend in four groups (services/weight_learning.py): Vegas props, the experts (Josh &
Hayden and Yahoo's consensus, averaged), ESPN and the app's model; their weights are learned.
Expert sources publish ranks, not points; each expert's rank N is converted to the points of
the Nth best numeric projection (props / ESPN / model blend) at that position before any
averaging, so a ranker's opinion moves a player exactly as far as their rank implies. Sources are read from the
weekly_source_projections snapshot (see services.weekly_refresh), so this module never
touches the network.
"""
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from threading import Lock
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import DataLoad, GameForecast, InjuryReport, NflGame, PlayerStatus, WeeklySourceProjection
from integrations import espn, odds_api, yahoo_experts
from services import projection_factors, rankings_store, score_ranges
from services.game_features import conditions_for, team_profiles
from services.projection_factors import inputs_for
from services.weather import forecasts
from services.build_locks import key_lock
from services.weight_learning import DEFAULT_WEIGHTS, effective_weights, grouped, position_weights, source_shares
from services.league import POSITIONS, LeagueSettings
from services.projections import PlayerSeason
from services.injuries import InjuryContext, build_context
from services.valuation import Valuation, build_valuation
from services.weekly_adjust import Adjusted, Scenario, Step, adjust, explain
from services.weekly_model import Matchup, matchup_for, model_stats, points_from_stats, props_line, weekly_rate

# The starting weights. Each completed week nudges them toward what actually predicted best
# (services/weight_learning.py); WeeklyRankings.weights holds the ones a build used.
WEIGHTS = DEFAULT_WEIGHTS
ANCHOR_SOURCES = ("vegas_props", "espn", "model")
# Extra matchup tilt on the final blend. Props and experts already price in the matchup;
# this deliberately leans further on opponent-adjusted schedule strength.
FINAL_SOS_TILT = 0.25
FIRST_WEEK, LAST_WEEK = 1, 18
SOURCE_LABELS = {"vegas_props": "Vegas player props", "josh_hayden": "Josh & Hayden (Yahoo)",
                 "model": "Matchup model", "yahoo_consensus": "Yahoo expert consensus", "espn": "ESPN",
                 "experts": "Experts (Yahoo consensus, Josh & Hayden)"}


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
    if_active: float = 0.0
    play_prob: float = 1.0
    status: str | None = None
    floor: float | None = None
    ceiling: float | None = None
    boost_points: float = 0.0
    scenarios: tuple[Scenario, ...] = ()
    explanation: tuple[Step, ...] = ()
    pct_rostered: float | None = None
    actual: float | None = None        # points scored that week (league scoring), once the game is final
    quantiles: tuple[float, ...] | None = None   # points if he plays, at score_ranges.LEVELS


@dataclass(frozen=True)
class WeeklyRankings:
    season: int
    week: int
    scoring: str
    players: tuple[WeeklyPlayer, ...]
    source_counts: Mapping[str, int] = field(default_factory=dict)
    context: InjuryContext | None = None
    weights: Mapping[str, float] = field(default_factory=lambda: dict(WEIGHTS))
    weights_learned_through: int | None = None   # the last completed week the weights learned from


@dataclass
class _Candidate:
    player_id: int
    name: str
    position: str
    team: str | None
    props: dict | None = None
    espn: dict | None = None
    expert_ranks: dict = field(default_factory=dict)
    pct_rostered: float | None = None

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
            candidate.pct_rostered = row.pct_rostered
        elif row.source == yahoo_experts.SOURCE and row.rank is not None:
            candidate.expert_ranks[row.expert] = row.rank
    return candidates


def _add_model_players(candidates: dict[int, _Candidate], valuation: Valuation) -> None:
    for value in valuation.values:
        if value.position in POSITIONS and value.player_id not in candidates:
            candidates[value.player_id] = _Candidate(value.player_id, value.name, value.position, value.team)


def _weighted(values: Mapping[str, float], weights: Mapping[str, float] = WEIGHTS) -> float | None:
    """Weighted mean over the groups present (per-source values are grouped first)."""
    groups = grouped(values)
    total = sum(weights.get(k, 0.0) for k in groups)
    return sum(weights.get(k, 0.0) * v for k, v in groups.items()) / total if total else None


def model_factors(session: Session, season: int, week: int) -> Callable[[str, str | None, str], tuple[float, str]]:
    """(factor, reasons) for the model source: calibration, opponent EPA / pass rush, and kickoff weather."""
    profiles = team_profiles(session, season, week)
    games = {}
    for game in session.scalars(select(NflGame).where(NflGame.season == season, NflGame.week == week)):
        games[game.home_team] = games[game.away_team] = game
    forecast = forecasts(session, [g.game_id for g in games.values()])

    def factor_for(position: str, team: str | None, opponent: str) -> tuple[float, str]:
        inputs = inputs_for(profiles, opponent, conditions_for(games.get(team or ""), forecast))
        return projection_factors.factor(position, inputs), projection_factors.describe(position, inputs)

    return factor_for


def _actual(season: PlayerSeason | None, week: int) -> float | None:
    game = next((g for g in season.games if g.week == week), None) if season else None
    return round(game.fantasy_points, 2) if game else None


def _rank_to_points(curve: list[float], rank: float) -> float:
    """Points of the rank-th best projection, interpolating fractional ranks."""
    index = min(max(rank - 1, 0.0), len(curve) - 1)
    low = int(index)
    high = min(low + 1, len(curve) - 1)
    return curve[low] + (curve[high] - curve[low]) * (index - low)


def _numeric_sources(candidate: _Candidate, matchup: Matchup, rate: float | None,
                     season_line: PlayerSeason | None, league: LeagueSettings,
                     model_factor: float = 1.0) -> dict[str, SourceValue]:
    scoring = league.scoring_settings()
    sources: dict[str, SourceValue] = {}
    model = (model_stats(season_line, rate, matchup, scoring)
             if rate is not None and season_line is not None else {})
    model = {key: value * model_factor for key, value in model.items()}   # calibration, EPA matchup, weather
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
        # Each expert's rank becomes points first, then the points are averaged: averaging ranks
        # first would understate a player the two disagree on (the points curve is convex).
        points = sum(_rank_to_points(curve, rank) for rank in josh_hayden) / len(josh_hayden)
        sources["josh_hayden"] = SourceValue(round(points, 2), sum(josh_hayden) / len(josh_hayden))
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


def player_rates(valuation: Valuation) -> dict[int, float]:
    """Recency-weighted model rate per player, before matchup and injuries."""
    return {p.player_id: weekly_rate(p, valuation.priors.get(p.player_id)) for p in valuation.seasons}


def blend(candidates: Mapping[int, _Candidate], valuation: Valuation, week: int,
          league: LeagueSettings, require_outside_source: bool = False,
          context: InjuryContext | None = None, rates: Mapping[int, float] | None = None,
          weights: Mapping[str, float] = WEIGHTS,
          factor_for: Callable[[str, str | None, str], tuple[float, str]] | None = None,
          weights_by_position: Mapping[str, Mapping[str, float]] | None = None,
          shapes: Mapping[int, tuple[float | None, float | None]] | None = None) -> tuple[WeeklyPlayer, ...]:
    """Pure blending step over already-loaded sources.

    With require_outside_source, players that no outside source projects or ranks are left
    out: when Yahoo/ESPN/props have published the week, absence usually means the player
    is injured or inactive, and the model alone can't know that.
    """
    seasons = {p.player_id: p for p in valuation.seasons}
    context = context or InjuryContext(availability={})
    rates = rates if rates is not None else player_rates(valuation)
    prepared = []
    for candidate in candidates.values():
        if require_outside_source and not candidate.has_outside_row:
            continue                                   # likely inactive; also kept off the rank curves
        matchup = matchup_for(candidate.team, candidate.position, week, valuation.schedule, valuation.defense)
        if matchup is None:
            continue                                   # bye week, or team unknown
        model_factor = factor_for(candidate.position, candidate.team, matchup.opponent) if factor_for else (1.0, "")
        numeric = _numeric_sources(candidate, matchup, rates.get(candidate.player_id),
                                   seasons.get(candidate.player_id), league, model_factor[0])
        prepared.append((candidate, matchup, numeric, model_factor))

    curves: dict[str, list[float]] = defaultdict(list)
    for candidate, _, numeric, _ in prepared:
        own_weights = (weights_by_position or {}).get(candidate.position, weights)
        anchor = _weighted({k: v.points for k, v in numeric.items() if k in ANCHOR_SOURCES}, own_weights)
        if anchor is not None:
            curves[candidate.position].append(anchor)
    for curve in curves.values():
        curve.sort(reverse=True)

    players = []
    for candidate, matchup, numeric, model_factor in prepared:
        sources = {**numeric, **_expert_sources(candidate, curves[candidate.position])}
        own_weights = (weights_by_position or {}).get(candidate.position, weights)
        combined = _weighted({k: v.points for k, v in sources.items()}, own_weights)
        if combined is None:
            continue
        expert_names = {**yahoo_experts.TRACKED_EXPERTS, yahoo_experts.CONSENSUS: "Yahoo consensus"}
        tilt = matchup.sos_factor ** FINAL_SOS_TILT
        model_share = source_shares(list(sources), own_weights).get("model", 0.0)
        adjusted: Adjusted = adjust(candidate.position, combined, tilt, matchup, model_share,
                                    candidate.player_id, context, (shapes or {}).get(candidate.player_id, (None, None)))
        if adjusted.projection <= 0:
            continue                                   # ruled out
        steps = explain(candidate.name, candidate.position, rates.get(candidate.player_id),
                        valuation.priors.get(candidate.player_id), matchup, sources, own_weights, SOURCE_LABELS,
                        combined, tilt, adjusted, context.boosts.get(candidate.player_id, ()),
                        context.returning.get(candidate.player_id, ()))
        if "model" in sources and abs(model_factor[0] - 1.0) >= 0.01:
            steps = (*steps[:1], Step("Model adjustments", f"x{model_factor[0]:.2f}",
                                      f"Applied to the model's number only: {model_factor[1]}"), *steps[1:])
        players.append(WeeklyPlayer(
            player_id=candidate.player_id, name=candidate.name, position=candidate.position,
            team=candidate.team, matchup=matchup, projection=adjusted.projection, sources=sources,
            expert_ranks={expert_names[e]: r for e, r in candidate.expert_ranks.items() if e in expert_names},
            if_active=adjusted.if_active, play_prob=adjusted.availability.play_prob,
            status=None if adjusted.availability.play_prob >= 1 and adjusted.availability.status is None
            else adjusted.availability.label,
            floor=adjusted.floor, ceiling=adjusted.ceiling, boost_points=adjusted.boost_points,
            quantiles=adjusted.quantiles,
            scenarios=adjusted.scenarios, explanation=steps, pct_rostered=candidate.pct_rostered,
            actual=_actual(seasons.get(candidate.player_id), week)))
    return _ranked(players)


def default_week(valuation: Valuation) -> int:
    """The first week with games still to play (falls back to the week after the latest stats)."""
    week = valuation.schedule.current_week() or (valuation.through_week or 0) + 1
    return min(max(week, FIRST_WEEK), LAST_WEEK)


_cache: dict[tuple, "WeeklyRankings"] = {}
_cache_lock = Lock()
CACHE_SIZE = 16


def _version(session: Session, season: int, week: int) -> tuple:
    """Changes whenever any input to the week's rankings changes."""
    return (str(session.get_bind().url),
            session.scalar(select(func.max(WeeklySourceProjection.fetched_at)).where(
                WeeklySourceProjection.season == season, WeeklySourceProjection.week == week)),
            session.scalar(select(func.max(PlayerStatus.fetched_at))),
            session.scalar(select(func.count(InjuryReport.id)).where(
                InjuryReport.season == season, InjuryReport.week == week)),
            session.scalar(select(func.max(DataLoad.id))),
            (effective_weights(session, season, week)[1] or SimpleNamespace(id=None)).id,
            session.scalar(select(func.max(GameForecast.fetched_at))))


def build_weekly_rankings(session: Session, league: LeagueSettings, week: int | None = None) -> WeeklyRankings:
    """Cached per league settings, week, and data version (sources, live status, reports, loads).

    Two layers: memory, then the local database (so a restart doesn't redo the slow build).
    Every fresh build also records this week's pre-kickoff projections for later scoring.
    """
    valuation = build_valuation(session, league)
    week = week or default_week(valuation)
    key = (league, week, _version(session, valuation.season, week))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    stored_key = rankings_store.cache_key(*key)
    with key_lock(("weekly", stored_key)):
        with _cache_lock:
            if key in _cache:
                return _cache[key]                 # built by a request we waited for
        rankings = rankings_store.load(session, stored_key)
        if not isinstance(rankings, WeeklyRankings):
            rankings = _build(session, league, valuation, week)
            rankings_store.save(session, stored_key, valuation.season, week, rankings)
            rankings_store.record_history(session, rankings)
        with _cache_lock:
            if len(_cache) >= CACHE_SIZE:
                _cache.pop(next(iter(_cache)))
            _cache[key] = rankings
    return rankings


def _build(session: Session, league: LeagueSettings, valuation: Valuation, week: int) -> WeeklyRankings:
    candidates = _load_sources(session, valuation.season, week, league.scoring)
    # Only Yahoo/ESPN cover the whole week; props alone (e.g. just Thursday's game) don't
    # say anything about who is active in the other games.
    has_outside_sources = any(c.espn is not None or c.expert_ranks for c in candidates.values())
    _add_model_players(candidates, valuation)
    rates = player_rates(valuation)
    # Sleeper tags and today's depth chart describe now, so they only apply to the current week.
    context = build_context(session, valuation.seasons, rates, valuation.season, week, valuation.schedule,
                            use_live=week == default_week(valuation))
    weights, learned = effective_weights(session, valuation.season, week)
    players = blend(candidates, valuation, week, league, require_outside_source=has_outside_sources,
                    context=context, rates=rates, weights=weights,
                    factor_for=model_factors(session, valuation.season, week),
                    weights_by_position=position_weights(session, valuation.season, week),
                    shapes=score_ranges.shape_inputs(session, valuation.season, candidates, before_week=week))
    counts = {key: sum(1 for p in players if key in grouped({k: v.points for k, v in p.sources.items()}))
              for key in weights}
    return WeeklyRankings(season=valuation.season, week=week, scoring=league.scoring,
                          players=players, source_counts=counts, context=context, weights=weights,
                          weights_learned_through=learned.week if learned else None)
