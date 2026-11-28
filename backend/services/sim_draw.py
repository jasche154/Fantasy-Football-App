"""Simulated seasons: the random draw of one game (services/sim_game.play; the model is described there).

The game is drawn in parts (services/sim_parts.py; the script in services/sim_script.py): the first half, the second
half up to the closing window's cut, its volume following the score at half time, and the window, a real game's
ending (sim_closing) with its real plays. Counts are integers by construction: a half's plays are Poisson, its
targets binomial among them, both shared out by the side's game plan for the day (who is in it and a small
lognormal game draw on each weight, sim_parts.plan); catches and fumbles are binomial, and yards are whole numbers
per player whose sums ARE the passer's line. A half's touchdowns are drive-limited (`sim_parts._touchdowns`): their
total is Binomial(drives, mean / drives) around the mean its catches and carries give (each one's TD rate), the
game's `td_drives` split between the halves by their share of the plays, so a team's score is less dispersed than
independent plays would make it, as real scores are (a team has about a dozen drives and each ends in one score at
most); the total is then shared out play by play in proportion to each catch's or carry's TD rate. `td_drives` is
tuned with the realism check (sim_eval: the spread of results around the pregame line and of team scores). Per-catch
and per-carry yards are shifted gammas (mean m, standard deviation sd, never below -shift), so a sum over n
plays is one gamma draw: Gamma(n k, theta) - n shift with k = (m + shift)^2 / sd^2, theta = sd^2 / (m + shift).

A side's yards per catch fall with its targets before the cut, x (targets / their mean) ** -`game.ypc_volume` (real
busy passing days are dump-offs: per team game 2021-2025, catches and yards per catch correlate -0.19, and
without the link simulated team receiving yards spread too wide: sd 81 against 72 a game, 500+ yard passing
games 3 a season against 0.6). The tuned 0.3 matches the real spread (sd 72, 99th percentile 428 against 423,
400+ yard passing games 11.7 a season against 10.7) at a stronger link than real (-0.29); it moves mean yards
by under 1%.

Field-goal tries (sim_parts.half) are a binomial over each half's share of fga_chances around its share of the
side's expected tries (sim_game.fga_mean at its expected yards and TDs, x its field-goal scale), moved linearly by
how far the half's own TDs and yards land from their means (the fitted log slopes, so a team that scores fewer TDs
kicks more): linear, so the draw's mean tries are the analytic ones (a log-linear move would kick more on average
than sim_game's mean says).

The closing window (sim_closing.close): the halves are the game up to the last minutes of regulation; the rest is a
real game's ending drawn from the score at that point, its scoring credited to real players (see sim_closing) and
its other plays shared out by the game plan (sim_parts.window).

Overtime (`_overtime`): a game tied after regulation stays tied with sim_closing's measured overtime tie share
(`ot_tie`), else one side wins it (in proportion to the two sides' mean points) with a field goal (`ot_field_goal`
of the time, a made kick in a distance band drawn like any other) or a touchdown (no try: it ends the game),
credited like a closing-window TD (sim_closing.score_td: never to one who left the game hurt, the passer's line
still his receivers' sum); a field goal is the kicker's unless he left hurt (then a fill-in's: points, no kick on
his line). All three rates are measured on 2018-2025 regular-season overtimes (sim_closing_fit: 118 games, 8 of them tied).
"""
from collections.abc import Sequence
from types import SimpleNamespace

import numpy as np

from services import kdst_model, sim_closing, sim_parts, sim_script
from services.replay_box import BoxLine
from services.replay_pool import Usage
from services.scoring import ScoringSettings
from services.sim_game import (
    MIN_SNAP_TO_RECORD, GameResult, PlayerGame, _dst_points, _fga_drawn, _mean_points, _side_means, _skill_points,
)
from services.sim_parts import (  # noqa: F401  (re-exported: the draw's building blocks keep their import path)
    SHARE_CAP, TD_DRIVES, _Offense, _phi, _shares, _touchdowns, _uncapped, _yards,
)
from services.sim_world import params


def ot_tie() -> float:
    """The share of overtimes that end tied (real, sim_closing.json)."""
    return float(sim_closing.ot()["tie"])


def ot_field_goal() -> float:
    """The share of decided overtimes that end on a field goal (real, sim_closing.json)."""
    return float(sim_closing.ot()["field_goal"])


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


