"""Simulated seasons: split backfields that one back wins during the season (a season simulated from week 1).

Measured the same way on a real and a simulated season (`committees`, from the box lines): a team's two
busiest RBs over weeks 1..EARLY_WEEKS (carries + targets) are a COMMITTEE when the second had COMMITTEE_RATIO
or more of the first's work and both played MIN_EARLY_GAMES of those weeks. It is RESOLVED at the first
week W after them where, over WINDOW games both played from W, one had WIN_RATIO x the other's work, and he
kept WIN_RATIO_REST x over all their shared games from W on. Real seasons 2018-2025 (`python -m
services.sim_depth --fit --seasons 2018-2025` -> sim_depth.json): about a quarter of teams open with a
committee, about half of those are won during the season, the early leader wins about three times in four,
mostly in the first weeks after the opening month.

The engine (`resolutions`): a team whose belief going into the season has its RB2 at COMMITTEE_RATIO of its
RB1's volume (sim_next_year.volume, rookies included) starts split; with `resolve_rate` (tuned so the
simulated seasons resolve about as many measured committees as the real ones; sim_eval --mode full reports
both) one back wins it at a week drawn from the fitted `weeks` (seed-dependent): the truth's busier of the two
with the fitted `leader` share, else the other. From that week the pair's work is split `post_ratio`
(loser / winner, fitted) with its total unchanged, as RoleChanges ("breakout" for the winner, "bust" for the
loser, so sim_market.buzz may hint it). The belief learns the winner only from played games, like any role.

`fill_ins`: the public news line on a backup QB in a week the belief's starter is hard-tagged ("Expected to
start while Justin Herbert is out (IR, Knee)."), so a league shows why the backup plays (sim_season).

Every team always has a QB to start. A backup who never played is in no table the pool reads, so a team could
reach a simulated season with one QB (2027: Cincinnati, Dallas, San Francisco), or lose both, and its games then
had receivers catching passes nobody threw. `with_real_qbs` first adds each team's real quarterbacks of today
(sim_setup.qb_rooms: the depth chart, else Sleeper's depth order; the offseason file's moves and retirements on
top) to a live or next season's pool; then `with_backup_qbs` gives a team still short of two a pooled QB ("DAL
backup QB"). Either kind gets a token projection so the public sees him and a manager can add him; the belief
makes him a typical backup (no prior: the depth role, the backup's passing quality). `emergency` puts a QB under
centre in a week all the team's QBs sit (sim_season): the pooled one, else the one due back soonest.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from collections.abc import Mapping, Sequence  # noqa: E402
from dataclasses import replace  # noqa: E402
from types import MappingProxyType  # noqa: E402
from functools import lru_cache  # noqa: E402

from services import replay_state, sim_market  # noqa: E402
from services.replay_pool import Injury, PoolPlayer, SeasonData  # noqa: E402
from services.sim_constants import fitted_json  # noqa: E402
from services.sim_world import STARTER_ROLE, RoleChange, World  # noqa: E402

PATH = Path(__file__).with_name("sim_depth.json")
FIT = "python -m services.sim_depth --fit --seasons 2018-2025"
EARLY_WEEKS = 4
COMMITTEE_RATIO = 0.6
MIN_EARLY_GAMES = 3
WINDOW = 3
WIN_RATIO = 2.0
WIN_RATIO_REST = 1.8


@lru_cache(maxsize=1)
def stored() -> dict:
    """The fitted committee model (sim_depth.json)."""
    return fitted_json(PATH, FIT, ("committee_rate", "resolved", "leader", "weeks", "post_ratio", "resolve_rate"))


def _rb_work(data: SeasonData) -> dict[str, dict[int, dict[int, float]]]:
    """{team: {week: {RB: carries + targets}}} from the box lines."""
    out: dict[str, dict[int, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for pid, games in data.team_by_week.items():
        player = data.players.get(pid)
        if player is None or player.position != "RB":
            continue
        for week, team in games:
            line = data.box.get((pid, week))
            if line is not None:
                out[team][week][pid] = float(line.carries + line.targets)
    return out


def _resolved(weeks: dict[int, dict[int, float]], a: int, b: int) -> tuple[int, int, float] | None:
    """(week, winner, loser's work / winner's after it) when the committee a-b is won, else None."""
    both = [w for w in sorted(weeks) if w > EARLY_WEEKS and a in weeks[w] and b in weeks[w]]
    for i in range(len(both) - WINDOW + 1):
        wa, wb = (sum(weeks[w][p] for w in both[i:i + WINDOW]) for p in (a, b))
        lead, lag = (a, b) if wa >= wb else (b, a)
        if max(wa, wb) < WIN_RATIO * min(wa, wb):
            continue
        ra, rb = (sum(weeks[w][p] for w in both[i:]) for p in (lead, lag))
        if ra >= WIN_RATIO_REST * rb:
            return both[i], lead, rb / max(ra, 1.0)
    return None


def committees(data: SeasonData) -> list[dict]:
    """Every committee of `data` (see the module docstring): {team, lead, partner, week, leader_won, ratio};
    week None = never won."""
    found = []
    for team, weeks in sorted(_rb_work(data).items()):
        early, played = defaultdict(float), defaultdict(int)
        for week in range(1, EARLY_WEEKS + 1):
            for pid, work in weeks.get(week, {}).items():
                early[pid] += work
                played[pid] += 1
        top = sorted(early, key=lambda p: (-early[p], p))[:2]
        if len(top) < 2 or early[top[0]] <= 0:
            continue
        a, b = top
        if early[b] < COMMITTEE_RATIO * early[a] or min(played[a], played[b]) < MIN_EARLY_GAMES:
            continue
        result = _resolved(weeks, a, b)
        found.append({"team": team, "lead": a, "partner": b, "week": result and result[0],
                      "leader_won": bool(result and result[1] == a), "ratio": result and result[2]})
    return found


def metrics(data: SeasonData) -> dict[str, float]:
    """sim_eval's committee rows: committees per team, the share won during the season."""
    found, teams = committees(data), len({t for games in data.team_by_week.values() for _, t in games}) or 1
    won = [c for c in found if c["week"] is not None]
    return {"RB committees per team": len(found) / teams,
            "RB committees won %": 100.0 * len(won) / len(found) if found else float("nan")}


