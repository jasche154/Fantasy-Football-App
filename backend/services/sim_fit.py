"""Simulated seasons: measure the game model's constants from real seasons (writes sim_params.json).

Run through `python -m services.sim_eval --fit [--seasons 2021-2025]`. Read-only on the database.

- league / positions: per team-game volumes and per-position rates (catch rate, yards per catch, TDs per
  catch, yards and TDs per carry, lost fumbles per touch) from player_stats_weekly; sacks, INTs and points
  from team_game_stats.
- game: the efficiency spread by the method of moments on team-game residuals (each team-season's mean taken
  out): the variance of log(yards per target) left after the per-target sampling noise, and its opponent share (how
  a game's volume spreads and follows the score is sim_script's, fitted on play-by-play by sim_script_fit).
  Share spread (a player's weight x exp(sd z) in a game): Var(count - n p) - n p (1 - p) = sd^2 (n p (1 - p))^2,
  p = his share over nearby games (LOCAL_WEEKS) with the same regulars active, so slow role changes and
  absences are not counted as game noise. (The simulator's volume and multinomial draws already explain
  most of it, so sim_eval tunes the spreads well below these measurements.)
- rates: per-catch / per-carry yard spreads around each player-season's own mean.
- kick: FG band shares and make rates, XP rate, two-point share; FG attempts ~ Poisson regression on the
  team's yards and TDs. dst: return TDs per INT / lost fumble (least squares), safeties, blocks.
- adp_curve: per scoring, the Nth by ADP at a position gets the Nth highest week-1 projection (averaged
  over seasons); adp_stdev: stdev = a + b x adp; kdst_adp / qb_adp: the mean ADP of the Nth K, D/ST and QB
  taken on the real 1-QB PPR lists.
"""
import json
import logging
import math
from collections import defaultdict
from statistics import mean

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.sim_world import PARAMS_PATH

log = logging.getLogger(__name__)

POSITION_GROUPS = ("QB", "RB", "WR", "TE")
LOCAL_WEEKS = 3                  # a player's game-level share is compared with his games this close
REGULAR_SHARE = 0.08             # a player with this mean share is a regular (his absence moves the others)
DEPTH_RANK = {"WR": 3, "TE": 1, "RB": 2}   # players below this rank on a team are depth
CURVE_LENGTH = {"QB": 40, "RB": 80, "WR": 100, "TE": 40, "K": 32, "DST": 32}
DESIGN = {                       # model choices, not measurements (see sim_game / sim_draw docstrings)
    "game": {"td_power": 1.0, "rush_power": 0.5, "int_power": 0.5},
    "rates": {"rec_shift": 3.0, "rush_shift": 4.0},
    "positions": {"QB": {"backup_starts": 0.05}},
}


def _rows(session: Session, sql: str, **bind) -> list:
    return list(session.execute(text(sql), bind))


def _player_games(session: Session, seasons: list[int]) -> list[dict]:
    """Skill player-games with their team (the opponent's opponent in the schedule)."""
    games = _rows(session, "select season, week, home_team, away_team from nfl_games where game_type='REG'")
    opp = {(s, w, h): a for s, w, h, a in games} | {(s, w, a): h for s, w, h, a in games}
    sql = f"""select s.season, s.week, s.opponent_team, s.player_id, p.position,
        coalesce(targets,0), coalesce(receptions,0), coalesce(receiving_yards,0), coalesce(receiving_tds,0),
        coalesce(carries,0), coalesce(rushing_yards,0), coalesce(rushing_tds,0), coalesce(attempts,0),
        coalesce(passing_yards,0), coalesce(passing_interceptions,0), coalesce(sacks_suffered,0),
        coalesce(sack_fumbles_lost,0), coalesce(rushing_fumbles_lost,0) + coalesce(receiving_fumbles_lost,0)
        from player_stats_weekly s join players p on p.id = s.player_id
        where s.season_type='REG' and s.season in ({",".join(map(str, seasons))})"""
    keys = ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "rush_td", "attempts", "pass_yds",
            "ints", "sacks", "sack_fumbles", "fumbles")
    out = []
    for row in _rows(session, sql):
        team = opp.get((row[0], row[1], row[2]))
        if team:
            group = row[4] if row[4] in POSITION_GROUPS else "other"
            out.append({"season": row[0], "week": row[1], "team": team, "opponent": row[2], "pid": row[3],
                        "pos": group, **dict(zip(keys, row[5:]))})
    return out


