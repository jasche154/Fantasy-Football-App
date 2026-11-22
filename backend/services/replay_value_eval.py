"""Replay League chart calibration (read-only): does the trade chart order players by what they really scored
from then on?

    python -m services.replay_value_eval --seasons 2020-2025 [--db path] [--scoring half]

The database is opened read-only and copied into memory; when it has no `replay_expert_ranks` rows, the
FantasyPros archive is downloaded (replay_experts) and matched in memory, never written to disk. For every
season and week 2-11, the chart of each skill player in the top POOL is compared (Spearman) with his real
points from that week to the end of the season, over the last starter at his position. The grid over
TAU x EXPERT_WEIGHT x FORM_WEIGHT is scored leave-season-out: each season is judged by the grid point that
did best on the others. Then sanity rows (2025) are printed.
"""
import argparse
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from itertools import product
from pathlib import Path
from statistics import mean

from sqlalchemy import create_engine, insert, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import ReplayExpertRank
from integrations.adp_history import SeasonMatcher
from services import replay_chart, replay_experts, replay_pool, replay_state, replay_values as rv
from services.replacement_eval import spearman
from services.replay_pool import SKILL, SeasonData
from services.replay_trade_value import STAR_PREMIUM

DEFAULT_DB = Path(__file__).resolve().parents[1] / "database" / "fantasy.db"
WEEKS = range(2, 12)
POOL = 200                                  # skill players judged per week: the top of the chart
TAUS = (32.0, 40.0, 48.0)
EXPERT_WEIGHTS = (0.5, 0.65, 0.8)
FORM_WEIGHTS = (0.35, 0.5, 0.65)
SANITY_SEASON = 2025


def read_only_copy(path: Path) -> Session:
    """A session on an in-memory copy of the database (the file is opened read-only)."""
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    memory = sqlite3.connect(":memory:", check_same_thread=False)
    source.backup(memory)
    source.close()
    engine = create_engine("sqlite://", creator=lambda: memory, poolclass=StaticPool)
    return Session(engine)


def add_expert_rows(session: Session, seasons: list[int]) -> None:
    """Fills the in-memory copy's expert table from the archive where it has none for a season."""
    if not inspect(session.get_bind()).has_table(ReplayExpertRank.__tablename__):
        ReplayExpertRank.__table__.create(session.get_bind())
    missing = [s for s in seasons if s >= replay_experts.FIRST_SEASON and not replay_experts.stored_rows(session, s)]
    if not missing:
        return
    raw, crosswalk = replay_experts.archive()
    for season in missing:
        dates = replay_experts._season_dates(session, season)
        rows = replay_experts.expert_rows(season, raw, SeasonMatcher(session, season), crosswalk, *dates) if dates else []
        if rows:                                 # expert_rows already keeps one line per player and snapshot
            session.execute(insert(ReplayExpertRank), rows)
        print(f"{season}: {len(rows)} expert rows in memory")


@contextmanager
def constants(tau: float, expert: float, form: float):
    old = (replay_chart.TAU, replay_chart.EXPERT_WEIGHT, replay_chart.FORM_WEIGHT)
    replay_chart.TAU, replay_chart.EXPERT_WEIGHT, replay_chart.FORM_WEIGHT = tau, expert, form
    try:
        yield
    finally:
        replay_chart.TAU, replay_chart.EXPERT_WEIGHT, replay_chart.FORM_WEIGHT = old


class Week:
    """One season-week's inputs to the chart, built once and re-charted per grid point."""

    def __init__(self, data: SeasonData, settings: replay_state.Settings, week: int):
        self.week, self.end = week, settings.end_week
        self.data = data.as_of(week)
        self.priors = rv._priors(self.data)
        know = rv.knowledge(data, rv.league_settings(settings), week, self.end)
        self.rows = [rv._row(self.data, data.players[v.player_id], self.priors[v.player_id], week, v.team,
                             v.games_left) for v in know.values]
        self.over = _over_baseline(data, settings, week)
        self.names = {v.player_id: v.name for v in know.values}

    def charts(self) -> dict[int, dict]:
        return replay_chart.charts(self.data, self.rows, self.priors, self.week, self.end, 0)[0]

    def spearman(self) -> float:
        charted = self.charts()
        skill = sorted((pid for pid in charted if self.over.get(pid) is not None),
                       key=lambda pid: -charted[pid]["chart"])[:POOL]
        return spearman([charted[pid]["chart"] for pid in skill], [self.over[pid] for pid in skill])


def _over_baseline(data: SeasonData, settings: replay_state.Settings, week: int) -> dict[int, float]:
    """{skill player: real points from `week` to the end, minus the last starter's at his position}."""
    totals: dict[int, float] = defaultdict(float)
    for (pid, w), points in data.actual.items():
        if week <= w <= settings.end_week:
            totals[pid] += points
    out: dict[int, float] = {}
    flex = settings.starters.get("FLEX", 0)
    for position in SKILL:
        ranked = sorted((totals.get(pid, 0.0) for pid, p in data.players.items() if p.position == position),
                        reverse=True)
        starters = settings.teams * (settings.starters.get(position, 0) + (flex / 3 if position != "QB" else 0))
        base = ranked[min(len(ranked) - 1, max(0, round(starters) - 1))]
        out.update({pid: totals.get(pid, 0.0) - base for pid, p in data.players.items() if p.position == position})
    return out