def resolutions(belief: World, truth: World, seed: int, weeks: tuple[int, int]) -> list[RoleChange]:
    """The truth's dated committee winners (see the module docstring). `weeks`: (first, last) week a change
    may start in (sim_talent.change_weeks)."""
    from services.sim_next_year import volume

    model = stored()
    pmf = {int(w): p for w, p in model["weeks"].items() if weeks[0] <= int(w) <= weeks[1]}
    changes = []
    for team in sorted(belief.teams):
        rbs = sorted((p for p in belief.roster(team) if p.position == "RB"), key=lambda p: (-volume(p), p.player_id))
        dice = replay_state.rng(seed, "sim", "committee", team)
        roll, pick, lead_roll = dice.random(3)
        if len(rbs) < 2 or volume(rbs[1]) < COMMITTEE_RATIO * volume(rbs[0]) or roll >= model["resolve_rate"] or not pmf:
            continue
        pair = [truth.players[p.player_id] for p in rbs[:2] if p.player_id in truth.players]
        if len(pair) < 2:
            continue
        pair.sort(key=lambda p: (-volume(p), p.player_id))
        winner, loser = pair if lead_roll < model["leader"] else pair[::-1]
        total, ratio = volume(winner) + volume(loser), model["post_ratio"]
        if volume(winner) <= 0 or volume(loser) <= 0:
            continue
        week = _week(pmf, pick)
        gain, cut = total / (1 + ratio) / volume(winner), total * ratio / (1 + ratio) / volume(loser)
        changes += [RoleChange(week, winner.player_id, gain, gain, 1.0, "breakout"),
                    RoleChange(week, loser.player_id, cut, cut, 1.0, "bust")]
    return changes


def fill_ins(belief: World, out: set[int], tags: Mapping[int, Injury], players: Mapping[int, PoolPlayer],
             week: int) -> dict[tuple[int, int], str]:
    """{(QB, week): why he starts}: the public news when a team's starting QB (by the belief) is out that week
    (`out`: its hard tags) and the next one in line steps in."""
    notes = {}
    for team in sorted(belief.teams):
        qbs = sorted((p for p in belief.roster(team) if p.position == "QB"), key=lambda p: (-p.starts, p.player_id))
        if len(qbs) < 2 or qbs[0].player_id not in out or qbs[0].starts < STARTER_ROLE:
            continue
        backup = next((p for p in qbs[1:] if p.player_id not in out), None)
        starter = players.get(qbs[0].player_id)
        if backup is None or starter is None:
            continue
        tag = tags.get(qbs[0].player_id)
        why = f"{tag.tag}, {tag.injury}" if tag is not None and tag.injury else (tag.tag if tag else "out")
        notes[(backup.player_id, week)] = f"Expected to start while {starter.name} is out ({why})."
    return notes


def _week(pmf: dict[int, float], u: float) -> int:
    total, run = sum(pmf.values()), 0.0
    for week in sorted(pmf):
        run += pmf[week] / total
        if u < run:
            return week
    return max(pmf)


