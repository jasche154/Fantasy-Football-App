"""Replay League trade chart: every player on one 0-1000 scale, rank-based (two good players can't add up
to a star).

A position rank becomes the ADP pick where that season's Nth player at the position went (`slot_curve`;
past the last drafted, PICKS_PAST_LAST picks per rank; fractional ranks interpolate), and a pick becomes
points on a convex curve (`pick_value` = 1000 x exp(-(pick - 1) / TAU)), so stars carry a premium. The Nth K/DST is
floored at pick KDST_FIRST_PICK + N (the ADP source takes them rounds too early). Nothing
depends on the weeks left: values move with injuries, form, expert ranks and a small bump once a player's
bye is behind him.

- Superflex leagues (`superflex_view` = replay_superflex.view): RBs, WRs, TEs, Ks and DSTs on the league-scoring
  1-QB list exactly as in a regular league; each QB at the pick of the non-QB slot the real 2-QB list puts him
  in (the fitted average slots without a 2-QB list). The chart, pre_rank and the CPU draft all use it. `star`
  reads a non-QB's rank in the 1-QB draft (replay_superflex.one_qb), so his chart is the regular league's.
- pre_rank: overall order of preseason picks (the slot of his preseason expert rank, else of his ADP);
  star = clamp((STAR_TOP - pre_rank) / STAR_SPAN, 0, 1). A league drafted in week K (jump in now) takes the
  latest expert snapshot by week K instead: its "preseason" is its draft.
- model_ppg: ros_ppg + k x (mean of his last <= FORM_GAMES real games - ros_ppg), k = n / FORM_GAMES x
  FORM_WEIGHT, x (1 - FORM_STAR_DOWN x star) for a dip, x (1 + FORM_STAR_UP x star) for a rise while ros_ppg
  is below his prior. FORM_GAMES = 5 is the user's choice ("values should swing a little less"), informed by real
  2020-25 weeks: after a big (25%+) one-week move with no news behind it the old value was closer to his next four
  weeks 71% of the time. Against the old chart (3 games at FORM_WEIGHT 0.5), a real 5-game window (recent_points
  carries replay_values.RECENT_GAMES = 5) cuts those no-news moves from 1,617 to 1,335 (-17%; 3 games at weight
  0.3 cut them only 12%), at held-out RMSE 4.030 -> 4.037 regular (out players 3.160 -> 3.164) and 5.020 -> 5.019
  superflex (out players 3.857 -> 3.842) on the same player-weeks.

- healthy_chart = w x value_at(expert rank) + (1 - w) x value_at(model rank), x BYE_BUMP once the bye is
  past, never above CHART_MAX (nor after an opportunity shock). The model rank is his fractional position
  rank on `model_ppg` (ros_ppg moved toward his last real games). w = EXPERT_WEIGHT for a snapshot at most
  EXPERT_FRESH_DAYS old, halved per further week; before the first in-season snapshot the preseason ranks
  stand in (ageing a week per week played). A season with no expert ranks at all (2018-19) has the ranks of
  ros_ppg (the sites' slow consensus) stand in at EXPERT_WEIGHT from week 1 (STAND_IN_EXPERTS): the model
  alone swung QBs 124 -> 495 -> 633 in three weeks; this halves the weekly moves and ranks no worse.
- chart: healthy_chart x (1 - replay_chart_injury.discount(games he misses, games left)): with a news report
  (replay_news) the games before the reported return (a season-ending report, or a return after the league's
  last week, keeps replay_chart_injury.SEASON_REPORT_KEEP); out without one, unreported_discount. The whole
  chart is discounted: while he is out it is his frozen pre-absence value (replay_values._frozen). The
  discount follows the user's targets (out 1-4 weeks 5-12% off, 6+ weeks 35-45%), not the share of games
  missed: availability and expected_missed (lineups, waivers) stay the real expected games.

Pure: no database, no Knowledge.
"""
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from types import MappingProxyType

from services import (replay_chart_injury, replay_fillin, replay_league_size, replay_news, replay_pool, replay_role,
                      replay_shocks, replay_superflex)
from services.replay_pool import SeasonData

