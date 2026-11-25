"""Replay League trade chart: every player on one 0-1000 scale, rank-based (two good players can't add up
to a star).

A position rank becomes the ADP pick where that season's Nth player at the position went (`slot_curve`;
past the last drafted, PICKS_PAST_LAST picks per rank; fractional ranks interpolate), and a pick becomes
points on a convex curve (`pick_value` = 1000 x exp(-(pick - 1) / TAU)), so stars carry a premium. The Nth K/DST is
floored at pick KDST_FIRST_PICK + N (the ADP source takes them rounds too early). Nothing
depends on the weeks left: values move with injuries, form, expert ranks and a small bump once a player's
bye is behind him.

- Superflex leagues (`superflex_view`): the season's 2-QB ADP list when it is loaded (data.adp_format ==
  "2qb", replay_adp_format), used as is. Without it every QB's ADP (and its spread) is multiplied by
  SUPERFLEX_QB_ADP before anything above reads it: QB1 goes about pick 10 instead of 25, QB12 about 45
  instead of 110, close to real superflex drafts. The chart, pre_rank and the CPU draft all use it.
- pre_rank: overall order of preseason picks (the slot of his preseason expert rank, else of his ADP);
  star = clamp((STAR_TOP - pre_rank) / STAR_SPAN, 0, 1). A league drafted in week K (jump in now) takes the
  latest expert snapshot by week K instead: its "preseason" is its draft.
- model_ppg: ros_ppg + k x (mean of his last <= FORM_GAMES real games - ros_ppg), k = n / FORM_GAMES x
  FORM_WEIGHT, x (1 - FORM_STAR_DOWN x star) for a dip, x (1 + FORM_STAR_UP x star) for a rise while ros_ppg
  is below his prior.

- healthy_chart = w x value_at(expert rank) + (1 - w) x value_at(model rank), x BYE_BUMP once the bye is
  past, never above CHART_MAX (nor after an opportunity shock). The model rank is his fractional position
  rank on `model_ppg` (ros_ppg moved toward his last real games). w = EXPERT_WEIGHT for a snapshot at most
  EXPERT_FRESH_DAYS old, halved per further week; before the first in-season snapshot the preseason ranks
  stand in (ageing a week per week played). A season with no expert ranks at all (2018-19) has the ranks of
  ros_ppg (the sites' slow consensus) stand in at EXPERT_WEIGHT from week 1 (STAND_IN_EXPERTS): the model
  alone swung QBs 124 -> 495 -> 633 in three weeks; this halves the weekly moves and ranks no worse.
- chart: with a news report (replay_news), healthy_chart x share of games he plays x AVAILABILITY_SHARE (0 if
  out for the season; from his second week out only the model half is discounted, the experts already price
  it). Out without a report: healthy_chart x the OUT_SHARE availability replay_values gives him.

Pure: no database, no Knowledge.
"""
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType

from services import replay_news, replay_pool, replay_role, replay_shocks
from services.replay_pool import PoolPlayer, SeasonData

TAU = 40.0                     # pick_value decay: 1 -> 1000, 12 -> 760, 24 -> 563, 50 -> 294, 100 -> 84
CHART_MAX = 1000.0
PICKS_PAST_LAST = 3            # slot curve past the last drafted player: this many picks per rank
EMPTY_CURVE_START = 200.0      # a position nobody drafted starts here
KDST = ("K", "DST")
KDST_FIRST_PICK = 150.0        # K/DST slot curve starts no earlier than this pick (FFC ADP drafts them too early)
STAR_TOP, STAR_SPAN = 60, 48   # star = clamp((STAR_TOP - pre_rank) / STAR_SPAN, 0, 1)
FORM_GAMES = 3                 # model_ppg: his last real games
FORM_WEIGHT = 0.50
FORM_STAR_DOWN = 0.6           # a star's dip counts less
FORM_STAR_UP = 0.3             # a breakout below his prior counts more for a star
EXPERT_WEIGHT = 0.65           # blend weight on the expert rank for a snapshot <= EXPERT_FRESH_DAYS old
EXPERT_FRESH_DAYS = 10
UNLISTED_GAP = 10              # missing from an existing snapshot: last listed + this
BYE_BUMP = 1.03                # once his bye is behind him
AVAILABILITY_SHARE = 0.9       # chart x share_played x this, when a news report exists
DAYS_PER_WEEK = 7
SUPERFLEX_QB_ADP = 0.4         # superflex without a 2-QB ADP list: a QB's ADP x this
STAND_IN_EXPERTS = True        # a season with no expert ranks at all: ros_ppg ranks stand in at EXPERT_WEIGHT


