"""Simulated seasons: the random draw of one game (services/sim_game.play; the model is described there).

Counts are integers by construction: targets and carries are split by a multinomial (each role weight times
a small lognormal game draw, relative so a small role stays small), catches and fumbles are binomial, and yards
are whole numbers per player whose sums ARE the passer's line. A team's touchdowns are drive-limited (`_touchdowns`):
their total is Binomial(td_drives, mean / td_drives) around the mean its catches and carries give (each one's TD
rate), so a team's score is less dispersed than independent plays would make it, as real scores are (a team
has about a dozen drives and each ends in one score at most); the total is then shared out play by play in
proportion to each catch's or carry's TD rate. `td_drives` is tuned with the realism check (sim_eval: the
spread of results around the pregame line and of team scores). Per-catch
and per-carry yards are shifted gammas (mean m, standard deviation sd, never below -shift), so a sum over n
plays is one gamma draw: Gamma(n k, theta) - n shift with k = (m + shift)^2 / sd^2, theta = sd^2 / (m + shift).

Overtime (`_overtime`): a game tied after regulation stays tied with OT_TIE, else one side wins it (in proportion
to the two sides' mean points) with a field goal (OT_FIELD_GOAL of the time, a made kick in a distance band
drawn like any other) or a touchdown (no try: it ends the game), credited to a receiver or a rusher drawn in
proportion to his expected TDs, with OT_TD_YARDS on the play, so the passer's line (the receivers' sum) and the
box score still add up. OT_TIE is set so simulated games end tied about as often as real ones (0.29% of regular
season games 2021-2025); the field goal share is the design choice of about half of decided overtimes.
"""
from collections.abc import Sequence
from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np

from services import kdst_model
from services.replay_box import BoxLine
from services.replay_pool import Usage
from services.scoring import ScoringSettings
from services.sim_game import (
    MAX_TD_RATE, MIN_SNAP_TO_RECORD, TWO_POINT_SUCCESS, GameResult, PlayerGame, _dst_points, _mean_points, _side_means,
    _skill_points, fga_mean,
)
from services.sim_world import params

TD_DRIVES = 6.0                  # default of the tuned `game.td_drives`
OT_TIE = 0.09                    # a tied game stays tied after overtime: 0.29% real ties / 3.2% simulated regulation ties
OT_FIELD_GOAL = 0.5              # a decided overtime ends on a field goal, else on a touchdown
OT_TD_YARDS = {"rec": 12, "rush": 4}   # yards on the winning touchdown play


@dataclass(frozen=True)
class _Offense:
    targets: np.ndarray
    catches: np.ndarray
    rec_yds: np.ndarray
    rec_td: np.ndarray
    rec_fumbles: np.ndarray
    carries: np.ndarray
    rush_yds: np.ndarray
    rush_td: np.ndarray
    rush_fumbles: np.ndarray
    attempts: int
    ints: int
    sacks: int
    sack_fumbles: int

    @property
    def tds(self) -> int:
        return int(self.rec_td.sum() + self.rush_td.sum())

    @property
    def yards(self) -> int:
        return int(self.rec_yds.sum() + self.rush_yds.sum())

    @property
    def fumbles(self) -> int:
        return int(self.rec_fumbles.sum() + self.rush_fumbles.sum() + self.sack_fumbles)


