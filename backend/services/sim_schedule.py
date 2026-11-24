"""Simulated seasons: a believable NFL regular-season schedule for a season not yet published.

Opponents follow the NFL formula (17 games per team):
- 6 division games, home and away;
- 4 against one division of its own conference (INTRA cycle, 3 years) and 4 against one of the other
  conference (INTER cycle, 4 years): two home, two away;
- 2 against the same-place finishers of the two remaining same-conference divisions (one home, one away);
- the 17th against the same-place finisher of an other-conference division: the INTER pairing of two seasons
  earlier, hosted by the AFC in odd seasons and the NFC in even ones.
The cycles are anchored on the real 2023-2026 schedules in the database (every pairing there checks out; the
Falcons' published 2027 opponents agree), so 2027 is: same conference East-South and North-West (both
conferences); AFC East-NFC East, North-West, South-South, West-North; 17th game (AFC home) AFC East-NFC South,
North-North, South-West, West-East. Rebuilding 2026's opponents from 2025's standings matches the real schedule.

Shortcut: home/away inside a rotation is seeded, not the real "opposite of last time" rule (that needs the
schedule of 3-4 years earlier), and the week-by-week order is random within the rules below.

Weeks (`build`): one bye per team in BYE_WEEKS (2-6 teams, an even number, per bye week), week 18 all division
games (as the NFL has done since 2021), the rest a random order found week by week (a perfect matching each
week; a dead end restarts with the next sub-seed). Shortcut: every game is on the week's Sunday (real weeks have
Thursday and Monday games), at the usual Sunday kickoff times.
"""
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from types import MappingProxyType

import numpy as np

from services.schedule_strength import Game, Schedule

DIVISIONS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "AFC East": ("BUF", "MIA", "NE", "NYJ"), "AFC North": ("BAL", "CIN", "CLE", "PIT"),
    "AFC South": ("HOU", "IND", "JAX", "TEN"), "AFC West": ("DEN", "KC", "LAC", "LV"),
    "NFC East": ("DAL", "NYG", "PHI", "WAS"), "NFC North": ("CHI", "DET", "GB", "MIN"),
    "NFC South": ("ATL", "CAR", "NO", "TB"), "NFC West": ("ARI", "LA", "SEA", "SF")})
TEAMS = tuple(sorted(team for teams in DIVISIONS.values() for team in teams))
DIVISION_OF = MappingProxyType({team: division for division, teams in DIVISIONS.items() for team in teams})
# Same-conference pairings by region, 3-year cycle from 2024 (the same in both conferences).
INTRA_ANCHOR, INTRA = 2024, ((("East", "South"), ("North", "West")), (("East", "North"), ("South", "West")),
                             (("East", "West"), ("North", "South")))
# AFC region -> NFC region, 4-year cycle from 2023.
INTER_ANCHOR, INTER = 2023, ({"East": "East", "North": "West", "South": "South", "West": "North"},
                             {"East": "West", "North": "East", "South": "North", "West": "South"},
                             {"East": "South", "North": "North", "South": "West", "West": "East"},
                             {"East": "North", "North": "South", "South": "East", "West": "West"})
SEVENTEENTH_LAG = 2                 # the 17th game's pairing is the INTER pairing of two seasons earlier
WEEKS = 18
BYE_WEEKS = range(5, 15)
MIN_BYES, MAX_BYES = 2, 6
EARLY_KICKOFF = "13:00"
LATE_KICKOFFS = ("16:05", "16:25", "16:25", "20:20")   # each week's last games: two late windows and the night game
ATTEMPTS = 200
SEARCH_BUDGET = 20_000              # matching steps per week before that attempt is abandoned


def rotation(season: int) -> dict[str, dict[str, str]]:
    """{"intra" | "inter" | "17th": {division: the division it is paired with}} for a season."""
    out: dict[str, dict[str, str]] = {"intra": {}, "inter": {}, "17th": {}}
    for conference in ("AFC", "NFC"):
        for a, b in INTRA[(season - INTRA_ANCHOR) % 3]:
            out["intra"][f"{conference} {a}"], out["intra"][f"{conference} {b}"] = f"{conference} {b}", f"{conference} {a}"
    for key, lag in (("inter", 0), ("17th", SEVENTEENTH_LAG)):
        for afc, nfc in INTER[(season - lag - INTER_ANCHOR) % 4].items():
            out[key][f"AFC {afc}"], out[key][f"NFC {nfc}"] = f"NFC {nfc}", f"AFC {afc}"
    return out


