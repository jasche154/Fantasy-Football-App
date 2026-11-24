"""Replay League trade check (read-only): does a CPU say yes and no to the right trades?

    python -m services.replay_trade_eval [--db path] [--seeds 0,1,2] [--slices-only]
    python -m services.replay_trade_eval --capture [--seasons 2020-2025]

The labeled cases live in tests/data/replay_trade_cases.json: {id, season, week, scoring, cpu_gives, cpu_gets,
label (must_accept | must_decline | even), note}. For each case and seed a league is drafted (`league`: a
noisy-ADP snake), the named players are swapped onto the user's team and one CPU's (`place`), and the user
proposes giving `cpu_gets` for `cpu_gives` to that CPU under every persona (`judge`). The default mode does it
on the real database (opened read-only, expert ranks pulled in memory as replay_value_eval does) and on the
committed slices, and prints accuracy per label and persona, every disagreement with its tally, and the chart
values involved.

`--capture` writes tests/data/replay_slices/{season}.json: every ADP player plus the best few undrafted by
peak projection, weeks 1-12 only (projections and points in tenths, tags, expert ranks, hard-tag returns), the whole
schedule; `load_slice` rebuilds a SeasonData from one. Usage and injury text are left out (trades don't read
them). Values are rounded to 0.1, so slice charts can differ from the database's by a point or two.
"""
import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

import numpy as np

from services import replay_state as rs, replay_trade_value as tv, replay_trades as rt, replay_values as rv
from services.replay_personas import PERSONAS, hype_for
from services.replay_pool import ALL_POSITIONS, SKILL, Injury, PoolPlayer, SeasonData
from services.replay_state import Pick, Settings, State, Team
from services.schedule_strength import Game, Schedule

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "tests" / "data" / "replay_trade_cases.json"
SLICES_DIR = ROOT / "tests" / "data" / "replay_slices"
LAST_WEEK = 12                  # trades close after week 11 (week 12 in its waiver phase)
EXTRA = {"QB": 4, "RB": 10, "WR": 12, "TE": 5, "K": 6, "DST": 8}   # undrafted players kept, by peak projection
DRAFT_NOISE = 0.15              # lognormal sigma on ADP for the eval draft
SEEDS = (0, 1, 2)
USER = 1
LABELS = ("must_accept", "must_decline", "even")


# ---------------- slices ----------------

def _keep(data: SeasonData) -> list[int]:
    peak: dict[int, float] = defaultdict(float)
    for (pid, week), points in data.projected.items():
        if week <= LAST_WEEK:
            peak[pid] = max(peak[pid], points)
    keep = [pid for pid, p in data.players.items() if p.adp is not None]
    for position, extra in EXTRA.items():
        undrafted = [pid for pid, p in data.players.items() if p.position == position and p.adp is None]
        keep += sorted(undrafted, key=lambda pid: (-peak[pid], pid))[:extra]
    return sorted(keep)