def _split(total: int, weights: np.ndarray, sd: float | np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """`total` shared around `weights`: each weight x a lognormal game draw (relative noise, so a small role
    stays small), renormalized, then a multinomial."""
    drawn = weights * np.exp(sd * rng.standard_normal(len(weights)))
    return rng.multinomial(total, drawn / drawn.sum())


def _yards(plays: np.ndarray, mean: np.ndarray, sd: np.ndarray, shift: float, rng: np.random.Generator) -> np.ndarray:
    """Whole yards over `plays` per player, each play a shifted gamma (mean, sd)."""
    base = np.maximum(mean + shift, 0.5)
    k, theta = base ** 2 / sd ** 2, sd ** 2 / base
    total = np.where(plays > 0, rng.gamma(np.maximum(plays * k, 1e-9), theta) - plays * shift, 0.0)
    return np.round(total).astype(int)


def _offense(side, n_targets: int, n_carries: int, eff: float, rng: np.random.Generator) -> _Offense:
    game, league, rates = params()["game"], params()["league"], params()["rates"]
    targets = _split(n_targets, side.target_w, game["share_sd_targets"], rng)
    catches = rng.binomial(targets, np.clip(side.catch, 0, 1))
    ypc = side.ypc * eff
    rec_yds = _yards(catches, ypc, rates["rec_cv"] * np.maximum(ypc, 1.0), rates["rec_shift"], rng)
    rec_rate = np.minimum(side.rec_td * eff ** game["td_power"], MAX_TD_RATE)
    spreads = np.full(len(side.carry_w), game["share_sd_carries"])
    if side.qb is not None:
        spreads[-2] = game["share_sd_qb_carries"]          # the starting QB is the last rusher before OTHER
    carries = _split(n_carries, side.carry_w, spreads, rng)
    rush_eff = eff ** game["rush_power"]
    ypr = side.ypr * rush_eff
    rush_yds = _yards(carries, ypr, np.full(len(ypr), rates["rush_sd"]), rates["rush_shift"], rng)
    rush_rate = np.minimum(side.rush_td * rush_eff ** game["td_power"], MAX_TD_RATE)
    rec_td, rush_td = _touchdowns(catches, rec_rate, carries, rush_rate, game.get("td_drives", TD_DRIVES), rng)
    attempts = int(n_targets + rng.binomial(n_targets, max(0.0, league["attempts_per_target"] - 1)))
    sacks = int(rng.binomial(attempts, min(0.5, side.sack_rate)))
    return _Offense(targets, catches, rec_yds, rec_td, rng.binomial(catches, side.rec_fumble), carries, rush_yds,
                    rush_td, rng.binomial(carries, side.fumble), attempts,
                    int(rng.binomial(attempts, min(0.5, side.int_rate / eff ** game["int_power"]))), sacks,
                    int(rng.binomial(sacks, league["sack_fumble"])))


def _touchdowns(catches: np.ndarray, rec_rate: np.ndarray, carries: np.ndarray, rush_rate: np.ndarray,
                drives: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """(receiving TDs, rushing TDs) per player: the team's total drive-limited, then shared play by play (see the
    module docstring). A player never scores more TDs than he has catches or carries."""
    plays = np.concatenate([catches, carries]).astype(int)
    rates = np.concatenate([rec_rate, rush_rate])
    mean = float((plays * rates).sum())
    tds = np.zeros(len(plays), dtype=int)
    if mean <= 0:
        return tds[:len(catches)], tds[len(catches):]
    n = max(int(round(drives)), int(np.ceil(mean)) + 1)
    total = min(int(rng.binomial(n, mean / n)), int(plays.sum()))
    for _ in range(total):
        weights = (plays - tds) * rates
        if weights.sum() <= 0:
            break
        tds[int(rng.choice(len(weights), p=weights / weights.sum()))] += 1
    return tds[:len(catches)], tds[len(catches):]


def _volumes(sides, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(efficiency, targets, carries) per side from the shared environment and game script."""
    game = params()["game"]
    rho, sd = game["eff_shared"], game["eff_sd"]
    z = np.sqrt(rho) * rng.standard_normal() + np.sqrt(1 - rho) * rng.standard_normal(2)
    eff = np.exp(sd * z - sd ** 2 / 2)
    lead = (z[0] - z[1]) / np.sqrt(2 * (1 - rho)) * np.array([1.0, -1.0])
    pace = np.sqrt(game["pace_shared"]) * rng.standard_normal() + np.sqrt(1 - game["pace_shared"]) * rng.standard_normal(2)
    t_sd, c_sd, t_script, c_script = game["targets_sd"], game["carries_sd"], game["script_targets"], game["script_carries"]
    targets = np.array([s.targets for s in sides]) * np.exp(t_sd * pace - t_script * lead - (t_sd ** 2 + t_script ** 2) / 2)
    carries = np.array([s.carries for s in sides]) * np.exp(c_sd * rng.standard_normal(2) + c_script * lead
                                                            - (c_sd ** 2 + c_script ** 2) / 2)
    return eff, rng.poisson(targets), rng.poisson(carries)


def _receiver_lines(side, o: _Offense, n_targets: int) -> dict[int, dict]:
    lines: dict[int, dict] = {}
    for i, pid in enumerate(side.receivers[:-1]):
        lines[pid] = {"targets": int(o.targets[i]), "rec": int(o.catches[i]), "rec_yds": int(o.rec_yds[i]),
                      "rec_td": int(o.rec_td[i]), "fumbles": int(o.rec_fumbles[i]),
                      "share": round(o.targets[i] / n_targets, 3) if n_targets else 0.0}
    for i, pid in enumerate(side.rushers[:-1]):
        line = lines.setdefault(pid, {"share": 0.0})
        line.update(carries=int(o.carries[i]), rush_yds=int(o.rush_yds[i]), rush_td=int(o.rush_td[i]),
                    fumbles=line.get("fumbles", 0) + int(o.rush_fumbles[i]))
    return lines


def _passers(side, o: _Offense, rng: np.random.Generator) -> dict[int, dict]:
    """The QB line(s): the receivers' sums, split with the backup when the starter left hurt."""
    if side.qb is None:
        return {}
    catches, yards, tds = o.catches, o.rec_yds, o.rec_td
    if side.backup is None or side.qb_share >= 1.0:
        mine = (catches, yards, tds)
        shares = {side.qb: 1.0}
    else:
        kept = rng.binomial(catches, side.qb_share)
        kept_yards = np.where(catches > 0, np.round(yards * kept / np.maximum(catches, 1)), 0).astype(int)
        kept_tds = rng.hypergeometric(tds, catches - tds, kept)      # his TDs are among the catches he threw
        mine = (kept, kept_yards, kept_tds)
        shares = {side.qb: side.qb_share, side.backup: 1.0 - side.qb_share}
    out = {}
    for pid, share in shares.items():
        c, y, t = mine if pid == side.qb else (catches - mine[0], yards - mine[1], tds - mine[2])
        out[pid] = {"pass_yds": int(y.sum()), "pass_td": int(t.sum()), "completions": int(c.sum()),
                    "pass_int": int(round(o.ints * share)) if pid == side.qb else o.ints - int(round(o.ints * share)),
                    "fumbles": int(round(o.sack_fumbles * share)) if pid == side.qb
                    else o.sack_fumbles - int(round(o.sack_fumbles * share)), "snap": 100.0 * share}
    return out


def _kicking(o: _Offense, def_tds: int, rng: np.random.Generator) -> tuple[int, dict]:
    """(points scored, kicker counts) for one team."""
    kick = params()["kick"]
    tds = o.tds + def_tds
    two = int(rng.binomial(tds, kick["two_point"]))
    pat_att = tds - two
    pat_made = int(rng.binomial(pat_att, kick["xp"]))
    fga = int(rng.binomial(round(kick["fga_chances"]), min(1.0, fga_mean(o.yards, o.tds) / round(kick["fga_chances"]))))
    bands = rng.multinomial(fga, kick["bands"])
    made = rng.binomial(bands, kick["make"])
    counts = {"made": made, "missed": bands - made, "pat_made": pat_made, "pat_att": pat_att}
    return 6 * tds + pat_made + 2 * int(rng.binomial(two, TWO_POINT_SUCCESS)) + 3 * int(made.sum()), counts


def draw_game(sides: Sequence, rng: np.random.Generator, scoring: ScoringSettings) -> GameResult:
    dst = params()["dst"]
    eff, n_targets, n_carries = _volumes(sides, rng)
    offense = [_offense(side, int(n_targets[i]), int(n_carries[i]), float(eff[i]), rng) for i, side in enumerate(sides)]
    # side i's defense recovers the other offense's lost fumbles plus special-teams ones (muffs, kick returns)
    recovered = [offense[1 - i].fumbles + int(rng.poisson(dst["extra_fumbles"])) for i in range(2)]
    returns = [int(rng.binomial(offense[1 - i].ints, dst["int_td"]) + rng.binomial(recovered[i], dst["fum_td"])
                   + rng.poisson(dst["st_td"])) for i in range(2)]
    safeties = rng.poisson(dst["safety"], 2)
    blocks = rng.poisson(dst["block"], 2)
    scored = [_kicking(offense[i], returns[i], rng) for i in range(2)]
    points = [scored[i][0] + 2 * int(safeties[i]) for i in range(2)]
    if points[0] == points[1]:
        offense, scored, points, n_targets, n_carries = _overtime(sides, offense, scored, points, n_targets,
                                                                  n_carries, rng)
    players: dict[int, PlayerGame] = {}
    for i, side in enumerate(sides):
        o, opp = offense[i], offense[1 - i]
        lines = _receiver_lines(side, o, int(n_targets[i]))
        for pid, passing in _passers(side, o, rng).items():
            line = lines.setdefault(pid, {"share": 0.0})
            line.update({k: v for k, v in passing.items() if k not in ("fumbles", "completions")})
            line["fumbles"] = line.get("fumbles", 0) + passing["fumbles"]
            line["snap"] = passing["snap"]
        for pid, line in lines.items():
            snap = line.pop("snap", side.snaps.get(pid, 0.0))
            fumbles, share = line.pop("fumbles", 0), line.pop("share", 0.0)
            box = BoxLine(**line)
            if not any(line.values()) and snap < MIN_SNAP_TO_RECORD and pid != side.qb:
                continue
            players[pid] = PlayerGame(box, round(_skill_points(box, fumbles, side.positions[pid], scoring), 2),
                                      Usage(round(min(100.0, snap), 1), box.targets, share, box.carries))
        if side.kicker is not None:
            counts = scored[i][1] if side.kicker_share >= 1.0 else _thinned(scored[i][1], side.kicker_share, rng)
            players[side.kicker] = PlayerGame(BoxLine(), float(_kick_points_drawn(counts)), None)
        if side.dst is not None:
            players[side.dst] = PlayerGame(BoxLine(), float(_dst_points(
                opp.sacks, opp.ints, recovered[i], returns[i], int(safeties[i]), int(blocks[i]), points[1 - i])), None)
    return GameResult(players, {side.team: points[i] for i, side in enumerate(sides)})


def _overtime(sides, offense: list, scored: list, points: list, n_targets: np.ndarray, n_carries: np.ndarray,
              rng: np.random.Generator) -> tuple[list, list, list, np.ndarray, np.ndarray]:
    """A tied game's overtime (see the module docstring): the winner's offense or kicker gets the winning play."""
    if rng.random() < OT_TIE:
        return offense, scored, points, n_targets, n_carries
    means = [_side_means(side) for side in sides]
    expect = [max(0.1, _mean_points(means[i], means[1 - i])) for i in range(2)]
    winner = 0 if rng.random() < expect[0] / sum(expect) else 1
    offense, scored, points = list(offense), list(scored), list(points)
    n_targets, n_carries = n_targets.copy(), n_carries.copy()
    if rng.random() < OT_FIELD_GOAL:
        kick = params()["kick"]
        counts = dict(scored[winner][1])
        band = int(rng.choice(len(kick["bands"]), p=np.array(kick["bands"]) / sum(kick["bands"])))
        counts["made"] = counts["made"].copy()
        counts["made"][band] += 1
        scored[winner] = (scored[winner][0] + 3, counts)
        points[winner] += 3
        return offense, scored, points, n_targets, n_carries
    m, o = means[winner], offense[winner]
    rec, rush = m["rec_td"][:-1], m["rush_td"][:-1]           # the "other" bucket's line is not kept
    weights = np.concatenate([rec, rush])
    pick = int(rng.choice(len(weights), p=weights / weights.sum())) if weights.sum() > 0 else len(rec)
    if pick < len(rec):
        one = np.eye(len(o.targets), dtype=int)[pick]
        offense[winner] = replace(o, targets=o.targets + one, catches=o.catches + one,
                                  rec_yds=o.rec_yds + OT_TD_YARDS["rec"] * one, rec_td=o.rec_td + one)
        n_targets[winner] += 1
    else:
        one = np.eye(len(o.carries), dtype=int)[min(pick - len(rec), len(o.carries) - 1)]
        offense[winner] = replace(o, carries=o.carries + one, rush_yds=o.rush_yds + OT_TD_YARDS["rush"] * one,
                                  rush_td=o.rush_td + one)
        n_carries[winner] += 1
    scored[winner] = (scored[winner][0] + 6, scored[winner][1])
    points[winner] += 6
    return offense, scored, points, n_targets, n_carries


def _thinned(counts: dict, share: float, rng: np.random.Generator) -> dict:
    """The kicks a kicker who left hurt took himself (each one his with probability `share`)."""
    made, missed = rng.binomial(counts["made"], share), rng.binomial(counts["missed"], share)
    pat_made = int(rng.binomial(counts["pat_made"], share))
    return {"made": made, "missed": missed, "pat_made": pat_made,
            "pat_att": pat_made + int(rng.binomial(counts["pat_att"] - counts["pat_made"], share))}


def _kick_points_drawn(counts: dict) -> float:
    row = SimpleNamespace(**{f"made_{b}": int(counts["made"][j]) for j, b in enumerate(kdst_model.BANDS)},
                          **{f"missed_{b}": int(counts["missed"][j]) for j, b in enumerate(kdst_model.BANDS)},
                          pat_made=counts["pat_made"], pat_att=counts["pat_att"])
    return kdst_model._actual_k(row)