@lru_cache(maxsize=64)
def slot_curve(data: SeasonData) -> Mapping[str, tuple[float, ...]]:
    """{position: (ADP pick of the 1st, 2nd, ... player drafted there)} for that season; the Nth K/DST
    (0-based n) no earlier than KDST_FIRST_PICK + n."""
    picks: dict[str, list[float]] = defaultdict(list)
    for player in data.players.values():
        if player.adp is not None:
            picks[player.position].append(player.adp)
    curve = {position: sorted(found) for position, found in picks.items()}
    for position in KDST:
        curve[position] = [max(pick, KDST_FIRST_PICK + n) for n, pick in enumerate(curve.get(position, ()))]
    return MappingProxyType({position: tuple(found) for position, found in curve.items() if found})


@lru_cache(maxsize=64)
def superflex_view(data: SeasonData) -> SeasonData:
    """The season as a superflex league sees it: every QB's ADP and spread x SUPERFLEX_QB_ADP (cached, so
    the curves built on it are cached too). A season read with the 2-QB ADP list already prices QBs so."""
    if data.adp_format == "2qb":
        return data

    def scaled(p: PoolPlayer) -> PoolPlayer:
        if p.position != "QB" or p.adp is None:
            return p
        stdev = p.adp_stdev * SUPERFLEX_QB_ADP if p.adp_stdev is not None else None
        return replace(p, adp=p.adp * SUPERFLEX_QB_ADP, adp_stdev=stdev)
    return replace(data, players=MappingProxyType({pid: scaled(p) for pid, p in data.players.items()}))


def clear_cache() -> None:
    superflex_view.cache_clear()
    slot_curve.cache_clear()
    _by_week.cache_clear()
    replay_news.clear_cache()
    replay_shocks.clear_cache()


replay_pool.EVICT_HOOKS.append(clear_cache)


def pick_value(pick: float) -> float:
    """Chart points for a draft pick: CHART_MAX at pick 1, decaying by TAU."""
    return CHART_MAX * math.exp(-(max(1.0, pick) - 1) / TAU)


def pick_at(curve: Mapping[str, Sequence[float]], position: str, rank: float) -> float:
    """The ADP pick of a (fractional, 1-based) position rank; past the last drafted, PICKS_PAST_LAST per rank."""
    picks = curve.get(position, ())
    rank = max(1.0, rank)
    if not picks:
        return EMPTY_CURVE_START + PICKS_PAST_LAST * (rank - 1)
    if rank >= len(picks):
        return picks[-1] + PICKS_PAST_LAST * (rank - len(picks))
    low = int(rank)                                 # 1-based; rank < len(picks) so low + 1 exists
    return picks[low - 1] + (rank - low) * (picks[low] - picks[low - 1])


def value_at(curve: Mapping[str, Sequence[float]], position: str, rank: float) -> float:
    """Chart points for a (fractional, 1-based) position rank, through the slot curve."""
    return pick_value(pick_at(curve, position, rank))