def stored_resolve_rate() -> float:
    """The tuned resolve_rate a refit keeps: 0.5 before the file exists; a file that can't be read raises (as
    sim_fit.write), so a refit never silently resets the tuned value."""
    return json.loads(PATH.read_text(encoding="utf-8")).get("resolve_rate", 0.5) if PATH.exists() else 0.5


def fit(seasons: list[int]) -> dict:
    """The real committees of `seasons` (the shape of sim_depth.json, resolve_rate kept from the stored file)."""
    from database.db import SessionLocal
    from services import replay_pool

    rows, teams = [], 0
    with SessionLocal() as session:
        for season in seasons:
            data = replay_pool.load_season_data(session, season, "ppr")
            rows += committees(data)
            teams += len({t for games in data.team_by_week.values() for _, t in games})
    won = [c for c in rows if c["week"] is not None]
    weeks: dict[int, int] = defaultdict(int)
    for c in won:
        weeks[c["week"]] += 1
    return {"seasons": seasons, "committee_rate": round(len(rows) / teams, 4),
            "resolved": round(len(won) / max(1, len(rows)), 4),
            "leader": round(sum(c["leader_won"] for c in won) / max(1, len(won)), 4),
            "weeks": {str(w): round(n / len(won), 4) for w, n in sorted(weeks.items())},
            "post_ratio": round(sum(c["ratio"] for c in won) / max(1, len(won)), 4),
            "resolve_rate": stored_resolve_rate(), "counts": {"committees": len(rows), "won": len(won), "team_seasons": teams}}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--seasons", default="2018-2025")
    args = parser.parse_args()
    first, _, last = args.seasons.partition("-")
    result = fit(list(range(int(first), int(last or first) + 1)))
    print(json.dumps(result, indent=1))
    if args.fit:
        PATH.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        stored.cache_clear()


if __name__ == "__main__":
    main()


BACKUP_QB_ID_BASE = 2_000_000_000       # pooled QBs: above database rows and rookie ids, below 2^31


def _qb_counts(base: SeasonData, week: int) -> Counter:
    return Counter(base.team_at(pid, week) for pid, p in base.players.items() if p.position == "QB")


def _with_added(base: SeasonData, added: Mapping[int, PoolPlayer], from_week: int) -> SeasonData:
    if not added:
        return base
    return replace(base, players=MappingProxyType({**base.players, **added}),
                   projected=MappingProxyType({**base.projected,
                                               **{(pid, from_week): sim_market.MIN_PROJECTION for pid in added}}))


def with_real_qbs(base: SeasonData, rooms: Mapping[str, Sequence[tuple[int, str]]], from_week: int,
                  moved: Mapping[int, str | None] | None = None) -> SeasonData:
    """`base` with each team's real quarterbacks of today who are not in its pool yet (`rooms`: {team: [(id,
    name)]} by depth, sim_setup.qb_rooms), on `moved`'s team when the offseason file moves him (None: retired,
    left out). Like the pooled QB he has no games of evidence: a backup's level and starts weight. Only for a
    live or the next season (today's rooms)."""
    teams = {team for team, _ in base.schedule.opponents}
    moved = moved or {}
    added = {pid: PoolPlayer(pid, name, "QB", adp_team=moved.get(pid, team))
             for team, qbs in rooms.items() for pid, name in qbs
             if pid not in base.players and moved.get(pid, team) in teams}
    return _with_added(base, added, from_week)


def with_backup_qbs(base: SeasonData, from_week: int) -> SeasonData:
    """`base` with a pooled QB for each schedule team with fewer than two QBs (module docstring): id
    BACKUP_QB_ID_BASE + season x 100 + the team's index, the same in every league of a season."""
    teams = sorted({team for team, _ in base.schedule.opponents})
    counts = _qb_counts(base, from_week)
    added = {BACKUP_QB_ID_BASE + base.season * 100 + i: team for i, team in enumerate(teams) if counts[team] < 2}
    return _with_added(base, {pid: PoolPlayer(pid, f"{team} backup QB", "QB", adp_team=team)
                              for pid, team in added.items() if pid not in base.players}, from_week)


def emergency(world: World, sitting: set[int], left: Mapping[int, int] | None = None) -> set[int]:
    """`sitting` without one QB of any team whose QBs all sit (no game without a QB): its pooled QB, else the
    one due back soonest (`left`: weeks of absence still to run; a QB with none, say a public tag, first)."""
    out, left = set(sitting), left or {}
    for team in world.teams:
        qbs = [p.player_id for p in world.roster(team) if p.position == "QB"]
        real = [pid for pid in qbs if pid < BACKUP_QB_ID_BASE]
        if not real or not all(pid in out for pid in real):
            continue
        pooled = [pid for pid in qbs if pid >= BACKUP_QB_ID_BASE]
        out.difference_update(pooled or [min(real, key=lambda pid: (left.get(pid, 0), pid))])
    return out