TAU = 40.0                     # pick_value decay: 1 -> 1000, 12 -> 760, 24 -> 563, 50 -> 294, 100 -> 84
CHART_MAX = 1000.0
PICKS_PAST_LAST = 3            # slot curve past the last drafted player: this many picks per rank
EMPTY_CURVE_START = 200.0      # a position nobody drafted starts here
KDST = ("K", "DST")
KDST_FIRST_PICK = 150.0        # K/DST slot curve starts no earlier than this pick (FFC ADP drafts them too early)
STAR_TOP, STAR_SPAN = 60, 48   # star = clamp((STAR_TOP - pre_rank) / STAR_SPAN, 0, 1)
FORM_GAMES = 5                 # model_ppg: his last real games (the user's choice, module docstring)
FORM_WEIGHT = 0.50
FORM_STAR_DOWN = 0.6           # a star's dip counts less
FORM_STAR_UP = 0.3             # a breakout below his prior counts more for a star
EXPERT_WEIGHT = 0.65           # blend weight on the expert rank for a snapshot <= EXPERT_FRESH_DAYS old
EXPERT_FRESH_DAYS = 10
UNLISTED_GAP = 10              # missing from an existing snapshot: last listed + this
BYE_BUMP = 1.03                # once his bye is behind him
DAYS_PER_WEEK = 7
HYPE_ESTABLISHED = 24          # a role shock's "he won the backfield" never for a player already this high at his
                               # position (James Cook, Derrick Henry), nor for one who is out
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


superflex_view = replay_superflex.view          # the season as a superflex league values it (cached)


def clear_cache() -> None:
    replay_superflex.clear_cache()
    slot_curve.cache_clear()
    _by_week.cache_clear()
    replay_news.clear_cache()
    replay_shocks.clear_cache()
    replay_fillin.history.cache_clear()
    replay_fillin.fill_ins.cache_clear()
    replay_league_size.sized.cache_clear()


replay_pool.EVICT_HOOKS.append(clear_cache)


def pick_value(pick: float) -> float:
    """Chart points for a draft pick: CHART_MAX at pick 1, decaying by TAU."""
    return CHART_MAX * math.exp(-(max(1.0, pick) - 1) / TAU)


def chart_pick(value: float) -> float | None:
    """The draft pick `pick_value` gives this many chart points (None for 0: undraftable)."""
    return 1.0 + TAU * math.log(CHART_MAX / value) if value > 0 else None


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


def _games_missed(data: SeasonData, team: str | None, since: int, week: int) -> int:
    """His team's games from the first week of his absence to the week before `week`."""
    return sum(data.schedule.opponent(team, w) is not None for w in range(max(1, since), week)) if team else 0


def _game_weeks(data: SeasonData, team: str | None, week: int, end_week: int) -> list[int]:
    return [w for w in range(max(1, week), end_week + 1) if data.schedule.opponent(team, w) is not None]


def _injured(data: SeasonData, row: dict, news: "replay_news.Report | None", week: int, end_week: int,
             expert: float, model: float) -> tuple[float, dict]:
    """(chart, availability fields from the news) from his weighted expert and model parts."""
    if news is None:
        if not row["out"]:
            return expert + model, {}
        injury = row["injury"]
        ir_tag = injury is not None and injury.tag in ("IR", "PUP")
        return (expert + model) * (1 - replay_chart_injury.unreported_discount(row["games_left"], ir_tag)), {}
    games = _game_weeks(data, row["team"], week, end_week)
    share = replay_news.share_played(news, games)
    extra = {"availability": round(share, 3), "expected_missed": round(row["games_left"] * (1 - share), 2)}
    if news.back is None or share == 0:                 # season-long (or back after the league's last week); may be wrong
        return (expert + model) * (1 - replay_chart_injury.season_report_discount(len(games))), extra
    return (expert + model) * (1 - replay_chart_injury.discount(len(games) * (1 - share), len(games))), extra


# {id: (expert part, model part, open streak, team games already missed in a frozen absence or None)}
Parts = Mapping[int, tuple[float, float, tuple[int, int, str], int | None]]