def model_ppg(ros_ppg: float, prior: float, recent_points: Sequence[float], star: float) -> float:
    """ros_ppg moved toward his recent real games (newest first), less so for a star's dip."""
    games = list(recent_points[:FORM_GAMES])
    if not games:
        return ros_ppg
    gap = sum(games) / len(games) - ros_ppg
    share = len(games) / FORM_GAMES * FORM_WEIGHT
    if gap < 0:
        share *= 1 - FORM_STAR_DOWN * star
    elif ros_ppg < prior:
        share *= 1 + FORM_STAR_UP * star
    return ros_ppg + share * gap


def star(pre_rank: int | None) -> float:
    """0-1: how much of a preseason star he was (1 inside the top 12, 0 from 60 on or unranked)."""
    if pre_rank is None:
        return 0.0
    return min(1.0, max(0.0, (STAR_TOP - pre_rank) / STAR_SPAN))


def fractional_ranks(scores: Mapping[int, float]) -> dict[int, float]:
    """{id: 1-based rank}. A lone score between two others sits between their ranks by where it falls between
    their scores (evenly spaced scores get 1, 2, 3, ...); the top, the bottom and every tied score take the
    first rank their score holds, however many share it."""
    ascending = sorted(scores.values())
    count = len(ascending)
    ranks: dict[int, float] = {}
    for pid, mine in scores.items():
        lower, higher = bisect_left(ascending, mine), count - bisect_right(ascending, mine)
        if higher == 0 or lower == 0 or count - higher - lower > 1:
            ranks[pid] = float(higher + 1)
            continue
        above, below = ascending[count - higher], ascending[lower - 1]     # ranks `higher` and `higher + 2`
        ranks[pid] = higher + 2 * (above - mine) / (above - below)
    return ranks


@lru_cache(maxsize=64)
def _by_week(data: SeasonData) -> Mapping[int, Mapping[int, int]]:
    weeks: dict[int, dict[int, int]] = defaultdict(dict)
    for (pid, week), rank in data.expert_ranks.items():
        weeks[week][pid] = rank
    return MappingProxyType(dict(weeks))


def pre_picks(data: SeasonData, drafted: int = 0) -> dict[int, float]:
    """{id: preseason pick}: the slot of his expert preseason rank, else his ADP's slot at his position (his
    ADP itself, except K/DST floored at KDST_FIRST_PICK). `drafted`: the league's draft week; a league drafted
    in week K (replay_state.draft_week) reads the latest expert snapshot by then instead of the preseason one."""
    weeks = _by_week(data)
    known = [w for w in weeks if w <= drafted]
    curve, ranked = slot_curve(data), weeks[max(known)] if known else {}
    by_adp = sorted((p.adp, pid) for pid, p in data.players.items() if p.adp is not None)
    seen: dict[str, int] = defaultdict(int)
    picks: dict[int, float] = {}
    for _, pid in by_adp:
        position = data.players[pid].position
        picks[pid] = curve[position][seen[position]]
        seen[position] += 1
    picks.update({pid: pick_at(curve, data.players[pid].position, rank)
                  for pid, rank in ranked.items() if pid in data.players})
    return picks


def pre_ranks(picks: Mapping[int, float]) -> dict[int, int]:
    """{id: overall preseason rank} from `pre_picks`."""
    return {pid: n for n, pid in enumerate(sorted(picks, key=lambda pid: (picks[pid], pid)), start=1)}


def expert_weight(age_days: float) -> float:
    return EXPERT_WEIGHT * 0.5 ** max(0.0, (age_days - EXPERT_FRESH_DAYS) / DAYS_PER_WEEK)


def snapshot(data: SeasonData, week: int) -> tuple[Mapping[int, int], float]:
    """({id: expert position rank}, blend weight) for the latest snapshot known at `week`; ({}, 0) if none."""
    weeks = _by_week(data)
    known = [w for w in weeks if w <= week]
    if not known:
        return {}, 0.0
    latest = max(known)
    age = data.expert_age.get(latest, 0) + DAYS_PER_WEEK * max(0, week - max(latest, 1))
    return weeks[latest], expert_weight(age)