def _team_games(lines: list[dict]) -> dict[tuple, dict]:
    totals: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
    for g in lines:
        t = totals[(g["season"], g["week"], g["team"])]
        t["opponent"] = g["opponent"]
        for key in ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "rush_td", "attempts", "ints",
                    "sacks", "sack_fumbles", "fumbles"):
            t[key] += g[key]
        if g["pos"] != "QB":
            t["rb_carries"] += g["carries"]
            t["rb_rush_yds"] += g["rush_yds"]
        if g["pos"] == "other":
            t["other_targets"] += g["targets"]
            t["other_carries"] += g["carries"]
    return totals


def _positions(lines: list[dict], teams: dict) -> dict:
    sums: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for g in lines:
        s = sums[g["pos"]]
        for key in ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "rush_td", "fumbles"):
            s[key] += g[key]
    out = {}
    for pos, s in sums.items():
        out[pos] = {"catch": s["rec"] / max(1, s["targets"]), "ypc": s["rec_yds"] / max(1, s["rec"]),
                    "rec_td": s["rec_td"] / max(1, s["rec"]), "ypr": s["rush_yds"] / max(1, s["carries"]),
                    "rush_td": s["rush_td"] / max(1, s["carries"]),
                    "fumble": s["fumbles"] / max(1, s["rec"] + s["carries"]),
                    "carries_per_target": s["carries"] / max(1, s["targets"])}
    seasons: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))     # (season, team, pid) totals
    for g in lines:
        k = seasons[(g["season"], g["team"], g["pid"], g["pos"])]
        k["games"] += 1
        k["targets"] += g["targets"] / max(1, teams[(g["season"], g["week"], g["team"])]["targets"])
        k["carries"] += g["carries"] / max(1, teams[(g["season"], g["week"], g["team"])]["carries"])
    ranked: dict[tuple, list] = defaultdict(list)
    for (season, team, pid, pos), k in seasons.items():
        ranked[(season, team, pos)].append((k["targets"] + k["carries"], k["targets"] / k["games"],
                                            k["carries"] / k["games"]))
    for pos, cut in DEPTH_RANK.items():
        depth = [row for (_, _, p), rows in ranked.items() if p == pos for row in sorted(rows, reverse=True)[cut:]]
        out[pos]["depth_targets"] = mean(r[1] for r in depth)
        out[pos]["depth_carries"] = mean(r[2] for r in depth)
    starters = [max(rows)[2] for (_, _, p), rows in ranked.items() if p == "QB"]
    out["QB"]["starter_carries"] = mean(starters)
    return out


def _target_variance(positions: dict, rates: dict) -> float:
    """Variance of one target's receiving yards (a catch or not, then the per-catch spread)."""
    wr = positions["WR"]
    sd = rates["rec_cv"] * wr["ypc"]
    return wr["catch"] * (sd ** 2 + wr["ypc"] ** 2) - (wr["catch"] * wr["ypc"]) ** 2


def _moments(teams: dict, target_variance: float) -> dict:
    """The environment spreads (see the module docstring)."""
    by_season: dict[tuple, list] = defaultdict(list)
    for (season, _, team), t in teams.items():
        by_season[(season, team)].append(t)
    for rows in by_season.values():
        mt, mc = mean(r["targets"] for r in rows), mean(r["carries"] for r in rows)
        my = sum(r["rec_yds"] for r in rows) / max(1, sum(r["targets"] for r in rows))
        for r in rows:
            r["dt"] = math.log(max(r["targets"], 1) / mt) - 0.5 / mt
            r["dc"] = math.log(max(r["carries"], 1) / mc) - 0.5 / mc
            r["dy"] = math.log(max(r["rec_yds"], 1) / max(r["targets"], 1) / my)
            r["noise_y"] = target_variance / (max(r["targets"], 1) * my ** 2)    # sampling variance of the log
    rows = list(teams.values())
    pairs = [(t, teams[(s, w, t["opponent"])]) for (s, w, _), t in teams.items() if (s, w, t["opponent"]) in teams]
    cov = lambda a, b, items: float(np.cov([x[0][a] for x in items], [x[1][b] for x in items])[0, 1])   # noqa: E731
    eff_var = max(1e-4, np.var([r["dy"] for r in rows]) - mean(r["noise_y"] for r in rows))
    return {"eff_sd": math.sqrt(eff_var), "eff_shared": min(0.9, max(0.0, cov("dy", "dy", pairs) / eff_var))}