def grid_table(weeks: dict[int, list[Week]]) -> dict[tuple, dict[int, float]]:
    """{(tau, expert weight, form weight): {season: mean Spearman over its weeks}}."""
    table = {}
    for point in product(TAUS, EXPERT_WEIGHTS, FORM_WEIGHTS):
        with constants(*point):
            table[point] = {season: mean(w.spearman() for w in found) for season, found in weeks.items()}
    return table


def leave_season_out(table: dict[tuple, dict[int, float]]) -> dict[int, tuple[tuple, float]]:
    seasons = next(iter(table.values())).keys()
    out = {}
    for held in seasons:
        best = max(table, key=lambda p: mean(v for s, v in table[p].items() if s != held))
        out[held] = (best, table[best][held])
    return out


def _find(week: Week, name: str) -> int | None:
    return next((pid for pid, n in week.names.items() if n == name), None)


def sanity(by_week: dict[int, Week]) -> None:
    charts = {w: week.charts() for w, week in by_week.items()}

    def show(w: int, name: str) -> float:
        pid = _find(by_week[w], name)
        return charts[w][pid]["chart"] if pid is not None else float("nan")

    if 6 in by_week:
        print(f"JSN week 6: {show(6, 'Jaxon Smith-Njigba'):.0f} (target >= 650)")
    print("Chase Brown by week: " + ", ".join(f"{w}:{show(w, 'Chase Brown'):.0f}" for w in sorted(by_week)))
    if 1 in by_week:
        star, pair = show(1, "Amon-Ra St. Brown"), [show(1, "Tetairoa McMillan"), show(1, "Jordan Mason")]
        premium = STAR_PREMIUM * star * (1 - max(pair) / star)
        print(f"Week 1: St. Brown {star:.0f} (+{premium:.0f} star premium) vs McMillan {pair[0]:.0f} + Mason "
              f"{pair[1]:.0f} = {sum(pair):.0f}")
    for w in (4, 8):
        hurt = sorted((p for p, c in charts.get(w, {}).items() if c["news"]), key=lambda p: -charts[w][p]["healthy_chart"])
        print(f"wk{w} injured: " + "; ".join(f"{by_week[w].names[p]} {charts[w][p]['healthy_chart']:.0f}->"
                                             f"{charts[w][p]['chart']:.0f} ({charts[w][p]['news'].text})" for p in hurt[:6]))
    for w in sorted(by_week):
        top = sorted(charts[w], key=lambda pid: -charts[w][pid]["chart"])[:12]
        print(f"wk{w:>2} top 12: " + "; ".join(f"{by_week[w].names[p]} {charts[w][p]['chart']:.0f}" for p in top))


def parse_seasons(text: str) -> list[int]:
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seasons", default="2020-2025")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--scoring", default="half")
    args = parser.parse_args()
    seasons = parse_seasons(args.seasons)
    session = read_only_copy(args.db)
    add_expert_rows(session, seasons)
    weeks: dict[int, list[Week]] = {}
    for season in seasons:
        settings = replay_state.Settings(season=season, scoring=args.scoring)
        data = replay_pool.load_season_data(session, season, args.scoring)
        weeks[season] = [Week(data, settings, w) for w in WEEKS]
        print(f"{season}: {len(data.expert_ranks)} expert ranks, {len(data.returns)} hard-tag streaks")
    table = grid_table(weeks)
    print("\nTAU  expert form | " + " ".join(str(s) for s in seasons) + " | mean")
    for point, by_season in sorted(table.items(), key=lambda kv: -mean(kv[1].values())):
        print(f"{point[0]:>4.0f} {point[1]:>5.2f} {point[2]:>5.2f} | "
              + " ".join(f"{by_season[s]:.3f}" for s in seasons) + f" | {mean(by_season.values()):.4f}")
    held = leave_season_out(table) if len(seasons) > 1 else {}
    if held:
        print("\nleave-season-out: " + ", ".join(f"{s}: {p} -> {v:.3f}" for s, (p, v) in held.items())
              + f"; mean {mean(v for _, v in held.values()):.4f}")
    current = (replay_chart.TAU, replay_chart.EXPERT_WEIGHT, replay_chart.FORM_WEIGHT)
    print(f"current constants {current}: mean {mean(table[current].values()):.4f}")
    if SANITY_SEASON in seasons:
        settings = replay_state.Settings(season=SANITY_SEASON, scoring=args.scoring)
        data = replay_pool.load_season_data(session, SANITY_SEASON, args.scoring)
        print(f"\nsanity {SANITY_SEASON} ({args.scoring}):")
        sanity({w: Week(data, settings, w) for w in range(1, 13)})


if __name__ == "__main__":
    main()