def _num(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _tenths(value: float | None) -> int | None:
    return None if value is None else round(value * 10)


def slice_json(data: SeasonData) -> dict:
    """The pruned season as plain JSON (see the module docstring)."""
    keep = _keep(data)
    weeks = range(1, LAST_WEEK + 1)
    players = []
    for pid in keep:
        p = data.players[pid]
        games: dict[str, list[int]] = defaultdict(list)
        played = data.team_by_week.get(pid, ())
        for week, team in [g for g in played if g[0] < LAST_WEEK] or list(played[:1]):
            games[team].append(week)
        players.append([
            pid, p.name, p.position, _num(p.adp), _num(p.adp_stdev), p.adp_team,
            [_tenths(data.projected.get((pid, w))) or 0 for w in weeks],             # tenths of a point
            [_tenths(data.actual.get((pid, w))) for w in range(1, LAST_WEEK)],
            [data.expert_ranks.get((pid, w)) for w in range(0, LAST_WEEK + 1)],
            [[team, found] for team, found in games.items()],
        ])
    kept = set(keep)
    return {
        "season": data.season, "scoring": data.scoring, "last_week": data.last_week,
        "games": [[g.week, g.home_team, g.away_team] for g in data.schedule.games],
        "players": players,
        "injuries": sorted([pid, w, i.tag] for (pid, w), i in data.injuries.items() if pid in kept and w <= LAST_WEEK),
        "returns": sorted([pid, w, back] for (pid, w), back in data.returns.items() if pid in kept and w <= LAST_WEEK),
        "expert_age": {str(w): age for w, age in data.expert_age.items() if w <= LAST_WEEK},
    }


def from_json(raw: dict) -> SeasonData:
    """A SeasonData rebuilt from `slice_json` output."""
    games = tuple(Game(week, home, away, None, None, None, None) for week, home, away in raw["games"])
    opponents = {}
    for g in games:
        opponents[(g.home_team, g.week)], opponents[(g.away_team, g.week)] = g.away_team, g.home_team
    players, projected, actual, ranks, teams = {}, {}, {}, {}, {}
    for pid, name, position, adp, stdev, adp_team, proj, points, expert, games_by_team in raw["players"]:
        players[pid] = PoolPlayer(pid, name, position, adp, stdev, adp_team)
        projected.update({(pid, w): v / 10 for w, v in enumerate(proj, start=1) if v})
        actual.update({(pid, w): v / 10 for w, v in enumerate(points, start=1) if v is not None})
        ranks.update({(pid, w): r for w, r in enumerate(expert) if r is not None})
        teams[pid] = tuple(sorted((w, team) for team, found in games_by_team for w in found))
    return SeasonData(
        season=raw["season"], scoring=raw["scoring"], players=MappingProxyType(players),
        schedule=Schedule(games=games, opponents=MappingProxyType(opponents)),
        actual=MappingProxyType(actual), projected=MappingProxyType(projected),
        team_by_week=MappingProxyType(teams), last_week=raw["last_week"],
        injuries=MappingProxyType({(pid, w): Injury(tag) for pid, w, tag in raw["injuries"]}),
        expert_ranks=MappingProxyType(ranks),
        expert_age=MappingProxyType({int(w): age for w, age in raw["expert_age"].items()}),
        returns=MappingProxyType({(pid, w): back for pid, w, back in raw["returns"]}))


_slices: dict[int, SeasonData] = {}


def load_slice(season: int) -> SeasonData:
    """The committed slice for a season (one object per season, so value caches hit)."""
    if season not in _slices:
        _slices[season] = from_json(json.loads((SLICES_DIR / f"{season}.json").read_text(encoding="utf-8")))
    return _slices[season]


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------- one case ----------------

def league(data: SeasonData, settings: Settings, seed: int) -> State:
    """A drafted league: a snake on noisy ADP, each team taking the best left that still lets it fill every
    starting slot. The user is team 1; the schedule is a round robin. Phase "open", week 1."""
    noise = rs.rng(seed, "trade-eval-draft")
    drafted = sorted((p for p in data.players.values() if p.adp is not None), key=lambda p: (p.adp, p.player_id))
    board = sorted(drafted, key=lambda p: p.adp * float(np.exp(noise.normal(0, DRAFT_NOISE))))
    ids = tuple(range(1, settings.teams + 1))
    rosters: dict[int, list[PoolPlayer]] = {team_id: [] for team_id in ids}
    picks = []
    for overall in range(1, settings.teams * settings.rounds + 1):
        round_, spot = divmod(overall - 1, settings.teams)
        team_id = settings.teams - spot if round_ % 2 else spot + 1
        mine = [p.position for p in rosters[team_id]]
        left = settings.rounds - len(mine) - 1
        choice = next(p for p in board if rv.lineup_holes([*mine, p.position], settings) <= left)
        board.remove(choice)
        rosters[team_id].append(choice)
        picks.append(Pick(overall, round_ + 1, team_id, choice.player_id))
    teams = tuple(Team(i, "You" if i == USER else f"CPU {i}", i == USER, tuple(p.player_id for p in rosters[i]),
                       1.0, dict.fromkeys(ALL_POSITIONS, 1.0)) for i in ids)
    return State(phase="open", week=1, teams=teams, draft_order=ids, picks=tuple(picks),
                 schedule=rs.round_robin(ids, settings.regular_season_weeks, seed), results=(),
                 waiver_order=ids[::-1], claims=(), locked=(), lineup=None, offers=(), events=(), traded={})


def find(data: SeasonData, name: str) -> int:
    """The player id for an exact name (an ADP player first). KeyError when nobody has it."""
    found = sorted((p for p in data.players.values() if p.name == name),
                   key=lambda p: (p.adp is None, p.adp or 0.0, -data.projection(p.player_id, 1)))
    if not found:
        raise KeyError(f"{data.season}: no player named {name!r}")
    return found[0].player_id


def place(state: State, know: rv.Knowledge, moves: dict[int, int]) -> State:
    """Each player in `moves` ({player: team id}) put on that team by a swap: the team's lowest-chart player at
    his position not in `moves` goes where he was (his old team, or free agency), so every roster keeps its
    size and positions."""
    for pid, team_id in moves.items():
        target = rs.team(state, team_id)
        if pid in target.roster:
            continue
        position = know.by_id[pid].position
        spare = [v for v in target.roster if v not in moves and know.by_id[v].position == position]
        victim = min(spare, key=lambda v: (know.by_id[v].chart, v))
        owner = next((t for t in state.teams if pid in t.roster), None)
        if owner is not None:
            state = rs.with_team(state, replace(owner, roster=tuple(victim if p == pid else p for p in owner.roster)))
        target = rs.team(state, team_id)
        state = rs.with_team(state, replace(target, roster=tuple(pid if p == victim else p for p in target.roster)))
    return state


def _user_drops(state: State, settings: Settings, know: rv.Knowledge, give: tuple, get: tuple) -> tuple[int, ...]:
    """The user's lowest-chart skill players outside the deal, as many as the deal puts him over the limit."""
    roster = [pid for pid in rs.user_team(state).roster if pid not in give] + list(get)
    drops = []
    for v in sorted((know.by_id[pid] for pid in rs.user_team(state).roster if pid not in give),
                    key=lambda v: (v.chart, v.player_id)):
        if len(roster) <= settings.roster_size:
            break
        rest = [pid for pid in roster if pid != v.player_id]
        if v.position in SKILL and rv.can_field_lineup([know.by_id[p].position for p in rest], settings):
            roster, drops = rest, [*drops, v.player_id]
    return tuple(drops)


@dataclass(frozen=True)
class Result:
    case: str
    label: str
    seed: int
    persona: str
    accepted: bool
    score: float
    tally: tv.Tally | None
    reasons: tuple[str, ...]
    charts: tuple[tuple[str, float, float], ...]   # (name, chart, healthy_chart) for cpu_gives then cpu_gets
    gives: int = 1                                 # how many of `charts` the CPU gives


def _tally(state: State, settings: Settings, know: rv.Knowledge, cpu: Team, gives: tuple, gets: tuple,
           verdict: rt.Verdict) -> tv.Tally:
    """The CPU's tally, rebuilt from the verdict exactly as replay_trades scores it (a shop discount included)."""
    kept = tuple(pid for pid in gets if pid not in verdict.their_drops)
    own_cuts = tuple(pid for pid in verdict.their_drops if pid not in gets)
    per_week = verdict.their_gain / max(1e-9, sum(rv.horizon(know, settings).weights.values()))
    return tv.tally(know, cpu.persona, gives, kept, own_cuts, per_week,
                    rt.needed_multiple(settings, rs.user_team(state)), True, cpu.hype, rs.markdowns(state, cpu.id))


def judge(data: SeasonData, case: dict, seed: int, personas=PERSONAS) -> list[Result]:
    """The case on one seed's league, once per persona of the CPU it is offered to."""
    settings = Settings(season=case["season"], scoring=case["scoring"])
    know = rv.knowledge(data, rv.league_settings(settings), case["week"], settings.end_week, seed)
    gives = tuple(find(data, n) for n in case["cpu_gives"])
    gets = tuple(find(data, n) for n in case["cpu_gets"])
    cpu_id = 2 + seed % (settings.teams - 1)
    state = replace(league(data.as_of(0), settings, seed), week=case["week"])
    state = place(state, know, {**dict.fromkeys(gives, cpu_id), **dict.fromkeys(gets, USER)})
    drops = _user_drops(state, settings, know, gets, gives)
    charts = tuple((know.by_id[pid].name, know.by_id[pid].chart, know.by_id[pid].healthy_chart)
                   for pid in (*gives, *gets))
    out = []
    for persona in personas:
        cpu = replace(rs.team(state, cpu_id), persona=persona, hype=hype_for(persona))
        trial = rs.with_team(state, cpu)
        verdict = rt.evaluate(trial, settings, know, USER, cpu_id, gets, gives, drops)
        out.append(Result(case["id"], case["label"], seed, persona, verdict.accepted, verdict.score,
                          _tally(trial, settings, know, cpu, gives, gets, verdict), verdict.reasons, charts, len(gives)))
    return out


def right(result: Result) -> bool | None:
    """Did the CPU answer as labeled? None for an even case."""
    return {"must_accept": result.accepted, "must_decline": not result.accepted}.get(result.label)


# ---------------- report ----------------

def report(title: str, results: list[Result]) -> None:
    print(f"\n=== {title} ===")
    table: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for r in results:
        table[(r.label, r.persona)].append(r.accepted if r.label == "even" else bool(right(r)))
    print("label         " + " ".join(f"{p:>9}" for p in PERSONAS) + "   (accuracy; even = accept rate)")
    for label in LABELS:
        cells = [table.get((label, p), []) for p in PERSONAS]
        print(f"{label:<13} " + " ".join(f"{sum(c) / len(c):>9.0%}" if c else f"{'-':>9}" for c in cells))
    by_case: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_case[r.case].append(r)
    print("\ncase                          label         accepts  score(min/max)   charts (cpu gives | cpu gets)")
    for case, found in by_case.items():
        first, n = found[0], len(found)
        gives = first.gives
        names = [f"{name} {chart:.0f}" + (f"({healthy:.0f})" if abs(chart - healthy) > 1 else "")
                 for name, chart, healthy in first.charts]
        flag = "" if all(right(r) is not False for r in found) else "  <-- WRONG"
        print(f"{case:<29} {first.label:<13} {sum(r.accepted for r in found):>2}/{n:<4} "
              f"{min(r.score for r in found):>7.0f}/{max(r.score for r in found):<7.0f} "
              f"{', '.join(names[:gives])} | {', '.join(names[gives:])}{flag}")
    wrong = [r for r in results if right(r) is False]
    print(f"\ndisagreements: {len(wrong)} of {sum(right(r) is not None for r in results)} labeled runs")
    for r in wrong:
        t = r.tally
        print(f"  {r.case} seed {r.seed} {r.persona}: {'accepted' if r.accepted else 'declined'} score {r.score:.0f}"
              f" | sent {t.sent:.0f} received {t.received:.0f} premium {t.premium:.0f} spot {t.spot:.0f}"
              f" lineup {t.lineup:.0f}" + (f" | {' '.join(r.reasons)}" if r.reasons else ""))


def run(cases: list[dict], data_for, seeds=SEEDS) -> list[Result]:
    return [r for case in cases for seed in seeds for r in judge(data_for(case["season"], case["scoring"]), case, seed)]


def main() -> None:
    from services import replay_pool, replay_value_eval    # database only here
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=replay_value_eval.DEFAULT_DB)
    parser.add_argument("--capture", action="store_true", help="write tests/data/replay_slices/{season}.json")
    parser.add_argument("--seasons", default="2020-2025")
    parser.add_argument("--scoring", default="half")
    parser.add_argument("--seeds", default="0,1,2")
    parser.add_argument("--slices-only", action="store_true")
    args = parser.parse_args()
    cases, seeds = load_cases(), tuple(int(s) for s in args.seeds.split(","))
    seasons = replay_value_eval.parse_seasons(args.seasons) if args.capture else sorted({c["season"] for c in cases})
    if args.capture or not args.slices_only:
        session = replay_value_eval.read_only_copy(args.db)
        replay_value_eval.add_expert_rows(session, seasons)
    if args.capture:
        SLICES_DIR.mkdir(parents=True, exist_ok=True)
        for season in seasons:
            raw = slice_json(replay_pool.load_season_data(session, season, args.scoring))
            path = SLICES_DIR / f"{season}.json"
            path.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
            print(f"{path.name}: {len(raw['players'])} players, {path.stat().st_size / 1024:.0f} KB")
        return
    if not args.slices_only:
        report("real database", run(cases, lambda s, sc: replay_pool.load_season_data(session, s, sc), seeds))
    report("slices", run(cases, lambda s, sc: load_slice(s), seeds))


if __name__ == "__main__":
    main()