def _share_sd(lines: list[dict], teams: dict, stat: str, positions: tuple[str, ...] = ("RB", "WR", "TE")) -> float:
    """Game-to-game spread of a player's `stat` share (see the module docstring)."""
    games: dict[tuple, list] = defaultdict(list)
    shares: dict[tuple, list] = defaultdict(list)          # (season, team, pid) -> shares per game played
    for g in lines:
        total = teams[(g["season"], g["week"], g["team"])][stat]
        if total > 0 and g["pos"] in positions:
            games[(g["season"], g["pid"], g["team"])].append((g["week"], g[stat], total))
            shares[(g["season"], g["team"], g["pid"])].append(g[stat] / total)
    regulars: dict[tuple, set] = defaultdict(set)          # (season, team, week) -> regulars who played
    for g in lines:
        played = shares.get((g["season"], g["team"], g["pid"]))
        if played and mean(played) >= REGULAR_SHARE:
            regulars[(g["season"], g["team"], g["week"])].add(g["pid"])
    excess = scale = 0.0
    for (season, _, team), played in games.items():
        for week, count, total in played:
            crew = regulars[(season, team, week)]
            near = [(c, n) for w, c, n in played if w != week and abs(w - week) <= LOCAL_WEEKS
                    and regulars[(season, team, w)] == crew]      # same regulars: no absence moves his share
            if not near:
                continue
            p = sum(c for c, _ in near) / sum(n for _, n in near)
            if 0 < p < 1:
                excess += (count - total * p) ** 2 - total * p * (1 - p)
                scale += (total * p * (1 - p)) ** 2
    return math.sqrt(max(0.0, excess / scale))


def _rates(lines: list[dict]) -> dict:
    seasons: dict[tuple, list] = defaultdict(list)
    for g in lines:
        seasons[(g["season"], g["pid"])].append(g)
    rec_num = rec_den = rush_num = rush_den = 0.0
    for games in seasons.values():
        catches, carries = sum(g["rec"] for g in games), sum(g["carries"] for g in games)
        ypc = sum(g["rec_yds"] for g in games) / catches if catches else 0
        ypr = sum(g["rush_yds"] for g in games) / carries if carries else 0
        for g in games:
            if g["rec"]:
                rec_num += (g["rec_yds"] - g["rec"] * ypc) ** 2
                rec_den += g["rec"] * max(ypc, 1.0) ** 2
            if g["carries"] and g["pos"] != "QB":
                rush_num += (g["rush_yds"] - g["carries"] * ypr) ** 2
                rush_den += g["carries"]
    return {"rec_cv": math.sqrt(rec_num / rec_den), "rush_sd": math.sqrt(rush_num / rush_den)}