def _bye_done(data: SeasonData, team: str | None, week: int) -> bool:
    return team is not None and any(data.schedule.opponent(team, w) is None for w in range(1, week))


def _game_weeks(data: SeasonData, team: str | None, week: int, end_week: int) -> list[int]:
    return [w for w in range(max(1, week), end_week + 1) if data.schedule.opponent(team, w) is not None]


def _injured(data: SeasonData, row: dict, news: "replay_news.Report | None", week: int, end_week: int,
             expert: float, model: float) -> tuple[float, dict]:
    """(chart, availability fields from the news) from his weighted expert and model parts."""
    if news is None:
        return (expert + model) * row["availability"], {}
    share = replay_news.share_played(news, _game_weeks(data, row["team"], week, end_week))
    extra = {"availability": round(share, 3), "expected_missed": round(row["games_left"] * (1 - share), 2)}
    discount = share * AVAILABILITY_SHARE
    if news.back is None:
        return 0.0, extra
    if week > news.since:                               # second week out on: the experts already price it
        return expert + model * discount, extra
    return (expert + model) * discount, extra


Parts = Mapping[int, tuple[float, float, tuple[int, int, str]]]    # {id: (expert part, model part, open streak)}


def charts(data: SeasonData, rows: Sequence[dict], priors: Mapping[int, float], week: int, end_week: int,
           seed: int, drafted: int = 0) -> tuple[dict[int, dict], str]:
    """({id: chart fields for ReplayValue (+ availability / expected_missed from news)}, rank_source).

    `rows` are replay_values rows (player_id, position, team, ros_ppg, recent_points, availability,
    games_left). `base_charts` then `with_news`: callers that value one week for many seeds cache the first."""
    fields, parts, source = base_charts(data, rows, priors, week, end_week, drafted)
    by_id = {row["player_id"]: row for row in rows}
    return {**fields, **with_news(data, by_id, fields, parts, week, end_week, seed)}, source


def base_charts(data: SeasonData, rows: Sequence[dict], priors: Mapping[int, float], week: int,
                end_week: int, drafted: int = 0) -> tuple[dict[int, dict], Parts, str]:
    """`charts` without the seeded news: (fields with news None, the parts `with_news` needs for players
    with an open hard-tag streak, rank_source). `drafted`: the league's draft week (see `pre_picks`)."""
    curve, picks = slot_curve(data), pre_picks(data, drafted)
    overall = pre_ranks(picks)
    listed, age_weight = snapshot(data, week)
    by_position: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_position[row["position"]].append(row)
    fields: dict[int, dict] = {}
    parts: dict[int, tuple[float, float, tuple[int, int, str]]] = {}
    source = "model"
    no_experts = not data.expert_ranks and STAND_IN_EXPERTS     # 2018-19: no expert ranks were ever loaded
    for position, group in by_position.items():
        ranks = [listed[r["player_id"]] for r in group if r["player_id"] in listed]
        w = age_weight if ranks else 0.0
        source = "experts" if w > 0 else source
        last = max(ranks, default=0)
        stars = {r["player_id"]: star(overall.get(r["player_id"])) for r in group}
        form = {r["player_id"]: model_ppg(r["ros_ppg"], priors.get(r["player_id"], 0.0), r["recent_points"],
                                          stars[r["player_id"]]) for r in group}
        model = fractional_ranks(replay_role.model_scores(data, group, form, week))
        plain = fractional_ranks({r["player_id"]: r["ros_ppg"] for r in group})
        consensus = {pid: float(listed.get(pid, last + UNLISTED_GAP)) for pid in stars}
        if no_experts and week > 0:             # the sites' own slow consensus (ros_ppg) stands in for the experts
            consensus, w = plain, EXPERT_WEIGHT
        for row in group:
            pid = row["player_id"]
            bump = BYE_BUMP if _bye_done(data, row["team"], week) else 1.0
            expert = w * value_at(curve, position, consensus[pid]) * bump if w else 0.0
            model_value = value_at(curve, position, model[pid])
            model_part = (1 - w) * model_value * bump
            if expert + model_part > CHART_MAX:         # the bye bump never lifts anyone past the top of the scale
                expert, model_part = (part * CHART_MAX / (expert + model_part) for part in (expert, model_part))
            streak = replay_news.open_streak(data, pid, week)
            if streak is not None:
                parts[pid] = (expert, model_part, streak)
            chart, _ = _injured(data, row, None, week, end_week, expert, model_part)
            fields[pid] = {
                "chart": round(chart, 1), "healthy_chart": round(expert + model_part, 1),
                "pre_chart": round(pick_value(picks[pid]), 1) if pid in picks else 0.0,
                "expert_rank": listed.get(pid), "pre_rank": overall.get(pid), "star": round(stars[pid], 3),
                "form_chart": round((1 - w) * (model_value - value_at(curve, position, plain[pid])) * bump, 1),
                "news": None,
            }
    return fields, parts, source


