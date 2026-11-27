"""Simulated seasons: one game drawn in parts, the plays before the cut and the window's (sim_draw.draw_game; the
script is described in sim_script, the rest of the game in sim_draw).

Per side, once a game: its efficiency (a shared game draw and its own, as before), its lean (a normal of mean 0
added to its pass share, `mix_sd`, measured by sim_script_fit; `game.mix_sd` overrides it for sim_eval --set; there
is no pace draw: plays spread like a Poisson's within a team-season) and its game plan (`plan`: who is in it,
sim_script.plan_weights, then each weight x a lognormal game draw, renormalized, the starting QB's carries on
their own). Then:

1. First half: its plays ~ Poisson(its mean targets + carries), targets ~ Binomial(plays, its mean pass
   share + its lean), the rest carries, both shared out by the game plan; catches, yards, TDs (drive-limited,
   `_touchdowns`, over the half's part of the game's drives, `drives_before_cut`, the count drawn at a quantile that
   leans on how far the half's yards beat their mean, `game.td_yards_rho`), interceptions and sacks per play,
   field goals around the half's share of the game's mean tries, moved by its own TDs and yards as much per TD and per
   yard as a whole game's are (linear, so the mean is exact).
2. Second half to the cut: the same around sim_script's second-half volume at the real margin at half time.
3. Window (after sim_closing.close picks the real ending): its non-scoring plays (sim_script.window_plays) shared
   out by the same game plan, kneels to the starting QB, at the same yards per play; no TD, interception or sack.

A busy passing day gets shorter catches: every catch's yards x (the side's targets before the cut / their mean) **
-`game.ypc_volume` (sim_draw's docstring has the measurement), applied after the halves' field goals are drawn (they
read the yards before it, whose mean is exact).

A half's TDs come with its yards: `game.td_yards_rho` (tuned 0.9 with sim_eval: a real team-game's yards and TD
points correlate 0.54 within team-seasons, 0.61 pooled, 2021-2025 from week 12; 0.27 / 0.31 with the TD count drawn
apart from the yards, 0.53 / 0.57 at 0.85, 0.58 / 0.61 at 0.95). The TD count keeps its Binomial, so no mean or
spread moves; team fantasy points spread 19.5 -> 21.1 a team-game against a real 22.2.
"""
import math
from dataclasses import dataclass, replace

import numpy as np

from services import sim_script
from services.sim_game import MAX_TD_RATE, TWO_POINT_SUCCESS
from services.sim_world import params

SHARE_CAP = 0.95                 # a player drawn on his own (the QB's carries) takes at most this share
TD_DRIVES = 5.0                  # default of the tuned `game.td_drives`


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

    def __add__(self, other: "_Offense") -> "_Offense":
        return _Offense(*(a + b for a, b in zip(vars(self).values(), vars(other).values())))


@dataclass(frozen=True)
class Plan:
    """One side's shares of its targets and carries in one game (receivers' / rushers' order, OTHER last)."""
    targets: np.ndarray
    carries: np.ndarray


# ---------------- the building blocks ----------------

def _shares(weights: np.ndarray, sd: float, rng: np.random.Generator, alone: tuple[int, float] | None = None) -> np.ndarray:
    """Shares around `weights` for one game: each weight x a lognormal game draw of mean 1 (relative noise, so a
    small role stays small), renormalized. `alone` (index, sd): that player's share is drawn on its own around his
    weight's share, with a wider spread (the starting QB's carries), and the others split the rest by their weights
    x (1 + their noise - its weighted mean), which sums to 1 and averages each weight exactly: renormalizing a wide
    draw with the narrow ones biased his share (with no mean correction +2.5% at 8 of 27 carries; with it, after
    renormalizing, -3.6%), and renormalizing the backs' noise lifted the smallest by about 2%."""
    z = rng.standard_normal(len(weights))
    if alone is None:
        drawn = weights * np.exp(sd * z - sd ** 2 / 2)
        return drawn / drawn.sum()
    index, wide = alone
    share = weights[index] / weights.sum()
    mine = min(SHARE_CAP, _uncapped(share, wide) * np.exp(wide * z[index] - wide ** 2 / 2))
    rest = np.delete(weights, index)
    if rest.sum() <= 0:
        return np.eye(len(weights))[index]
    rest = rest / rest.sum()
    noise = np.exp(sd * np.delete(z, index) - sd ** 2 / 2)
    others = np.maximum(rest * (1.0 + noise - rest @ noise), 0.0)      # re-centred, not renormalized: mean exact
    return np.insert(others / others.sum() * (1.0 - mine), index, mine)