def charts(data: SeasonData, rows: Sequence[dict], priors: Mapping[int, float], week: int, end_week: int,
           seed: int, drafted: int = 0) -> tuple[dict[int, dict], str]:
    """({id: chart fields for ReplayValue (+ availability / expected_missed from news)}, rank_source).

    `rows` are replay_values rows (player_id, position, team, ros_ppg, recent_points, availability,
    games_left). `base_charts` then `with_news`: callers that value one week for many seeds cache the first."""
    fields, parts, source = base_charts(data, rows, priors, week, end_week, drafted)
    by_id = {row["player_id"]: row for row in rows}
    return {**fields, **with_news(data, by_id, fields, parts, week, end_week, seed)}, source


def base_charts(data: SeasonData, rows: Sequence[dict], priors: Mapping[int, float], week: int,
                end_week: int, drafted: int = 0, regular: SeasonData | None = None,
                frozen: Mapping[int, float] | None = None, live: dict[int, float] | None = None,
                since: Mapping[int, int] | None = None,
                board: SeasonData | None = None) -> tuple[dict[int, dict], Parts, str]:
    """`charts` without the seeded news: (fields with news None, the parts `with_news` needs for players
    with an open hard-tag streak, rank_source). `drafted`: the league's draft week (see `pre_picks`).
    `regular`: a superflex league's season on the 1-QB list (replay_superflex.one_qb), whose overall ranks
    set a non-QB's `star`, so the SUPERFLEX slot changes only the QBs. `frozen`: {id: healthy chart} for the
    players in an injury absence (replay_values), standing in for the expert and model parts; their live
    healthy chart goes into `live` when given; `since`: the first week of each frozen absence, so the frozen value
    is scaled by `replay_chart_injury.return_factor` of the games missed (`with_news` uses the reported length).
    `board`: the season whose ADP sets the preseason picks (pre_rank, pre_chart, `star`) when `data` is a view
    that moves ADP only to change the value curve (replay_league_size.sized); default `data`."""
    frozen, since = frozen or {}, since or {}
    curve, picks = slot_curve(data), pre_picks(board or data, drafted)
    overall = pre_ranks(picks)
    star_ranks = overall if regular is None else {
        **pre_ranks(pre_picks(regular, drafted)),
        **{pid: rank for pid, rank in overall.items() if data.players[pid].position == "QB"}}
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
        stars = {r["player_id"]: star(star_ranks.get(r["player_id"])) for r in group}
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
            missed = None
            if pid in frozen:                           # out: his value before the absence (replay_values._frozen)
                if live is not None:
                    live[pid] = expert + model_part
                expert, model_part = min(CHART_MAX, frozen[pid] * bump), 0.0
                missed = _games_missed(data, row["team"], since.get(pid, week), week)
            streak = replay_news.open_streak(data, pid, week)
            if streak is not None:
                parts[pid] = (expert, model_part, streak, missed)
            if missed is not None:                      # what he comes back worth (the length known so far)
                expert *= replay_chart_injury.return_factor(missed)
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
              end_week: int, seed: int, regular: SeasonData | None = None) -> dict[int, dict]:
    """{id: fields} for the players in `parts` (their seeded news report and the chart it gives) and the
    players an opportunity shock moves (replay_shocks: the market's reaction, which reads that news).
    `regular`: the 1-QB season the fill-ins are read on (`_filling`); default `data`."""
    out: dict[int, dict] = {}
    for pid, (expert, model_part, (since, back, tag), missed) in parts.items():
        started = data.injuries.get((pid, since))          # the streak's first tag sets its news group
        first = started.tag if started is not None else tag
        news = replay_news.report(seed, pid, since, back, week, tag, data.last_week, first)
        if news.vague:                                  # "no timetable yet": the market prices the whole spread
            _, extra = _injured(data, rows[pid], news, week, end_week, expert, model_part)
            chart = (expert + model_part) * _vague_keep(data, rows[pid], news, first, week, end_week, missed)
            out[pid] = {**fields[pid], **extra, "chart": round(chart, 1), "news": news}
            continue
        if missed is not None:                          # frozen: what he comes back worth after the reported absence
            games = _game_weeks(data, rows[pid]["team"], week, end_week)
            left = len(games) * (1 - replay_news.share_played(news, games)) if news.back else len(games)
            factor = replay_chart_injury.return_factor(missed + left)
            expert, model_part = expert * factor, model_part * factor
        chart, extra = _injured(data, rows[pid], news, week, end_week, expert, model_part)
        out[pid] = {**fields[pid], **extra, "chart": round(chart, 1), "news": news}
    shocked = {**out, **_shocked(data, rows, fields, out, week)}
    return {**shocked, **_filling(data, rows, {**fields, **shocked}, week, end_week, seed, regular)}


