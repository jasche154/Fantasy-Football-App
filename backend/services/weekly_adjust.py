"""Turn a player's blended projection into the final number, range, scenarios and explanation.

1. **If-active projection:** the source blend times the schedule tilt, plus teammate boosts.
   - When a teammate's status is uncertain (Questionable), outside sources usually assume he
     plays, so the expected boost is added in full.
   - When he is already ruled Out or Doubtful, experts and lines have priced it in, so only the
     model's share of the boost is added.
2. **Projection:** the if-active number times the player's own chance of playing.
3. **Floor and ceiling:** the if-active number times the 10th and 90th percentile of
   actual-over-projected points from the backtest, by position. The floor is 0 if he may sit.
4. **Scenarios:** "if <teammate> sits", the projection with that teammate's full vacated
   work instead of its probability-weighted share.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from services.injuries import Availability, Boost, InjuryContext
from services import score_ranges
from services.weight_learning import source_shares
from services.weekly_model import Matchup, projection_ranges, range_for

PRICED_IN = 0.1            # teammates below this chance to play are already priced in by outside sources



@dataclass(frozen=True)
class Scenario:
    teammate: str
    teammate_status: str
    extra_points: float
    projection: float


@dataclass(frozen=True)
class Step:
    label: str
    value: str
    detail: str


@dataclass(frozen=True)
class Adjusted:
    if_active: float
    projection: float
    floor: float | None
    ceiling: float | None
    boost_points: float
    scenarios: tuple[Scenario, ...]
    availability: Availability
    quantiles: tuple[float, ...] | None = None       # points if he plays, at score_ranges.LEVELS


def adjust(position: str, blended: float, tilt: float, matchup: Matchup, model_share: float,
           player_id: int, context: InjuryContext,
           shape: tuple[float | None, float | None] = (None, None)) -> Adjusted:
    availability = context.of(player_id)
    base = blended * tilt
    contributions = []
    for boost in context.boosts.get(player_id, ()):
        teammate_prob = context.of(boost.from_player_id).play_prob
        share = model_share if teammate_prob < PRICED_IN else 1.0
        contributions.append((boost, boost.expected_points * matchup.multiplier * share, share))
    boost_total = sum(c for _, c, _ in contributions)
    # A starter coming back takes work from his fill-in. Experts and lines already price that
    # in, but the model's recency-weighted rate was built while the starter was out.
    give_back = sum(r.expected_loss for r in context.returning.get(player_id, ())) * matchup.multiplier * model_share
    boost_total -= give_back
    if_active = max(0.0, base + boost_total)
    scenarios = tuple(
        Scenario(b.from_name, b.from_status, round(b.full_points * matchup.multiplier * share - c, 2),
                 round((if_active - c + b.full_points * matchup.multiplier * share) * availability.play_prob, 2))
        for b, c, share in contributions if b.full_points * matchup.multiplier * share - c >= 0.5)
    # Floor and ceiling are the 10th / 90th percentile if he plays; the chance he sits is
    # separate (play_prob), so it isn't counted twice in the matchup simulation.
    quantiles = score_ranges.player_quantiles(position, if_active, *shape)
    floor = ceiling = None
    if quantiles:
        floor = round(quantiles[score_ranges.FLOOR_INDEX], 1)
        ceiling = round(quantiles[score_ranges.CEILING_INDEX], 1)
    elif (ranges := range_for(position, if_active)):
        floor, ceiling = round(if_active * ranges["floor"], 1), round(if_active * ranges["ceiling"], 1)
    return Adjusted(if_active=round(if_active, 2), projection=round(if_active * availability.play_prob, 2),
                    floor=floor, ceiling=ceiling, boost_points=round(boost_total, 2),
                    scenarios=scenarios, availability=availability, quantiles=quantiles)


def explain(name: str, position: str, rate: float | None, prior: float | None, matchup: Matchup,
            sources: Mapping[str, object], weights: Mapping[str, float], labels: Mapping[str, str],
            blended: float, tilt: float, adjusted: Adjusted, boosts: tuple[Boost, ...],
            returning: tuple = ()) -> tuple[Step, ...]:
    """Plain-English steps from inputs to the final projection."""
    steps = []
    if rate is not None:
        prior_text = f", shrunk toward last season's {prior:.1f}" if prior is not None else ""
        steps.append(Step("Recent production", f"{rate:.1f} pts/game",
                          "Recent games count more (a game four weeks ago counts half); each game blends 60% actual "
                          f"and 40% usage-based expected points{prior_text}."))
    venue = "home" if matchup.home else "away"
    rank_text = f" (#{matchup.sos_rank} easiest vs {position})" if matchup.sos_rank else ""
    steps.append(Step("Matchup", f"x{matchup.sos_factor:.2f} defense, x{matchup.implied_factor:.2f} Vegas",
                      f"{venue} vs {matchup.opponent}: this defense gives up {abs(matchup.sos_factor - 1) * 100:.0f}% "
                      f"{'more' if matchup.sos_factor >= 1 else 'less'} than usual to {position}s after adjusting for "
                      f"the offenses it faced{rank_text}. "
                      + (f"Vegas implies {matchup.implied_total:.1f} team points vs a {matchup.team_ppg:.1f} average."
                         if matchup.implied_total and matchup.team_ppg else "No Vegas line yet.")))
    shares = source_shares(list(sources), weights)
    parts = []
    for key, value in sources.items():
        points = getattr(value, "points", 0.0)
        rank = getattr(value, "rank", None)
        rank_text = f" (rank {position}{rank:g})" if rank is not None else ""
        parts.append(f"{labels[key]} {points:.1f}{rank_text} x {shares.get(key, 0.0):.0%}")
    steps.append(Step("Source blend", f"{blended:.1f}", "; ".join(parts) + "."))
    steps.append(Step("Schedule tilt", f"x{tilt:.3f}",
                      "An extra lean on opponent-adjusted schedule strength, on top of what sources already price in."))
    for back in returning:
        if back.expected_loss > 0:
            steps.append(Step(f"{back.name} returning", f"-{back.expected_loss:.1f} pts (model)",
                              f"In {back.games_without} games without {back.name} this season he gained "
                              f"{back.expected_loss:.1f} expected pts/game; with {back.name} back, the model "
                              "takes that back out of its share of the blend."))
    for boost in boosts:
        steps.append(Step(f"If {boost.from_name} sits", f"+{boost.full_points:.1f} pts",
                          f"{boost.from_name}: {boost.from_status}. His vacated work is estimated by {boost.method}; "
                          f"weighted by his chance of sitting it adds {boost.expected_points:.1f}."))
    availability = adjusted.availability
    if availability.play_prob < 1:
        steps.append(Step("Availability", f"{availability.play_prob:.0%} to play",
                          f"{availability.label}. Historically, players with this designation and practice "
                          f"status played {availability.play_prob:.0%} as often as healthy starters."))
    if adjusted.floor is not None and adjusted.ceiling is not None:
        steps.append(Step("Range", f"{adjusted.floor:.1f} to {adjusted.ceiling:.1f}",
                          f"10th to 90th percentile outcomes for {position}s with this projection in the 2025 backtest."))
    steps.append(Step("Projection", f"{adjusted.projection:.1f}",
                      f"If active: {adjusted.if_active:.1f}" + (f", times {availability.play_prob:.0%} to play."
                                                                if availability.play_prob < 1 else ".")))
    return tuple(steps)
