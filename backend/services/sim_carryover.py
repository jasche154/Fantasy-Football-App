"""Simulated seasons: long injuries carried from one season into the next (a future season simulated whole).

`carried(data, next_week_one, detail)` finds every player whose hard tag (O, IR, PUP) is still running at the
newest injury report of `data` (the season so far) and estimates when he plays again:

- hurt: the date of his team's last game before the streak's first tagged week (else that week's date - 7 days);
- recovery: RECOVERY_WEEKS, the typical return-to-play time for the injury, matched in order on the body text
  (the current status's detail, e.g. "Knee - ACL", when `detail` has it, else the report's "Knee"); a generic
  entry (KNEE_AND_CO) with an operation in the status notes ("Surgery") takes SURGERY x as long, and an
  operated Achilles is a repaired tear (OPERATED; a bare "Achilles" is mostly a strain: the real players
  still tagged with one at a season's end were back by week 3);
- the gap: weeks from that date to next season's week 1 (`next_week_one`);
- games out next season: the recovery left after the gap, in whole weeks (a week a game). The truth draws
  the recovery x lognormal(RECOVERY_SD) (`draw`, seeded per player), so a player expected back for week 1 may
  still start on the injury list and one expected out may be early. The public belief prices the expected
  games out (`expected`, the mean of the same distribution, on a fixed normal quantile grid), and a preseason
  note says when he is expected back (`note`). The truth's games out become an absence that starts the
  season (sim_season.simulate's `carried`), tagged and reported like any other (replay_news reads its real
  return through the usual noise).

The table is the usual published return-to-play times (e.g. ACL and Achilles about 11 months, a Lisfranc
or a pectoral tear about 5); RECOVERY_SD (a quarter of the time) is the spread those studies report for
ACL returns. Checked against real seasons with `python -m services.sim_carryover --check`: players still
tagged at the end of a 2018-2024 season, how many missed next season's week 1 against the share of healthy
players who did (the box scores also miss depth players, so only the difference means anything), next to
what this model predicts for the same players from their report's body text.
"""
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from services import replay_state
from services.replay_pool import IR_TAGS, SeasonData
from services.sim_injuries import Spell

RECOVERY_WEEKS = (               # (text in the body, weeks to return): the first match wins
    ("acl", 48.0), ("achilles", 8.0), ("patell", 40.0), ("lisfranc", 26.0), ("tibia", 16.0), ("pcl", 20.0),
    ("pectoral", 20.0), ("triceps", 20.0), ("biceps", 16.0), ("fibula", 12.0), ("fracture", 10.0),
    ("meniscus", 8.0), ("mcl", 6.0), ("concussion", 3.0), ("hamstring", 4.0), ("knee", 6.0), ("foot", 6.0),
    ("ankle", 5.0), ("shoulder", 6.0), ("neck", 6.0), ("back", 5.0), ("hip", 5.0))
DEFAULT_WEEKS = 5.0
KNEE_AND_CO = ("knee", "foot", "ankle", "shoulder", "neck", "back", "hip")
SURGERY = 2.0
OPERATED = {"achilles": 48.0}    # with an operation in the notes: a repaired tear, not a strain
RECOVERY_SD = 0.25
GRID = 41                        # normal quantiles the expected games out are averaged over
SEASON_GAMES = 17
POSITIONS = ("QB", "RB", "WR", "TE", "K")


@dataclass(frozen=True)
class Carry:
    body: str                    # shown to the user
    hurt_week: int               # the week of last season he was hurt in (the streak's first tagged week - 1)
    recovery: float              # typical weeks to return
    gap: float                   # weeks from the injury to next season's week 1
    expected: float              # mean games out next season (the public's estimate)


def recovery_weeks(body: str, notes: str | None = None) -> float:
    text = body.lower()
    for key, weeks in RECOVERY_WEEKS:
        if key in text:
            operated = bool(notes and "surgery" in notes.lower())
            if operated and key in OPERATED:
                return OPERATED[key]
            return weeks * (SURGERY if operated and key in KNEE_AND_CO else 1.0)
    return DEFAULT_WEEKS


def games_out(recovery: float, gap: float, z: float) -> int:
    """Games missed next season if the recovery takes recovery x lognormal(RECOVERY_SD) at draw z."""
    true = recovery * math.exp(RECOVERY_SD * z - RECOVERY_SD ** 2 / 2)
    return min(SEASON_GAMES, max(0, math.ceil(true - gap)))


def _grid() -> np.ndarray:
    from statistics import NormalDist

    return np.array([NormalDist().inv_cdf((i + 0.5) / GRID) for i in range(GRID)])


def expected_out(recovery: float, gap: float) -> float:
    return float(np.mean([games_out(recovery, gap, z) for z in _grid()]))


def _dates(data: SeasonData) -> dict[tuple[str, int], date]:
    out = {}
    for g in data.schedule.games:
        if g.gameday:
            day = date.fromisoformat(g.gameday)
            out[(g.home_team, g.week)] = out[(g.away_team, g.week)] = day
    return out


def week_one(data: SeasonData) -> date | None:
    days = [date.fromisoformat(g.gameday) for g in data.schedule.games if g.week == 1 and g.gameday]
    return min(days) if days else None


