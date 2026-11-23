"""Replay League opportunity-shock calibration (read-only): does the trade chart move now to where the market
(the experts' ranks over the next weeks) settled, and what does that cost the forecast?

    python -m services.replay_shock_eval --seasons 2018-2025 [--db path] [--scoring half]

Offline only: the market's later ranks and the real points from the event on are read here, never at
runtime. Steps: fit SHARE_FIT (week 1-2 opportunity share on the log of the preseason rank), catalogue every
shock (replay_shocks.events, first week each is live), measure the market move y = log(settled rank) -
log(r) where r is the chart's healthy position rank at the event week without shocks, and settled the
market's rank over the next 1-3 weeks: the mean of the log expert rank (snapshots) and the log projection
rank (each week's projection among his position), each less the same gap for the players without a shock
ranked inside CONTROL_RANKS that week (so only what the event adds counts). GAMMA per (kind, position) is
fitted by least squares on x = m x (log toward - log r) leaving each season out; then the chart's rank at
the event week with and without shocks is compared with that market (lead metrics), and the chart with
real points from then on (forecast metrics).
"""
import argparse
import math
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import mean, median

from services import replay_chart, replay_pool, replay_shocks as rs, replay_state, replay_values as rv
from services.replay_pool import SeasonData
from services.replay_value_eval import DEFAULT_DB, Week, add_expert_rows, parse_seasons, read_only_copy

WEEKS = range(2, 15)
FORECAST_WEEKS = range(2, 12)
SHARE_RANKS = 80                 # SHARE_FIT: players ranked inside this before the season
MIN_FIT = 4                      # events needed to fit a (kind, position) on its own; else the kind's pooled fit
MAX_GAMMA = 1.5
CONTROL_RANKS = 60               # players without a shock ranked inside this give each week's market baseline


@dataclass
class Event:
    season: int
    shock: rs.Shock
    missed: float
    settled: float | None                  # the market's rank 1-3 weeks on, baseline removed (None: no market)
    name: str
    other: str
    before: float                          # his healthy-chart position rank at the event week, no shocks
    real: int                              # his position rank on real points from the event week on

    @property
    def x(self) -> float:
        return self.missed * (math.log(self.shock.toward) - math.log(self.before))

    @property
    def y(self) -> float:
        return math.log(self.settled) - math.log(self.before)


@contextmanager
def gammas(table: dict):
    old = dict(rs.GAMMA)
    rs.GAMMA.update(table)
    try:
        yield
    finally:
        rs.GAMMA.update(old)


def fit_shares(seasons: dict[int, SeasonData]) -> dict[str, tuple[float, float]]:
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for data in seasons.values():
        at = data.as_of(3)
        pre = rs.market_ranks(at, 1)
        for pid, share in rs.shares(at, 3).items():
            if pre.get(pid, SHARE_RANKS + 1) <= SHARE_RANKS:
                points[at.players[pid].position].append((math.log(pre[pid]), share))
    fit = {}
    for position, found in points.items():
        mx, my = mean(x for x, _ in found), mean(y for _, y in found)
        b = sum((x - mx) * (y - my) for x, y in found) / sum((x - mx) ** 2 for x, _ in found)
        fit[position] = (round(my - b * mx, 3), round(b, 3))
    return fit


@lru_cache(maxsize=512)
def projection_ranks(data: SeasonData, week: int) -> dict[int, int]:
    by_position: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for (pid, w), points in data.projected.items():
        if w == week and points > 0 and pid in data.players:
            by_position[data.players[pid].position].append((-points, pid))
    return {pid: n for group in by_position.values() for n, (_, pid) in enumerate(sorted(group), start=1)}


def market(data: SeasonData, pid: int, week: int) -> tuple[float | None, float | None]:
    """(mean log expert rank, mean log projection rank) over the 1-3 weeks after `week`; None where missing."""
    later = range(week + 1, week + 4)
    experts = [math.log(rs.market_ranks(data, w + 1)[pid]) for w in later
               if w in data.expert_age and pid in rs.market_ranks(data, w + 1)]
    projected = [math.log(projection_ranks(data, w)[pid]) for w in later if pid in projection_ranks(data, w)]
    return (mean(experts) if experts else None), (mean(projected) if projected else None)


def baseline(data: SeasonData, before: dict[int, float], week: int, skip: set[int]) -> dict[str, tuple[float, float]]:
    """{position: (median expert gap, median projection gap)} to the chart for players without a shock."""
    gaps: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for pid, rank in before.items():
        if rank <= CONTROL_RANKS and pid not in skip:
            for found, gap in zip(gaps[data.players[pid].position], market(data, pid, week)):
                if gap is not None:
                    found.append(gap - math.log(rank))
    return {position: tuple(median(g) if g else 0.0 for g in pair) for position, pair in gaps.items()}