def _check_standings(standings: Mapping[str, int]) -> None:
    for division, teams in DIVISIONS.items():
        places = sorted(standings.get(team, 0) for team in teams)
        if places != [1, 2, 3, 4]:
            raise ValueError(f"{division} needs places 1-4 once each, got {[standings.get(t) for t in teams]}.")


def _by_place(division: str, standings: Mapping[str, int]) -> dict[int, str]:
    return {standings[team]: team for team in DIVISIONS[division]}


def opponents(season: int, standings: Mapping[str, int], seed: int = 0) -> list[tuple[str, str]]:
    """The season's 272 games as (home, away), unordered in time."""
    _check_standings(standings)
    dice = np.random.default_rng([seed, season, 1])
    rot = rotation(season)
    games: list[tuple[str, str]] = []
    for teams in DIVISIONS.values():
        games += [(a, b) for a in teams for b in teams if a != b]
    done: set[frozenset] = set()
    for division, other in (*rot["intra"].items(), *rot["inter"].items()):
        if frozenset((division, other)) in done:
            continue
        done.add(frozenset((division, other)))
        mine, theirs = list(DIVISIONS[division]), list(DIVISIONS[other])
        dice.shuffle(mine)
        dice.shuffle(theirs)
        flip = bool(dice.integers(2))
        games += [(a, b) if ((i + j) % 2 == 0) != flip else (b, a)
                  for i, a in enumerate(mine) for j, b in enumerate(theirs)]
    for conference in ("AFC", "NFC"):
        # Rotation pairs (D1, D2), (D3, D4): hosts run D1 -> D3 -> D2 -> D4 -> D1, so each team is home once.
        first = next(d for d in DIVISIONS if d.startswith(conference))
        d1, d2 = first, rot["intra"][first]
        d3 = next(d for d in DIVISIONS if d.startswith(conference) and d not in (d1, d2))
        d4 = rot["intra"][d3]
        cycle = [d1, d3, d2, d4] if dice.integers(2) else [d1, d4, d2, d3]
        for host, guest in zip(cycle, cycle[1:] + cycle[:1]):
            hosts, guests = _by_place(host, standings), _by_place(guest, standings)
            games += [(hosts[p], guests[p]) for p in range(1, 5)]
    host_conference = "AFC" if season % 2 else "NFC"
    for division in (d for d in DIVISIONS if d.startswith(host_conference)):
        hosts, guests = _by_place(division, standings), _by_place(rot["17th"][division], standings)
        games += [(hosts[p], guests[p]) for p in range(1, 5)]
    return games


def _byes(dice: np.random.Generator) -> dict[str, int]:
    counts = Counter({week: MIN_BYES for week in BYE_WEEKS})
    while sum(counts.values()) < len(TEAMS):
        week = int(dice.choice([w for w in BYE_WEEKS if counts[w] < MAX_BYES]))
        counts[week] += 2
    weeks = [week for week, n in sorted(counts.items()) for _ in range(n)]
    order = list(TEAMS)
    dice.shuffle(order)
    return dict(zip(order, weeks))


def _match(teams: set[str], left: list[tuple[str, str]], dice: np.random.Generator,
           allowed=lambda game: True) -> list[int] | None:
    """Indexes into `left` of games pairing off every team in `teams` once (depth-first, fewest options first)."""
    options: dict[str, list[int]] = defaultdict(list)
    for i, (home, away) in enumerate(left):
        if home in teams and away in teams and allowed((home, away)):
            options[home].append(i)
            options[away].append(i)
    for team in options:
        dice.shuffle(options[team])
    budget = [SEARCH_BUDGET]

    def search(free: set[str], chosen: list[int]) -> list[int] | None:
        if not free:
            return chosen
        budget[0] -= 1
        if budget[0] < 0:
            return None
        # sorted: ties go to the first team code, never to str hash order (which differs per process)
        team = min(sorted(free), key=lambda t: sum(1 for i in options[t] if set(left[i]) <= free))
        for i in options[team]:
            pair = set(left[i])
            if pair <= free:
                found = search(free - pair, chosen + [i])
                if found is not None:
                    return found
        return None

    return search(set(teams), [])