def _uncapped(share: float, sd: float, steps: int = 3) -> float:
    """The base share whose lognormal draw, capped at SHARE_CAP, still averages `share`."""
    if share <= 0:
        return 0.0
    if sd <= 0:                                      # no spread: the draw is the share itself
        return share
    base = share
    for _ in range(steps):
        a = (math.log(SHARE_CAP / base) - sd ** 2 / 2) / sd
        capped = base * _phi(a) + SHARE_CAP * (1 - _phi(a + sd))
        base *= share / capped
    return base


def _phi(v: float) -> float:
    return 0.5 * (1 + math.erf(v / math.sqrt(2)))


def _yards(plays: np.ndarray, mean: np.ndarray, sd: np.ndarray, shift: float, rng: np.random.Generator) -> np.ndarray:
    """Whole yards over `plays` per player, each play a shifted gamma (mean, sd)."""
    return _yards_scored(plays, mean, sd, shift, rng, scored=False)[0]


def _yards_scored(plays: np.ndarray, mean: np.ndarray, sd: np.ndarray, shift: float,
                  rng: np.random.Generator, scored: bool = True) -> tuple[np.ndarray, float, float]:
    """`_yards` plus (sum of each player's yards sd x his normal score, sum of their variances): the normal score of
    a gamma draw by Wilson-Hilferty's cube root, close to exact from shape 1 (a player's yards over a few plays)."""
    out = np.zeros(len(plays), dtype=int)
    some = plays > 0
    if not some.any():
        return out, 0.0, 0.0
    base = np.maximum(mean[some] + shift, 0.5)
    k, theta = base ** 2 / sd[some] ** 2, sd[some] ** 2 / base
    shape = plays[some] * k
    drawn = rng.gamma(shape, theta)
    out[some] = np.round(drawn - plays[some] * shift).astype(int)
    if not scored:
        return out, 0.0, 0.0
    c = 1.0 / (9.0 * shape)
    score = ((drawn / (shape * theta)) ** (1.0 / 3.0) - (1.0 - c)) / np.sqrt(c)
    spread = np.sqrt(shape) * theta
    return out, float(spread @ score), float(spread @ spread)


def _binomial_at(u: float, n: int, p: float) -> int:
    """The Binomial(n, p) count at quantile `u` (its inverse distribution function)."""
    if p >= 1.0:
        return n
    q, k = 1.0 - p, 0
    pmf = q ** n
    cdf = pmf
    while cdf < u and k < n:
        pmf *= (n - k) / (k + 1) * p / q
        k += 1
        cdf += pmf
    return k