def settled_rank(data: SeasonData, pid: int, week: int, base: tuple[float, float]) -> float | None:
    found = [log_rank - bias for log_rank, bias in zip(market(data, pid, week), base) if log_rank is not None]
    return math.exp(mean(found)) if found else None


def ranks(week: Week) -> dict[int, float]:
    """{id: healthy-chart fractional position rank} at this week with the current GAMMA."""
    charted = week.charts()
    return replay_chart.healthy_ranks({r["player_id"]: r for r in week.rows},
                                      {pid: c["healthy_chart"] for pid, c in charted.items()})


def catalogue(season: int, data: SeasonData, settings: replay_state.Settings, weeks: dict[int, Week]) -> list[Event]:
    league, events = rv.league_settings(settings), []
    for n in WEEKS:
        with gammas(dict.fromkeys(rs.GAMMA, 0.0)):
            before = ranks(weeks[n])
        know = rv.knowledge(data, league, n, settings.end_week)
        shocks = [shock for shock in rs.events(data, n) if shock.week == n and shock.player_id in before]
        base = baseline(data, before, n, {shock.player_id for shock in rs.events(data, n)})
        for shock in shocks:
            gone = know.by_id.get(shock.other) if shock.other else None
            missed = 1.0 - gone.availability if shock.kind == "teammate_out" and gone else 1.0
            if missed < rs.MIN_MISSED or (shock.toward < before[shock.player_id]) != shock.up:
                continue
            settled = settled_rank(data, shock.player_id, n, base.get(shock.position, (0.0, 0.0)))
            events.append(Event(season, shock, missed, settled,
                                data.players[shock.player_id].name, gone.name if gone else "",
                                before[shock.player_id], _ros_rank(data, shock.player_id, n, settings.end_week)))
    return events


def fit_gamma(events: list[Event]) -> dict[tuple[str, str], float]:
    def ls(found: list[Event]) -> float:
        sxx = sum(e.x ** 2 for e in found)
        return min(MAX_GAMMA, max(0.0, sum(e.x * e.y for e in found) / sxx)) if sxx else 0.0
    usable = [e for e in events if e.settled is not None]
    table = {}
    for kind, position in rs.GAMMA:
        own = [e for e in usable if e.shock.kind == kind and e.shock.position == position]
        table[(kind, position)] = round(ls(own if len(own) >= MIN_FIT else [e for e in usable if e.shock.kind == kind]), 2)
    return table


def _rank(charts: dict[int, dict], data: SeasonData, pid: int) -> int:
    position, mine = data.players[pid].position, charts[pid]["chart"]
    return 1 + sum(1 for p, c in charts.items() if c["chart"] > mine and data.players[p].position == position)