def with_news(data: SeasonData, rows: Mapping[int, dict], fields: Mapping[int, dict], parts: Parts, week: int,
              end_week: int, seed: int) -> dict[int, dict]:
    """{id: fields} for the players in `parts` (their seeded news report and the chart it gives) and the
    players an opportunity shock moves (replay_shocks: the market's reaction, which reads that news)."""
    out: dict[int, dict] = {}
    for pid, (expert, model_part, (since, back, tag)) in parts.items():
        news = replay_news.report(seed, pid, since, back, week, tag)
        chart, extra = _injured(data, rows[pid], news, week, end_week, expert, model_part)
        out[pid] = {**fields[pid], **extra, "chart": round(chart, 1), "news": news}
    return {**out, **_shocked(data, rows, fields, out, week)}


def healthy_ranks(rows: Mapping[int, dict], healthy: Mapping[int, float]) -> dict[int, float]:
    """{id: fractional position rank on the healthy chart} for the positions shocks move."""
    by_position: dict[str, dict[int, float]] = defaultdict(dict)
    for pid, row in rows.items():
        if row["position"] in replay_shocks.POSITIONS and pid in healthy:
            by_position[row["position"]][pid] = healthy[pid]
    return {pid: rank for group in by_position.values() for pid, rank in fractional_ranks(group).items()}


def _shocked(data: SeasonData, rows: Mapping[int, dict], fields: Mapping[int, dict], news: Mapping[int, dict],
             week: int) -> dict[int, dict]:
    """{id: fields} for the players an opportunity shock moves: healthy_chart by the shock, chart in proportion."""
    if not replay_shocks.events(data, week):
        return {}
    availability = {pid: row["availability"] for pid, row in rows.items()}
    availability.update({pid: f["availability"] for pid, f in news.items() if "availability" in f})
    healthy = {pid: f["healthy_chart"] for pid, f in fields.items()}
    curve = slot_curve(data)
    out: dict[int, dict] = {}
    found = replay_shocks.moves(data, week, availability, healthy, healthy_ranks(rows, healthy),
                                lambda position, rank: value_at(curve, position, rank))
    for pid, (delta, shock) in found.items():
        now = news.get(pid, fields[pid])
        moved = min(CHART_MAX, max(0.0, now["healthy_chart"] + delta))
        chart = now["chart"] * moved / now["healthy_chart"]          # > 0: moves() scales delta by it
        other = data.players[shock.other].name if shock.other in data.players else None
        missed = 1.0 - availability.get(shock.other, 1.0)
        hype = replay_shocks.reason(shock, other, missed) if delta >= replay_shocks.STAR_MIN else None
        out[pid] = {**now, "healthy_chart": round(moved, 1), "chart": round(chart, 1),
                    "shock_chart": round(delta, 1), "shock": shock.kind, "hype_reason": hype}
    return out