def _filling(data: SeasonData, rows: Mapping[int, dict], fields: Mapping[int, dict], week: int,
             end_week: int, seed: int, regular: SeasonData | None = None) -> dict[int, dict]:
    """{id: fields} of the fill-in QBs (replay_fillin): share x the value of his rank without the form bonus +
    (1 - share) x a backup's (the QB ranked just past one per team), never above his own chart. A graduate
    (replay_fillin.graduated: past probation, his regular not expected back) keeps his form: his share of an
    established starter's starts x his chart + the rest x a backup's. `share` reads the regular starter's news,
    so it is the same report his injured chart is priced on. Who fills in, and his `prior` group, are read on
    `regular` (the 1-QB season, unmoved by the superflex view or the league-size taper: what the fit read); only
    the backup's value comes off `data`'s curve."""
    found = replay_fillin.fill_ins(regular or data, week)
    if not found:
        return {}
    curve = slot_curve(data)
    backup = value_at(curve, "QB", len({team for team, _ in data.schedule.opponents}) + 1)
    out: dict[int, dict] = {}
    for pid, fill in found.items():
        if pid not in fields or pid not in rows:
            continue
        now = fields[pid]
        news = replay_fillin.regular_news(data, fill, week, seed)       # the report his injured chart reads
        played = replay_fillin.share(fill, news, _game_weeks(data, rows[pid]["team"], week, end_week))
        if played is None:                              # past probation, his regular not expected back: the team's QB
            continue
        if replay_fillin.graduated(fill, news):         # the team's QB on a slower climb: his form counts
            chart = played * now["chart"] + (1 - played) * min(backup, now["chart"])
            out[pid] = {**now, "chart": round(chart, 1), "fill_in": round(played, 3)}
            continue
        rank_value = now["healthy_chart"] - now.get("form_chart", 0.0)
        chart = min(now["chart"], played * rank_value + (1 - played) * min(backup, rank_value))
        out[pid] = {**now, "chart": round(chart, 1), "healthy_chart": round(chart, 1), "form_chart": 0.0,
                    "fill_in": round(played, 3)}
    return out


def _vague_keep(data: SeasonData, row: dict, news: "replay_news.Report", tag: str, week: int, end_week: int,
                missed: int | None) -> float:
    """The expected share of his (frozen) value kept over the public spread of return weeks (replay_news.vague_odds):
    the return factor of the whole absence (frozen players only) x (1 - the discount of the games he misses)."""
    games = _game_weeks(data, row["team"], week, end_week)
    expected = 0.0
    for back, chance in replay_news.vague_odds(news.since, week, tag):
        left = sum(w < back for w in games)
        keep = 1 - replay_chart_injury.discount(left, len(games)) if games else 1.0
        factor = replay_chart_injury.return_factor(missed + left) if missed is not None else 1.0
        expected += chance * factor * keep
    return expected


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
    ranks = healthy_ranks(rows, healthy)
    found = replay_shocks.moves(data, week, availability, healthy, ranks,
                                lambda position, rank: value_at(curve, position, rank))
    for pid, (delta, shock) in found.items():
        now = news.get(pid, fields[pid])
        moved = min(CHART_MAX, max(0.0, now["healthy_chart"] + delta))
        chart = now["chart"] * moved / now["healthy_chart"]          # > 0: moves() scales delta by it
        other = data.players[shock.other].name if shock.other in data.players else None
        missed = 1.0 - availability.get(shock.other, 1.0)
        hyped = (delta >= replay_shocks.STAR_MIN and not now.get("out", rows[pid].get("out"))
                 and not (shock.kind == "role_won" and shock.anchor <= HYPE_ESTABLISHED))
        hype = replay_shocks.reason(shock, other, missed) if hyped else None
        out[pid] = {**now, "healthy_chart": round(moved, 1), "chart": round(chart, 1),
                    "shock_chart": round(delta, 1), "shock": shock.kind, "hype_reason": hype}
    return out