def _ros_rank(data: SeasonData, pid: int, week: int, end: int) -> int:
    totals: dict[int, float] = defaultdict(float)
    for (p, w), points in data.actual.items():
        if week <= w <= end and data.players[p].position == data.players[pid].position:
            totals[p] += points
    return 1 + sum(1 for t in totals.values() if t > totals.get(pid, 0.0))


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = mean(a), mean(b)
    sa, sb = math.sqrt(sum((x - ma) ** 2 for x in a)), math.sqrt(sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa and sb else float("nan")


def lead_report(events: list[Event], weeks: dict[int, dict[int, Week]], held: dict[int, dict]) -> None:
    rows = []
    for e in events:
        if e.settled is not None:
            with gammas(held[e.season]):
                rows.append((e, ranks(weeks[e.season][e.shock.week])[e.shock.player_id]))
    print("\nLEAD (leave-season-out GAMMA): healthy-chart rank at the event week vs the market 1-3 weeks on "
          "(experts and projections, baseline removed)")
    print("kind          pos   n | corr(move, market move) | mean |rank - market| no shock -> shock | "
          "forecast: mean |rank - real ROS rank|, moved closer")
    groups = defaultdict(list)
    for row in rows:
        groups[(row[0].shock.kind, row[0].shock.position)].append(row)
        groups[("ALL", "")].append(row)
    for (kind, position), found in sorted(groups.items()):
        y = [e.y for e, _ in found]
        moved = [math.log(after) - math.log(e.before) for e, after in found]
        eb, ea = mean(abs(e.before - e.settled) for e, _ in found), mean(abs(a - e.settled) for e, a in found)
        fb, fa = mean(abs(e.before - e.real) for e, _ in found), mean(abs(a - e.real) for e, a in found)
        shifted = [(e, a) for e, a in found if abs(a - e.before) >= 0.5]
        closer = sum(abs(a - e.real) < abs(e.before - e.real) for e, a in shifted)
        print(f"{kind:<13} {position:<3} {len(found):>3} | {corr(moved, y):>5.2f} | {eb:>5.1f} -> {ea:>5.1f} | "
              f"{fb:>5.1f} -> {fa:>5.1f}, {closer}/{len(shifted)}")


def forecast_report(weeks: dict[int, dict[int, Week]], held: dict[int, dict]) -> None:
    print("\nFORECAST: mean Spearman (chart vs real points over the last starter from then on), weeks 2-11")
    for season, found in weeks.items():
        chosen = [w for n, w in found.items() if n in FORECAST_WEEKS]
        with gammas(dict.fromkeys(rs.GAMMA, 0.0)):
            before = mean(w.spearman() for w in chosen)
        with gammas(held[season]):
            after = mean(w.spearman() for w in chosen)
        print(f"{season}: {before:.4f} -> {after:.4f} ({after - before:+.4f})")


def example(name: str, season: int, data: SeasonData, weeks: dict[int, Week], span: range, final: dict) -> None:
    pid = next((p for p, pl in data.players.items() if pl.name.startswith(name)), None)
    if pid is None:
        return
    cells = []
    for n in span:
        week = weeks[n]
        if pid not in week.data.players:
            continue
        with gammas(dict.fromkeys(rs.GAMMA, 0.0)):
            off = week.charts()
        with gammas(final):
            on = week.charts()
        pos = data.players[pid].position
        cells.append(f"w{n} {off[pid]['chart']:.0f} {pos}{_rank(off, week.data, pid)} -> {on[pid]['chart']:.0f} "
                     f"{pos}{_rank(on, week.data, pid)}{' [' + on[pid]['shock'] + ']' if on[pid].get('shock') else ''}"
                     f" (experts {data.expert_ranks.get((pid, n), '-')})")
    print(f"{season} {name}: " + "; ".join(cells))


EXAMPLES = ((2025, "Javonte Williams", range(1, 7)), (2025, "Miles Sanders", range(1, 6)),
            (2024, "Mike Evans", range(6, 13)), (2024, "Chris Godwin", range(6, 10)),
            (2025, "Davante Adams", range(5, 11)), (2025, "Puka Nacua", range(5, 10)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seasons", default="2018-2025")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--scoring", default="half")
    args = parser.parse_args()
    years = parse_seasons(args.seasons)
    session = read_only_copy(args.db)
    add_expert_rows(session, years)
    seasons = {s: replay_pool.load_season_data(session, s, args.scoring) for s in years}
    settings = {s: replay_state.Settings(season=s, scoring=args.scoring) for s in years}
    rs.SHARE_FIT.update(fit_shares(seasons))
    rs.clear_cache()
    print(f"SHARE_FIT = {rs.SHARE_FIT}")
    weeks = {s: {w: Week(seasons[s], settings[s], w) for w in range(1, 15)} for s in years}
    events = [e for s in years for e in catalogue(s, seasons[s], settings[s], weeks[s])]
    counts = Counter((e.season, e.shock.kind) for e in events)
    print("\nEVENTS per season: " + "; ".join(f"{s}: " + ", ".join(f"{k} {counts[(s, k)]}" for k in sorted(
        {k for _, k in counts}) if counts[(s, k)]) for s in years))
    final = fit_gamma(events)
    held = {s: fit_gamma([e for e in events if e.season != s]) for s in years}
    print(f"\nGAMMA (all seasons) = {final}")
    for kind, position in final:
        found = [e for e in events if e.settled is not None and (e.shock.kind, e.shock.position) == (kind, position)]
        if found:
            print(f"  {kind:<13} {position}: n={len(found):>3} median move {median(e.y for e in found):+.2f} log ranks, "
                  f"median x {median(e.x for e in found):+.2f}, gamma {final[(kind, position)]}")
    lead_report(events, weeks, held)
    forecast_report(weeks, held)
    print("\nEXAMPLES (value, position rank: without shocks -> with)")
    for season, name, span in EXAMPLES:
        if season in seasons:
            example(name, season, seasons[season], weeks[season], span, final)
    shown = {name for _, name, _ in EXAMPLES}
    print("\nBIGGEST OTHER SHOCKS (final GAMMA; settled = the experts 1-3 weeks on)")
    sized = []
    for e in events:
        if e.name in shown:
            continue
        with gammas(final):
            target = rs.target_rank(e.shock, e.before, e.missed)
        sized.append((abs(math.log(target) - math.log(e.before)), e, target))
    for _, e, target in sorted(sized, key=lambda t: -t[0])[:10]:
        real = e.real
        experts = market(seasons[e.season], e.shock.player_id, e.shock.week)[0]
        settled = f"{e.settled:.0f}" if e.settled else "none"
        settled += f" (experts {math.exp(experts):.0f})" if experts is not None else ""
        print(f"{e.season} wk{e.shock.week} {e.name} ({e.shock.position}) {e.shock.kind} [{e.other}]: "
              f"market before {e.shock.anchor:.0f}, chart {e.before:.0f} -> {target:.0f} now; market {settled}; real ROS rank {real}")
    replay_chart.clear_cache()


if __name__ == "__main__":
    main()