def _backup_quality(lines: list[dict]) -> float:
    """Yards per attempt of every QB but each team-season's main passer, over the league's."""
    yards: dict[tuple, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    attempts: dict[tuple, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for g in lines:
        if g["pos"] == "QB" and g["attempts"]:
            yards[(g["season"], g["team"])][g["pid"]] += g["pass_yds"]
            attempts[(g["season"], g["team"])][g["pid"]] += g["attempts"]
    backup_yds = backup_att = 0.0
    for key, by_qb in yards.items():
        main = max(by_qb, key=by_qb.get)
        backup_yds += sum(v for pid, v in by_qb.items() if pid != main)
        backup_att += sum(v for pid, v in attempts[key].items() if pid != main)
    all_yds = sum(sum(v.values()) for v in yards.values())
    all_att = sum(sum(v.values()) for v in attempts.values())
    return (backup_yds / backup_att) / (all_yds / all_att)


def _league(session: Session, seasons: list[int], lines: list[dict], teams: dict) -> dict:
    rows = list(teams.values())

    def total(key: str) -> float:
        return sum(r[key] for r in rows)
    points = [r[0] for r in _rows(session, f"""select points_for from team_game_stats
        where points_for is not null and season in ({",".join(map(str, seasons))})""")]
    return {"targets": total("targets") / len(rows), "carries": total("carries") / len(rows),
            "rb_carries": total("rb_carries") / len(rows), "pass_yards": total("rec_yds") / len(rows),
            "attempts_per_target": total("attempts") / total("targets"), "int_rate": total("ints") / total("attempts"),
            "sack_rate": total("sacks") / total("attempts"), "sack_fumble": total("sack_fumbles") / total("sacks"),
            "rb_ypr": total("rb_rush_yds") / total("rb_carries"),
            "other_targets": total("other_targets") / total("targets"),
            "other_carries": total("other_carries") / total("carries"),
            "yards": (total("rec_yds") + total("rush_yds")) / len(rows),
            "tds": (total("rec_td") + total("rush_td")) / len(rows), "fumbles": total("fumbles") / len(rows),
            "points": mean(points), "points_sd": float(np.std(points)), "backup_quality": _backup_quality(lines)}


def _poisson(x: np.ndarray, y: np.ndarray, iterations: int = 25) -> np.ndarray:
    beta = np.zeros(x.shape[1])
    beta[0] = math.log(max(y.mean(), 1e-6))
    for _ in range(iterations):
        mu = np.exp(x @ beta)
        beta = beta + np.linalg.solve(x.T @ (x * mu[:, None]), x.T @ (y - mu))
    return beta


def _in(seasons: list[int]) -> str:
    return f"season in ({','.join(str(int(s)) for s in seasons)})"


def _kick(session: Session, seasons: list[int], teams: dict) -> dict:
    made = _rows(session, f"""select sum(made_0_39), sum(made_40_49), sum(made_50), sum(missed_0_39),
        sum(missed_40_49), sum(missed_50), sum(pat_made), sum(pat_att) from kicker_games where {_in(seasons)}""")[0]
    attempts = [made[i] + made[i + 3] for i in range(3)]
    stats = {(s, w, t): (fga, pat, dtd) for s, w, t, fga, pat, dtd in _rows(
        session, f"select season, week, team, fg_att, pat_att, def_tds from team_game_stats where {_in(seasons)}")}
    keys = [k for k in teams if k in stats]
    yards = np.array([teams[k]["rec_yds"] + teams[k]["rush_yds"] for k in keys], dtype=float)
    tds = np.array([teams[k]["rec_td"] + teams[k]["rush_td"] for k in keys], dtype=float)
    x = np.column_stack([np.ones(len(keys)), np.log(np.maximum(yards, 1) / yards.mean()), tds - tds.mean()])
    fga = np.array([stats[k][0] for k in keys], dtype=float)
    beta = _poisson(x, fga)
    fitted = np.exp(x @ beta)
    # FG attempts are under-dispersed: a binomial over n chances with var = m (1 - m / n) given the mean
    left = max(1e-3, fga.mean() - (fga.var() - fitted.var()))
    all_tds = tds.sum() + sum(stats[k][2] for k in keys)
    return {"bands": [a / sum(attempts) for a in attempts], "make": [made[i] / attempts[i] for i in range(3)],
            "xp": made[6] / made[7], "two_point": max(0.0, 1 - sum(stats[k][1] for k in keys) / all_tds),
            "fga_log": float(beta[0]), "fga_yards": float(beta[1]), "fga_tds": float(beta[2]),
            "fga_chances": float(fga.mean() ** 2 / left)}


def _dst(session: Session, seasons: list[int]) -> dict:
    rows = np.array(_rows(session, f"""select def_ints, def_fumble_rec, def_tds, def_safeties, def_blocks
        from team_game_stats where {_in(seasons)}"""), dtype=float)
    x = np.column_stack([rows[:, 0], rows[:, 1], np.ones(len(rows))])
    beta = np.linalg.lstsq(x, rows[:, 2], rcond=None)[0]
    return {"int_td": float(max(0.0, beta[0])), "fum_td": float(max(0.0, beta[1])), "st_td": float(max(0.0, beta[2])),
            "safety": float(rows[:, 3].mean()), "block": float(rows[:, 4].mean()), "fumble_rec": float(rows[:, 1].mean())}


def _adp_curve(session: Session, seasons: list[int]) -> dict:
    """{scoring: {position: [points per game of the Nth by ADP]}} (see the module docstring)."""
    positions = dict(_rows(session, "select id, position from players"))
    curves: dict = {}
    for scoring in ("ppr", "half", "std"):
        ranks: dict[str, list[list[float]]] = defaultdict(list)
        for season in seasons:
            drafted = _rows(session, "select player_id, adp from replay_adp where season=:s and format='ppr'", s=season)
            week_one: dict[int, float] = {}
            for pid, _, points in _rows(session, f"""select player_id, source, pts_{scoring} from replay_projections
                    where season=:s and week=1 and pts_{scoring} > 0 order by source desc""", s=season):
                week_one[pid] = points          # 'espn' sorts last, so it wins
            for position, length in CURVE_LENGTH.items():
                count = sum(1 for pid, _ in drafted if positions.get(pid) == position)
                best = sorted((p for pid, p in week_one.items() if positions.get(pid) == position), reverse=True)
                if count and best:
                    ranks[position].append(best[:min(length, count)])
        curves[scoring] = {pos: [round(mean(v[i] for v in lists if len(v) > i), 2)
                                 for i in range(max(len(v) for v in lists))] for pos, lists in ranks.items()}
    return curves


def _kdst_adp(session: Session, seasons: list[int], positions: tuple[str, ...] = ("K", "DST"),
              every: bool = False) -> dict[str, list[float]]:
    """{position: mean ADP of the Nth one drafted} over the seasons' 1-QB PPR lists (K and D/ST; "qb_adp": QB).
    `every`: only the ranks every season lists (a tail averaged over fewer lists is not monotonic)."""
    out = {}
    for position in positions:
        lists = [[r[0] for r in _rows(session, """select a.adp from replay_adp a join players p on p.id = a.player_id
                  where a.season=:s and a.format='ppr' and p.position=:p order by a.adp""", s=season, p=position)]
                 for season in seasons]
        lists = [v for v in lists if v]
        out[position] = [round(mean(v[i] for v in lists if len(v) > i), 1) for i in range((min if every else max)(map(len, lists)))]
    return out


def _adp_stdev(session: Session, seasons: list[int]) -> list[float]:
    rows = np.array(_rows(session, f"select adp, stdev from replay_adp where stdev is not null and {_in(seasons)}"),
                    dtype=float)
    slope, intercept = np.polyfit(rows[:, 0], rows[:, 1], 1)
    return [float(intercept), float(slope)]


def fit(session: Session, seasons: list[int]) -> dict:
    lines = _player_games(session, seasons)
    teams = _team_games(lines)
    positions = _positions(lines, teams)
    rates = {**_rates(lines), **DESIGN["rates"]}
    game = {**_moments(teams, _target_variance(positions, rates)), "share_sd_targets": _share_sd(lines, teams, "targets"),
            "share_sd_carries": _share_sd(lines, teams, "carries"),
            "share_sd_qb_carries": _share_sd(lines, teams, "carries", ("QB",)), **DESIGN["game"]}
    for position, extra in DESIGN["positions"].items():
        positions[position].update(extra)
    league, dst = _league(session, seasons, lines, teams), _dst(session, seasons)
    dst["extra_fumbles"] = max(0.0, dst.pop("fumble_rec") - league["fumbles"])     # special teams, muffs
    return {"seasons": seasons, "league": league, "positions": positions,
            "rates": rates, "game": game, "kick": _kick(session, seasons, teams), "dst": dst,
            "adp_curve": _adp_curve(session, seasons), "adp_stdev": _adp_stdev(session, seasons),
            "kdst_adp": _kdst_adp(session, seasons), "qb_adp": _kdst_adp(session, seasons, ("QB",), every=True)["QB"]}


def _rounded(value):
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def write(fitted: dict, tuned: dict | None = None) -> None:
    """Store fitted constants, keeping the tuned section already in the file unless one is given."""
    old = json.loads(PARAMS_PATH.read_text()) if PARAMS_PATH.exists() else {}
    PARAMS_PATH.write_text(json.dumps(_rounded({**fitted, "tuned": tuned if tuned is not None else old.get("tuned", {})}),
                                      indent=1) + "\n")
    log.info("Wrote %s", PARAMS_PATH)