def next_week_one(data: SeasonData, schedule) -> date | None:
    """Next season's first game day: from its `schedule` when it has dates, else a year after `data`'s."""
    days = [date.fromisoformat(g.gameday) for g in schedule.games if g.week == 1 and g.gameday]
    this = week_one(data)
    return min(days) if days else this + timedelta(days=364) if this else None


def carried(data: SeasonData, next_week_one: date,
            detail: Mapping[int, tuple[str | None, str | None]] | None = None, everyone: bool = False) -> dict[int, Carry]:
    """{player: Carry} for the players hurt at `data`'s newest injury report and not expected to be fully
    past it by next season's week 1 (expected games out may still be 0: the truth may keep him out).
    `detail`: {player: (body detail, notes)} from the current status, when known. `everyone`: every player
    still tagged, even one sure to be back (for `check`)."""
    if not data.injuries:
        return {}
    newest = max(week for _, week in data.injuries)
    dates, detail = _dates(data), detail or {}
    out = {}
    for (pid, week), injury in data.injuries.items():
        player = data.players.get(pid)
        if week != newest or injury.tag not in IR_TAGS or player is None or player.position not in POSITIONS:
            continue
        start = week
        while (pid, start - 1) in data.injuries and data.injuries[(pid, start - 1)].tag in IR_TAGS \
                and (pid, start - 1) not in data.actual:
            start -= 1
        games = [w for w, _ in data.team_by_week.get(pid, ()) if w < start]
        team = data.team_at(pid, start)
        hurt = dates.get((team, max(games))) if games else None
        hurt = hurt or (dates.get((team, start)) or next_week_one - timedelta(days=364)) - timedelta(days=7)
        body, notes = detail.get(pid, (None, None))
        body = body or injury.injury or "Undisclosed"
        recovery = recovery_weeks(body, notes)
        gap = (next_week_one - hurt).days / 7.0
        if recovery * math.exp(3 * RECOVERY_SD) <= gap and not everyone:
            continue                                     # back before week 1 even at the slow end
        out[pid] = Carry(body[:60], max(games) if games else start - 1, recovery, gap, expected_out(recovery, gap))
    return out


def with_expected(belief, carries: Mapping[int, Carry]):
    """The belief (a sim_world.World) with each carried player believed out for his expected games (rounded):
    the market prices the missed weeks and his teammates' share of them (sim_market.values)."""
    from dataclasses import replace
    from types import MappingProxyType

    return replace(belief, players=MappingProxyType({
        pid: replace(p, out_weeks=round(carries[pid].expected)) if pid in carries else p
        for pid, p in belief.players.items()}))


def draw(carries: Mapping[int, Carry], seed: int) -> dict[int, Spell]:
    """{player: the absence he really starts the season with} (none when the draw has him back for week 1)."""
    out = {}
    for pid in sorted(carries):
        c = carries[pid]
        z = float(replay_state.rng(seed, "sim", "carryover", pid).standard_normal())
        missed = games_out(c.recovery, c.gap, z)
        if missed > 0:
            out[pid] = Spell(games_out=missed, exit_share=1.0, body=c.body, questionable_back=False)
    return out


def note(c: Carry) -> str:
    """The preseason news line: when the public expects him back (the expected games out, rounded)."""
    when = round(c.expected)
    hurt = f"hurt in week {c.hurt_week} last season" if c.hurt_week > 0 else "hurt before last season"
    back = "expected to be ready for week 1" if when == 0 else f"expected back around week {when + 1}"
    return f"Recovering from {c.body} ({hurt}); {back}."


def check(seasons: list[int]) -> dict[str, float]:
    """Real cross-season returns vs this model (see the module docstring): players still tagged at the end of
    each season in `seasons` who played the next one."""
    from database.db import SessionLocal
    from services import replay_pool

    hurt, healthy, predicted, real_out, model_out = [], [], [], [], []
    with SessionLocal() as session:
        for season in seasons:
            data, after = (replay_pool.load_season_data(session, s, "ppr") for s in (season, season + 1))
            found = carried(data, week_one(after), everyone=True)
            first = {pid: min(w for w, _ in games) for pid, games in after.team_by_week.items() if games}
            for pid, c in found.items():
                if pid in first:
                    hurt.append(first[pid] > 1)
                    real_out.append(first[pid] - 1)
                    predicted.append(float(np.mean([games_out(c.recovery, c.gap, z) > 0 for z in _grid()])))
                    model_out.append(c.expected)
            late = {pid for (pid, week) in data.actual if week >= data.last_week - 1}
            healthy += [first[pid] > 1 for pid in late if pid in first and pid not in found]

    def share(rows: list) -> float:
        return 100.0 * sum(rows) / max(1, len(rows))

    return {"players": len(hurt), "real missed week 1 %": share(hurt), "healthy missed week 1 %": share(healthy),
            "real excess %": share(hurt) - share(healthy), "model missed week 1 %": share(predicted),
            "real games out (mean)": sum(real_out) / max(1, len(real_out)),
            "model games out (mean)": sum(model_out) / max(1, len(model_out))}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Carried injuries: real cross-season returns vs the model.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--seasons", default="2018-2024")
    first_season, _, last_season = parser.parse_args().seasons.partition("-")
    for key, value in check(list(range(int(first_season), int(last_season or first_season) + 1))).items():
        print(f"{key:28s} {value:8.2f}")