def _passers(side, o: _Offense, rng: np.random.Generator, regulation: _Offense | None = None,
             windowed: _Offense | None = None) -> dict[int, dict]:
    """The QB line(s): the receivers' sums, split with the backup when the starter left hurt. A starter who played
    share s of the game threw sim_script.exit_parts(s) of `regulation`'s passes (the halves, else `o`'s) and of the
    window's (`windowed` less `regulation`: none when he left before the cut); the rest, overtime's too, are the
    backup's. Attempts are each passer's completions + interceptions + his share of the other incomplete attempts
    (drawn like his catches: a later TD catch adds an attempt and a catch), so attempts >= completions +
    interceptions for each; sacks are not attempts."""
    if side.qb is None:
        return {}
    catches, yards, tds = o.catches, o.rec_yds, o.rec_td
    early = 1.0
    if side.backup is None or side.qb_share >= 1.0:
        mine = (catches, yards, tds)
        shares = {side.qb: 1.0}
        missed = o.attempts - int(catches.sum()) - o.ints
    else:
        base = regulation or o
        early, late = sim_script.exit_parts(side.qb_share, "targets")
        mine, missed = _thrown(base.catches, base.rec_yds, base.rec_td, base.attempts - int(base.catches.sum()) - base.ints,
                               early, rng)
        if windowed is not None and windowed is not base:
            more, also = _thrown(windowed.catches - base.catches, windowed.rec_yds - base.rec_yds,
                                 windowed.rec_td - base.rec_td, windowed.attempts - base.attempts
                                 - int(windowed.catches.sum() - base.catches.sum()), late, rng)
            mine, missed = tuple(a + b for a, b in zip(mine, more)), missed + also
        shares = {side.qb: side.qb_share, side.backup: 1.0 - side.qb_share}
    ints, sacked = o.ints, o.sack_fumbles                 # interceptions and sacks come from the halves only
    if early < 1.0:                                       # each his with chance `early` (rounding gave him nearly all)
        ints, sacked = int(rng.binomial(o.ints, early)), int(rng.binomial(o.sack_fumbles, early))
    out = {}
    for pid, share in shares.items():
        starter = pid == side.qb
        c, y, t = mine if starter else (catches - mine[0], yards - mine[1], tds - mine[2])
        att = int(c.sum()) + ints + missed if starter else o.attempts - out[side.qb]["pass_att"]   # backup: the rest
        out[pid] = {"pass_cmp": int(c.sum()), "pass_att": att, "pass_yds": int(y.sum()), "pass_td": int(t.sum()),
                    "pass_int": ints if starter else o.ints - ints,
                    "fumbles": sacked if starter else o.sack_fumbles - sacked, "snap": 100.0 * share}
    return out


def _thrown(catches: np.ndarray, yards: np.ndarray, tds: np.ndarray, incomplete: int, q: float,
            rng: np.random.Generator) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray], int]:
    """((catches, yards, TDs) per receiver, other incomplete attempts) the starter threw of these, each with
    chance `q`: his yards in proportion to his catches, his TDs among the catches he threw."""
    kept = rng.binomial(catches, q)
    kept_yards = np.where(catches > 0, np.round(yards * kept / np.maximum(catches, 1)), 0).astype(int)
    kept_tds = rng.hypergeometric(tds, catches - tds, kept)
    return (kept, kept_yards, kept_tds), int(rng.binomial(max(0, incomplete), q))


def _scripted(sides: Sequence) -> Sequence:
    """`sides` as sim_centre centres a two-team game (on their own means) when they have no game script yet."""
    if all(side.end is not None for side in sides):
        return sides
    from services.sim_centre import centred
    done = centred({side.team: side for side in sides})
    return tuple(done[side.team] for side in sides)


def _scored(h: "sim_parts.Half", def_tds: int, rng: np.random.Generator) -> tuple[int, dict]:
    """(points before the cut, kicker counts) of one side: its halves' and its return TDs' with their tries."""
    points, pat_made, pat_att = sim_parts.tries(def_tds, rng)
    return h.points + points, {"made": h.made, "missed": h.missed, "pat_made": h.pat_made + pat_made,
                               "pat_att": h.pat_att + pat_att}