def _weeks(games: list[tuple[str, str]], byes: Mapping[str, int],
           dice: np.random.Generator) -> dict[int, list[tuple[str, str]]] | None:
    left = list(games)
    plan: dict[int, list[tuple[str, str]]] = {}
    for week in (WEEKS, *range(1, WEEKS)):                  # week 18 first: division games only
        playing = {team for team in TEAMS if byes[team] != week}
        same = (lambda g: DIVISION_OF[g[0]] == DIVISION_OF[g[1]]) if week == WEEKS else (lambda g: True)
        picked = _match(playing, left, dice, same)
        if picked is None:
            return None
        plan[week] = [left[i] for i in picked]
        taken = set(picked)
        left = [g for i, g in enumerate(left) if i not in taken]
    return plan


def week_one_sunday(season: int) -> date:
    """The season opens the Thursday after Labor Day (first Monday of September); its Sunday is 3 days on."""
    first = date(season, 9, 1)
    labor_day = first + timedelta(days=(0 - first.weekday()) % 7)
    return labor_day + timedelta(days=6)


def build(season: int, standings: Mapping[str, int], seed: int) -> Schedule:
    """A full regular season for `season` from last season's division places ({team: 1-4}); scores None."""
    games = opponents(season, standings, seed)
    for attempt in range(ATTEMPTS):
        dice = np.random.default_rng([seed, season, 2, attempt])
        plan = _weeks(games, _byes(dice), dice)
        if plan is not None:
            break
    else:
        raise RuntimeError(f"No {season} schedule found for seed {seed} in {ATTEMPTS} attempts.")
    sunday = week_one_sunday(season)
    out = []
    for week in range(1, WEEKS + 1):
        day = (sunday + timedelta(weeks=week - 1)).isoformat()
        week_games = sorted(plan[week])
        late = len(week_games) - len(LATE_KICKOFFS)
        for i, (home, away) in enumerate(week_games):
            time = EARLY_KICKOFF if i < late else LATE_KICKOFFS[i - late]
            out.append(Game(week, home, away, None, None, None, None, gameday=day, gametime=time))
    return Schedule(games=tuple(out), opponents=_opponent_index(out))


def _opponent_index(games: Iterable[Game]) -> Mapping[tuple[str, int], str]:
    index: dict[tuple[str, int], str] = {}
    for g in games:
        index[(g.home_team, g.week)] = g.away_team
        index[(g.away_team, g.week)] = g.home_team
    return MappingProxyType(index)


def standings_now(games: Iterable[Game], before_week: int | None = None) -> dict[str, int]:
    """{team: place in its division 1-4} from the final games so far (before `before_week` if given): win
    percentage (a tie is half a win), then among teams level on it head-to-head, then division record, then
    points differential, then team code. Shortcut: the NFL's later tiebreakers (common games, strength of
    victory) and its re-start of the steps after a three-way tie is cut to two are not applied."""
    results: list[tuple[str, str, float]] = []      # (team, opponent, 1 / 0.5 / 0)
    diff = Counter()
    for g in games:
        if not g.is_final or (before_week is not None and g.week >= before_week):
            continue
        margin = g.home_score - g.away_score
        for team, other, sign in ((g.home_team, g.away_team, 1), (g.away_team, g.home_team, -1)):
            diff[team] += sign * margin
            results.append((team, other, 1.0 if sign * margin > 0 else 0.5 if margin == 0 else 0.0))

    def pct(team: str, among: Iterable[str] | None = None) -> float:
        mine = [r for t, o, r in results if t == team and (among is None or o in among)]
        return sum(mine) / len(mine) if mine else 0.0

    out = {}
    for teams in DIVISIONS.values():
        level = {t: [u for u in teams if pct(u) == pct(t)] for t in teams}
        ranked = sorted(teams, key=lambda t: (-pct(t), -pct(t, level[t]), -pct(t, teams), -diff[t], t))
        out.update({team: place for place, team in enumerate(ranked, start=1)})
    return out