def _touchdowns(catches: np.ndarray, rec_rate: np.ndarray, carries: np.ndarray, rush_rate: np.ndarray,
                drives: float, rng: np.random.Generator, slack: int = 1,
                yards: float | None = None, rho: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(receiving TDs, rushing TDs) per player: the total drive-limited, Binomial(drives, mean / drives) around the
    mean its catches and carries give (at least the mean + `slack` drives), then shared out play by play in
    proportion to each one's TD rate. A player never scores more TDs than he has catches or carries. With `yards`
    (the plays' yards as a standard normal score given the plays, `_yards_scored`) the count is the binomial's at
    the quantile Phi(rho x yards + sqrt(1 - rho^2) x a normal): the same Binomial, so every mean and spread is
    kept, but a half that gains more yards than its plays' mean scores more of its TDs, as a real one does."""
    plays = np.concatenate([catches, carries]).astype(int)
    rates = np.concatenate([rec_rate, rush_rate])
    mean = float((plays * rates).sum())
    tds = np.zeros(len(plays), dtype=int)
    if mean <= 0:
        return tds[:len(catches)], tds[len(catches):]
    n = max(int(round(drives)), int(np.ceil(mean)) + slack)
    if yards is None:
        total = min(int(rng.binomial(n, mean / n)), int(plays.sum()))
    else:
        u = _phi(rho * yards + math.sqrt(1.0 - rho * rho) * float(rng.standard_normal()))
        total = min(_binomial_at(u, n, mean / n), int(plays.sum()))
    while total > 0:                         # shared out in proportion; a player past his plays gives the rest back
        weights = (plays - tds) * rates
        if weights.sum() <= 0:
            break
        tds += rng.multinomial(total, weights / weights.sum())
        over = np.maximum(tds - plays, 0)
        tds -= over
        total = int(over.sum())
    return tds[:len(catches)], tds[len(catches):]


# ---------------- once a game ----------------

def environment(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """(efficiency, lean) per side: efficiency lognormal of mean 1, partly shared by the two sides; lean a normal of
    mean 0 (`spread.mix_sd`, or `game.mix_sd` for sim_eval --set) added to the pass share of both halves (a
    pass-first or run-first plan). No pace draw: a team's plays spread like a Poisson's within a team-season
    (sim_script.json's `spread.plays_sd`, measured 0)."""
    game = params()["game"]
    rho, sd = game["eff_shared"], game["eff_sd"]
    z = np.sqrt(rho) * rng.standard_normal() + np.sqrt(1 - rho) * rng.standard_normal(2)
    lean = game.get("mix_sd", sim_script.stored()["spread"]["mix_sd"]) * rng.standard_normal(2)
    return np.exp(sd * z - sd ** 2 / 2), lean


def plan(side, rng: np.random.Generator) -> Plan:
    """The side's game plan (see the module docstring): who is in it (`plan_inputs`, from its weights before anyone
    left the game), then each weight x a lognormal game draw, the starting QB's carries on their own."""
    game = params()["game"]
    inputs = plan_inputs(side)
    drawn = rng.random(len(inputs.q)) < inputs.q
    t = _planned(side.target_w, inputs.receivers, inputs.q, drawn)
    c = _planned(sim_script.carry_weights(side), inputs.rushers, inputs.q, drawn)
    qb = (side.rushers.index(side.qb), game["share_sd_qb_carries"]) if side.qb is not None else None
    return Plan(_shares(t, game["share_sd_targets"], rng), _shares(c, game["share_sd_carries"], rng, qb))


def plan_inputs(side) -> "sim_script.PlanInputs":
    """Who may sit out of the side's game plan, worked out once per side from its weights before any exit."""
    def work(s) -> sim_script.PlanInputs:
        target_w, carry_w = s.whole if s.whole is not None else (s.target_w, s.carry_w)
        return sim_script.plan_inputs(s.receivers, target_w, s.rushers, carry_w, s.targets, s.carries, s.positions)
    return sim_script.structural(side, "plan", work)


def _planned(weights: np.ndarray, index: np.ndarray, q: np.ndarray, drawn: np.ndarray) -> np.ndarray:
    keep = index < 0                                       # OTHER and the QBs keep theirs
    at = np.where(keep, 0, index)
    return sim_script.plan_weights(weights, np.where(keep, 1.0, q[at]), np.where(keep, True, drawn[at]), keep)


# ---------------- the plays ----------------

def plays(side, plan: Plan, n_targets: int, n_carries: int, eff: float, rng: np.random.Generator,
          drives: float | None = None, kneels: int = 0, busy: float = 1.0) -> _Offense:
    """`n_targets` and `n_carries` shared out by `plan` with catches, yards (x `busy` per catch) and fumbles; with
    `drives`: TDs (drive-limited over them), interceptions and sacks at the per-play rates / the halves' share
    (sim_script.before_cut); without: none (the window's non-scoring plays) and `kneels` to the QB who finishes
    the game (sim_script.kneeler)."""
    game, league, rates = params()["game"], params()["league"], params()["rates"]
    targets = rng.multinomial(n_targets, plan.targets)
    catches = rng.binomial(targets, np.clip(side.catch, 0, 1))
    ypc = side.ypc * eff * busy
    rec_yds, rec_score, rec_var = _yards_scored(catches, ypc, rates["rec_cv"] * np.maximum(ypc, 1.0),
                                                rates["rec_shift"], rng, drives is not None)
    carries = rng.multinomial(n_carries, plan.carries)
    rush_eff = eff ** game["rush_power"]
    ypr, rush_td = sim_script.rushing(side)
    rush_yds, rush_score, rush_var = _yards_scored(carries, ypr * rush_eff, np.full(len(ypr), rates["rush_sd"]),
                                                   rates["rush_shift"], rng, drives is not None)
    rec_fumbles, rush_fumbles = rng.binomial(catches, side.rec_fumble), rng.binomial(carries, side.fumble)
    attempts = int(n_targets + rng.binomial(n_targets, max(0.0, league["attempts_per_target"] - 1)))
    zero = np.zeros(len(catches), dtype=int), np.zeros(len(carries), dtype=int)
    if drives is None:
        if kneels:
            at = side.rushers.index(sim_script.kneeler(side))
            carries, rush_yds = carries.copy(), rush_yds.copy()
            carries[at] += kneels
            rush_yds[at] += int(round(kneels * sim_script.kneel_yards()))
        return _Offense(targets, catches, rec_yds, zero[0], rec_fumbles, carries, rush_yds, zero[1], rush_fumbles,
                        attempts, 0, 0, 0)
    p_t, p_c = sim_script.scoring_share(side)
    rec_rate = np.minimum(side.rec_td * eff ** game["td_power"] / p_t, MAX_TD_RATE)
    rush_rate = np.minimum(rush_td * rush_eff ** game["td_power"] / p_c, MAX_TD_RATE)
    spread = rec_var + rush_var
    score = (rec_score + rush_score) / math.sqrt(spread) if spread > 0 else 0.0
    rec_td, rush_td = _touchdowns(catches, rec_rate, carries, rush_rate, drives, rng, slack=0, yards=score,
                                  rho=game.get("td_yards_rho", 0.0))
    sacks = int(rng.binomial(attempts, min(0.5, side.sack_rate / p_t)))
    return _Offense(targets, catches, rec_yds, rec_td, rec_fumbles, carries, rush_yds, rush_td, rush_fumbles,
                    attempts, 0, sacks, int(rng.binomial(sacks, league["sack_fumble"])))


def interceptions(side, o: _Offense, eff: float, rng: np.random.Generator) -> int:
    """The interceptions of `o`'s attempts (the halves' together): an interception is an incomplete attempt, drawn
    among them at the rate that keeps rate x attempts its mean (drawn per half, a half with every attempt complete
    lost its share: 0.7% of the mean)."""
    game = params()["game"]
    missed = o.attempts - int(o.catches.sum())
    rate = min(0.5, side.int_rate / eff ** game["int_power"] / sim_script.scoring_share(side)[0])
    return int(rng.binomial(missed, min(1.0, rate * o.attempts / max(missed, 1))))


# ---------------- the halves ----------------

@dataclass(frozen=True)
class Half:
    offense: _Offense
    points: int                  # TDs with their tries and field goals (return TDs and safeties come later)
    made: np.ndarray             # field goals by distance band
    missed: np.ndarray
    pat_made: int
    pat_att: int


def _per_play(side) -> np.ndarray:
    """[yards per target, TDs per target, yards per carry, TDs per carry] before the cut, at mean efficiency."""
    t_share, c, _, _ = sim_script.shares(side)
    t = t_share * side.catch
    p_t, p_c = sim_script.scoring_share(side)
    ypr, rush_td = sim_script.rushing(side)
    return np.array([t @ side.ypc, t @ np.minimum(side.rec_td / p_t, MAX_TD_RATE),
                     c @ ypr, c @ np.minimum(rush_td / p_c, MAX_TD_RATE)])


def per_td() -> float:
    """Points a TD brings with its try, on average."""
    kick = params()["kick"]
    return 6 + (1 - kick["two_point"]) * kick["xp"] + kick["two_point"] * 2 * TWO_POINT_SUCCESS


def tries(tds: int, rng: np.random.Generator) -> tuple[int, int, int]:
    """(points, extra points made, extra points tried) of `tds` touchdowns with their tries."""
    kick = params()["kick"]
    two = int(rng.binomial(tds, kick["two_point"]))
    pat_att = tds - two
    pat_made = int(rng.binomial(pat_att, kick["xp"]))
    return 6 * tds + pat_made + 2 * int(rng.binomial(two, TWO_POINT_SUCCESS)), pat_made, pat_att


def half(side, plan: Plan, volume: np.ndarray, share: float, eff: float, fg_tries: float,
         rng: np.random.Generator, lean: float = 0.0, drives: int | None = None, per: np.ndarray | None = None) -> Half:
    """One half: `volume` [mean targets, carries], `share` its share of the plays before the cut (and so of its mean
    field-goal tries `fg_tries`), `lean` added to its pass share, `drives` its share of the game's TD drives
    (`drives_before_cut`; default the half's share of `game.td_drives`), `per` its `_per_play` when known."""
    game, kick = params()["game"], params()["kick"]
    mean = float(volume.sum())
    n = int(rng.poisson(mean))
    n_targets = int(rng.binomial(n, min(1.0, max(0.0, volume[0] / mean + lean)))) if mean > 0 else 0
    drives = game.get("td_drives", TD_DRIVES) * share if drives is None else drives
    o = plays(side, plan, n_targets, n - n_targets, eff, rng, drives=drives)
    per = _per_play(side) if per is None else per
    mean_tds = volume[0] * per[1] + volume[1] * per[3]
    mean_yards = max(1.0, volume[0] * per[0] + volume[1] * per[2])
    # the game's tries move by the same amount per TD and per yard whichever half they come in (sim_draw's rule for a
    # whole game, split): a half's own base would move them by its share of it, and points spread 10% more
    move = share + kick["fga_tds"] * (o.tds - mean_tds) + kick["fga_yards"] * (o.yards - mean_yards) * share / mean_yards
    chances = max(1, round(kick["fga_chances"] * share))
    fga = int(rng.binomial(chances, min(1.0, fg_tries * max(0.0, move) / chances)))
    bands = rng.multinomial(fga, kick["bands"])
    made = rng.binomial(bands, kick["make"])
    points, pat_made, pat_att = tries(o.tds, rng)
    return Half(o, points + 3 * int(made.sum()), made, bands - made, pat_made, pat_att)


def mean_points(side, volume: np.ndarray, share: float, fg_tries: float, per: np.ndarray | None = None) -> float:
    """A half's mean points (`half`'s arguments)."""
    kick = params()["kick"]
    per = _per_play(side) if per is None else per
    tds = volume[0] * per[1] + volume[1] * per[3]
    return tds * per_td() + 3 * float(np.dot(kick["bands"], kick["make"])) * fg_tries * share


def drives_before_cut(side, volumes: np.ndarray, share: float, per: np.ndarray | None = None) -> tuple[int, int]:
    """The side's TD drives in each half: the game's `game.td_drives`, or its mean TDs before the cut + 1 when more
    (as one drive-limited draw of the whole game had), split by the first half's share of the plays, each half at
    least its own mean TDs (so a half's binomial chance stays a chance). Splitting first keeps the TD count's spread
    a whole game's: a mean + 1 floor in each half gave a strong offense 7 drives, not 5."""
    per = _per_play(side) if per is None else per
    means = volumes[:, 0] * per[1] + volumes[:, 1] * per[3]
    total = max(int(round(params()["game"].get("td_drives", TD_DRIVES))), int(np.ceil(means.sum())) + 1)
    first = max(int(round(total * share)), int(np.ceil(means[0])))
    return first, max(total - first, int(np.ceil(means[1])))


def _second_volume(side, lead: float) -> np.ndarray:
    shares = sim_script.stored()["shares"]
    return np.array([side.targets * shares["targets"][1] * sim_script.second_half("targets", side.margin, lead),
                     side.carries * shares["carries"][1] * sim_script.second_half("carries", side.margin, lead)])


def before_cut(sides, fg_tries: list[float], rng: np.random.Generator) -> tuple[list[Half], list[Plan], np.ndarray, np.ndarray]:
    """Both halves of both sides up to the cut, the second following the score at half time (sim_script): (each
    side's halves summed into one Half, its game plan, efficiency, busy-day factor). `fg_tries`: each side's mean
    field-goal tries in a game (sim_game._fga_drawn at its means). The lead at half time is the side's mean lead
    (`half_lead`, what its projections use) plus how far each side's first half beat its own mean, so the second
    half's volume averages the projected one."""
    eff, lean = environment(rng)
    plans = [plan(side, rng) for side in sides]
    volumes = [sim_script.parts(side) for side in sides]
    share = [float(v[0].sum() / v.sum()) if v.sum() > 0 else 0.5 for v in volumes]
    per = [_per_play(side) for side in sides]
    drives = [drives_before_cut(side, volumes[i], share[i], per[i]) for i, side in enumerate(sides)]
    first = [half(side, plans[i], volumes[i][0], share[i], eff[i], fg_tries[i], rng, lean[i], drives[i][0], per[i])
             for i, side in enumerate(sides)]
    surprise = [first[i].points - mean_points(side, volumes[i][0], share[i], fg_tries[i], per[i])
                for i, side in enumerate(sides)]
    out, busy = [], np.ones(2)
    for i, side in enumerate(sides):
        volume = _second_volume(side, side.half_lead + surprise[i] - surprise[1 - i])
        second = half(side, plans[i], volume, 1.0 - share[i], eff[i], fg_tries[i], rng, lean[i], drives[i][1],
                      per[i])
        o = first[i].offense + second.offense
        o = replace(o, ints=interceptions(side, o, float(eff[i]), rng))
        busy[i] = (max(int(o.targets.sum()), 1) / max(float(volumes[i][:, 0].sum()), 1.0)) \
            ** -params()["game"].get("ypc_volume", 0.0)
        o = replace(o, rec_yds=np.round(o.rec_yds * busy[i]).astype(int))
        out.append(Half(o, first[i].points + second.points, first[i].made + second.made,
                        first[i].missed + second.missed, first[i].pat_made + second.pat_made,
                        first[i].pat_att + second.pat_att))
    return out, plans, eff, busy


def window(sides, offense: list[_Offense], events: list, plans: list[Plan], eff: np.ndarray, busy: np.ndarray,
           n_targets: np.ndarray, n_carries: np.ndarray, rng: np.random.Generator) -> tuple[list, np.ndarray, np.ndarray]:
    """Each side's window plays besides its scoring ones (`events`: the real ending sim_closing.close gave each
    side), added to its offense and its team counts: shared by its game plan without the players who left the game
    hurt (the window is the end of it), its kneels the finishing QB's."""
    offense, n_targets, n_carries = list(offense), n_targets.copy(), n_carries.copy()
    for i, side in enumerate(sides):
        targets, carries, kneels = sim_script.window_plays(side, events[i], rng)
        kneels = kneels if sim_script.kneeler(side) in side.rushers else 0
        _, _, t_win, c_win = sim_script.shares(side)
        late = Plan(sim_script.window_split(plans[i].targets, t_win, side.receivers, side.exited),
                    sim_script.window_split(plans[i].carries, c_win, side.rushers, side.exited))
        targets = targets if late.targets.sum() > 0 else 0
        carries = carries if late.carries.sum() > 0 else 0
        offense[i] = offense[i] + plays(side, late, targets, carries, float(eff[i]), rng, kneels=kneels,
                                        busy=float(busy[i]))
        n_targets[i] += targets
        n_carries[i] += carries + kneels
    return offense, n_targets, n_carries