def draw_game(sides: Sequence, rng: np.random.Generator, scoring: ScoringSettings,
              until_cut: bool = False) -> GameResult | tuple[int, int]:
    """One game (see the module docstring); `until_cut`: only the two sides' points at the closing window's cut
    (what sim_closing_fit.cut_spread measures)."""
    dst = params()["dst"]
    sides = _scripted(sides)
    means = [_side_means(side) for side in sides]
    tries = [_fga_drawn(float(m["rec_yds"].sum() + m["rush_yds"].sum()), float(m["rec_td"].sum() + m["rush_td"].sum()),
                        m["fg_scale"]) for m in means]
    halves, plans, eff, busy = sim_parts.before_cut(sides, tries, rng)
    offense = [h.offense for h in halves]
    # side i's defense recovers the other offense's lost fumbles plus special-teams ones (muffs, kick returns)
    extra = [int(rng.poisson(dst["extra_fumbles"])) for _ in range(2)]
    recovered = [offense[1 - i].fumbles + extra[i] for i in range(2)]
    returns = [int(rng.binomial(offense[1 - i].ints, dst["int_td"]) + rng.binomial(recovered[i], dst["fum_td"])
                   + rng.poisson(dst["st_td"])) for i in range(2)]
    safeties = rng.poisson(dst["safety"], 2)
    blocks = rng.poisson(dst["block"], 2)
    scored = [_scored(halves[i], returns[i], rng) for i in range(2)]
    regulation = offense
    if until_cut:
        return scored[0][0] + 2 * int(safeties[0]), scored[1][0] + 2 * int(safeties[1])
    expect = [max(0.1, _mean_points(means[i], means[1 - i])) for i in range(2)]
    cut = [scored[i][0] + 2 * int(safeties[i]) for i in range(2)]
    events = sim_closing.pick(cut, expect, rng, [side.end.edge for side in sides])
    n_targets = np.array([int(o.targets.sum()) for o in offense])
    n_carries = np.array([int(o.carries.sum()) for o in offense])
    offense, scored, points, returns, safeties, n_targets, n_carries = sim_closing.close(
        sides, offense, scored, cut, returns, list(safeties), n_targets, n_carries, rng, means, expect, events)
    offense, n_targets, n_carries = sim_parts.window(sides, offense, events, plans, eff, busy, n_targets, n_carries, rng)
    windowed = offense
    recovered = [offense[1 - i].fumbles + extra[i] for i in range(2)]     # the window's fumbles count for the D/ST
    if points[0] == points[1]:
        offense, scored, points, n_targets, n_carries = _overtime(sides, offense, scored, points, n_targets,
                                                                  n_carries, rng, means, expect)
    players: dict[int, PlayerGame] = {}
    for i, side in enumerate(sides):
        o, opp = offense[i], offense[1 - i]
        lines = _receiver_lines(side, o, int(n_targets[i]))
        for pid, passing in _passers(side, o, rng, regulation[i], windowed[i]).items():
            line = lines.setdefault(pid, {"share": 0.0})
            line.update({k: v for k, v in passing.items() if k != "fumbles"})
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
              rng: np.random.Generator, means: list, expect: list) -> tuple[list, list, list, np.ndarray, np.ndarray]:
    """A tied game's overtime (see the module docstring): the winner's offense or kicker gets the winning play.
    `means`: each side's sim_game._side_means; `expect`: each side's mean points (who wins it)."""
    if rng.random() < ot_tie():
        return offense, scored, points, n_targets, n_carries
    winner = 0 if rng.random() < expect[0] / sum(expect) else 1
    offense, scored, points = list(offense), list(scored), list(points)
    n_targets, n_carries = n_targets.copy(), n_carries.copy()
    if rng.random() < ot_field_goal():
        kick = params()["kick"]
        counts = dict(scored[winner][1])
        band = int(rng.choice(len(kick["bands"]), p=np.array(kick["bands"]) / sum(kick["bands"])))
        if sides[winner].kicker_share >= 1.0:            # a kicker who left hurt: a fill-in kicked it
            counts["made"] = counts["made"].copy()
            counts["made"][band] += 1
        scored[winner] = (scored[winner][0] + 3, counts)
        points[winner] += 3
        return offense, scored, points, n_targets, n_carries
    offense[winner], extra_target, extra_carry = sim_closing.score_td(
        sides[winner], means[winner], offense[winner], None, sim_closing.ot()["td_yards"], rng)
    n_targets[winner] += extra_target
    n_carries[winner] += extra_carry
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
