"""Simulated seasons: one NFL game, top-down so the box score adds up exactly.

For each team (a `_Side` built from a World, who is out, and who leaves the game hurt):

1. Who dresses: every QB, K and D/ST, and at RB / WR / TE the top ACTIVE_SLOTS healthy players by role
   weight (deeper players are inactive, as real ones are). The starting QB (and K) is the active one with
   the highest `starts` weight.
2. Environment. A shared game draw g and a draw per team e make each offense's efficiency
   eff = exp(sd_eff x (sqrt(rho) g + sqrt(1 - rho) e)) (opposing offenses share shootouts). The leader by
   efficiency runs more and the trailer passes more (game script, `script_*`); team targets and carries are
   Poisson around the team's volume times those draws.
3. Targets go to the dressed receivers (RB/WR/TE + an "other" bucket for players outside the pool, which also
   takes whatever a thin roster leaves short of a full team, `_other`) by a multinomial around their role
   weights. Carries likewise, except the starting QB keeps his share of a full-strength backfield
   (`_carry_weights`), so a hurt back's carries go to the next back. A player who leaves hurt keeps
   exit_share of his weight; the rest flows to teammates.
4. Per receiver: catches ~ Binomial(targets, catch rate); yards ~ a shifted gamma per catch around his yards
   per catch x QB quality / the team's usual passer x the opposing pass defense x eff; TDs ~ Binomial(catches,
   TD rate x the same). Rushing the same way per carry.
5. The QB's line IS the sum: passing yards and TDs are the receivers' (the "other" bucket included, its own
   line discarded). If the starter leaves hurt, each receiver's catches are split binomially between him and
   the backup, yards in proportion, TDs among the catches each threw. INTs and sacks come per attempt.
6. Kicker: XP attempts = the team's TDs (some go for two), FG attempts ~ a binomial over `fga_chances` whose
   mean rises with the team's yards and falls with its TDs (drives that stall), by distance band; a kicker who
   leaves hurt keeps each kick with probability exit_share (a fill-in takes the rest). D/ST: its
   sacks and INTs ARE the opposing offense's, its fumble recoveries are the opponent's lost fumbles plus
   special-teams ones, return TDs ride on them; points allowed = the opponent's points.
7. Points come from services/scoring.py (skill) and kdst_model's formulas (K, D/ST), so custom league
   scoring works. `expected` gives the analytic means of the same model (game noise at its mean).
8. Centred on a prediction (services/sim_centre.py): given each side's wanted mean points (from the game's
   line, sim_lines), its TD rates and field-goal tries are multiplied by one factor and its yards by a power of
   it. `expected` and `play` apply the same factor, so projections stay unbiased for the draw.
9. A side built from a World with no in-game exits is cached per World (`_side_of`): a week's line, projections,
   expert values and tags rebuild the same belief sides many times.
"""
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace

import numpy as np

from services import kdst_model
from services.replay_box import BoxLine
from services.replay_pool import Usage
from services.scoring import ScoringSettings, fantasy_points
from services.sim_world import OTHER, World, params

SNAP_TOP = {"QB": 100.0, "RB": 70.0, "WR": 92.0, "TE": 85.0}   # the lead player's snap share at a position
SNAP_EXPONENT = 0.5              # snaps fall with the square root of his weight vs the lead player's
MIN_SNAP_TO_RECORD = 20.0        # an active player with no stat is listed as playing above this snap share
MAX_TD_RATE = 0.6
FULL_STRENGTH = 1.0              # role weights of a team at full strength sum to about this
ACTIVE_SLOTS = {"RB": 3, "WR": 5, "TE": 3}     # game-day actives at a position (the top healthy by role weight)
PA_GRID = np.arange(0, 80)       # points allowed values for the D/ST expectation
TWO_POINT_SUCCESS = 0.48
SIDE_CACHE = 4096                # cached sides (`_side_of`); the cache is emptied when it is full
_sides: dict[tuple, tuple] = {}


@dataclass(frozen=True)
class _Side:
    team: str
    receivers: tuple[int, ...]          # OTHER last
    target_w: np.ndarray
    catch: np.ndarray
    ypc: np.ndarray                     # with QB, defense applied (eff not yet)
    rec_td: np.ndarray
    rushers: tuple[int, ...]            # OTHER last
    carry_w: np.ndarray
    ypr: np.ndarray
    rush_td: np.ndarray
    fumble: np.ndarray                  # lost fumbles per touch, per rusher (receivers use their own below)
    rec_fumble: np.ndarray
    targets: float
    carries: float
    qb: int | None
    backup: int | None
    qb_share: float                     # share of the game the starter plays
    int_rate: float
    sack_rate: float
    kicker: int | None
    kicker_share: float                 # share of the game the kicker plays (the rest of his kicks go to a fill-in)
    dst: int | None
    positions: Mapping[int, str] = field(default_factory=dict)
    snaps: Mapping[int, float] = field(default_factory=dict)
    fg_scale: float = 1.0               # field-goal tries x this (the centring factor, sim_centre)
    exited: frozenset[int] = frozenset()   # players who leave the game hurt (no overtime score)


@dataclass(frozen=True)
class PlayerGame:
    box: BoxLine
    points: float
    usage: Usage | None                 # skill players only


@dataclass(frozen=True)
class GameResult:
    players: Mapping[int, PlayerGame]   # everyone who played (K and D/ST included)
    points: Mapping[str, int]           # team -> points scored


@dataclass(frozen=True)
class Expected:
    box: BoxLine                        # mean line (floats)
    points: float


def _norm(position: str) -> Mapping:
    return params()["positions"][position]


def _starter(players, position: str):
    ranked = sorted((p for p in players if p.position == position), key=lambda p: (-p.starts, p.player_id))
    return ranked[0] if ranked else None, ranked[1] if len(ranked) > 1 else None


def _side(world: World, team: str, opponent: str | None, out: Collection[int],
          exits: Mapping[int, float]) -> _Side:
    league, other = params()["league"], _norm("other")
    form = world.teams[team]
    defense = world.teams.get(opponent) if opponent else None
    pass_def, rush_def = (defense.pass_def, defense.rush_def) if defense else (1.0, 1.0)
    takeaways = defense.takeaways if defense else 1.0
    roster = _dressed([p for p in world.roster(team) if p.player_id not in out])
    qb, backup = _starter(roster, "QB")
    kicker, _ = _starter(roster, "K")
    qb_share = exits.get(qb.player_id, 1.0) if qb else 1.0
    quality = ((qb.quality * qb_share + (backup.quality if backup else qb.quality) * (1 - qb_share))
               if qb else league.get("backup_quality", 0.9))
    pass_mult = quality / max(form.passer, 1e-6) * pass_def
    receivers = [p for p in roster if p.position in ("RB", "WR", "TE") and p.targets > 0]
    rushers = [p for p in roster if p.position in ("RB", "WR", "TE") and p.carries > 0]
    if qb is not None:
        rushers.append(qb)
    positions = {p.player_id: p.position for p in roster}
    snaps = _snaps(roster, qb, exits)
    known = _sides.get(("other", id(world), team))        # a world's full roster never changes: once per team
    if known is None or known[0] is not world:
        known = (world, _other(world, team, form))
        _sides[("other", id(world), team)] = known
    other_targets, other_carries = known[1]
    return _Side(
        team=team,
        receivers=(*(p.player_id for p in receivers), OTHER),
        target_w=np.array([*_weights(receivers, "targets", exits), other_targets]),
        catch=np.array([*(p.catch for p in receivers), other["catch"]]),
        ypc=np.array([*(p.ypc for p in receivers), other["ypc"]]) * pass_mult,
        rec_td=np.array([*(p.rec_td for p in receivers), other["rec_td"]]) * pass_mult,
        rushers=(*(p.player_id for p in rushers), OTHER),
        carry_w=_carry_weights(rushers, qb, other_carries, exits),
        ypr=np.array([*(p.ypr for p in rushers), other["ypr"]]) * rush_def,
        rush_td=np.array([*(p.rush_td for p in rushers), other["rush_td"]]) * rush_def,
        fumble=np.array([*(_norm(p.position)["fumble"] for p in rushers), other["fumble"]]),
        rec_fumble=np.array([*(_norm(p.position)["fumble"] for p in receivers), other["fumble"]]),
        targets=form.targets, carries=form.carries,
        qb=qb.player_id if qb else None, backup=backup.player_id if backup else None, qb_share=qb_share,
        int_rate=(qb.int_rate if qb else league["int_rate"]) * takeaways,
        sack_rate=league["sack_rate"] * takeaways ** 0.5,
        kicker=kicker.player_id if kicker else None,
        kicker_share=exits.get(kicker.player_id, 1.0) if kicker else 1.0,
        dst=next((p.player_id for p in roster if p.position == "DST"), None),
        positions=positions, snaps=snaps, exited=frozenset(pid for pid, share in exits.items() if share < 1.0))


def _side_of(world: World, team: str, opponent: str | None, out: Collection[int]) -> _Side:
    """`_side` with no exits, cached per World object (it is immutable; the entry keeps it alive, so its id is
    not reused while cached)."""
    ids = _sides.get((id(world), team))
    if ids is None or ids[0] is not world:
        ids = (world, frozenset(p.player_id for p in world.roster(team)))
        _sides[(id(world), team)] = ids
    key = (id(world), team, opponent, ids[1].intersection(out))      # only his team's players out matter
    hit = _sides.get(key)
    if hit is not None and hit[0] is world:
        return hit[1]
    if len(_sides) >= SIDE_CACHE:
        _sides.clear()
    side = _side(world, team, opponent, out, {})
    _sides[key] = (world, side)
    return side


def _dressed(healthy: list) -> list:
    """The game-day actives: at RB, WR and TE the top ACTIVE_SLOTS healthy players by role weight (the rest
    are inactive, as real depth players are); every QB, K and D/ST."""
    out = [p for p in healthy if p.position not in ACTIVE_SLOTS]
    for position, slots in ACTIVE_SLOTS.items():
        group = sorted((p for p in healthy if p.position == position),
                       key=lambda p: (-(p.targets + p.carries), p.player_id))
        out += group[:slots]
    return out


def _other(world: World, team: str, form) -> tuple[float, float]:
    """(target, carry) weights of the team's "other" bucket: its own, or if larger a depth player's share (the
    position's depth_targets / depth_carries) for every dressed slot its full-strength roster cannot fill (a
    thin pool, e.g. a new season's ADP players only). The larger, not the sum: a belief fitted on simulated
    games has already seen that slack in its own "other" weight. Counting slots, not 1 - the weights, keeps
    the bucket from feeding on a drift in the weights' scale. Injuries do not count here, so a hurt player's
    work still flows to his teammates."""
    full = _dressed(world.roster(team))
    targets = carries = 0.0
    for position, slots in ACTIVE_SLOTS.items():
        empty = slots - sum(1 for p in full if p.position == position)
        targets += empty * _norm(position)["depth_targets"]
        carries += empty * _norm(position)["depth_carries"]
    return max(form.other_targets, targets), max(form.other_carries, carries)


def _weights(group, attr: str, exits: Mapping[int, float]) -> list[float]:
    return [getattr(p, attr) * exits.get(p.player_id, 1.0) for p in group]


def _carry_weights(rushers, qb, other: float, exits: Mapping[int, float]) -> np.ndarray:
    """Carry weights in `rushers` order (the QB last) plus OTHER. The QB keeps his share of a full-strength
    backfield (his weight over max(FULL_STRENGTH, all active weights)); the backs and receivers who are active
    split the rest, so an injured back's carries go to the next back, not to the QB."""
    backs = np.array([*_weights([p for p in rushers if p is not qb], "carries", exits), other])
    if qb is None:
        return backs
    mine = qb.carries * exits.get(qb.player_id, 1.0)
    share = mine / max(FULL_STRENGTH, mine + backs.sum())
    scaled = backs / backs.sum() * (1 - share)
    return np.array([*scaled[:-1], share, scaled[-1]])


def _snaps(roster, qb, exits: Mapping[int, float]) -> dict[int, float]:
    out = {}
    for position in ("RB", "WR", "TE"):
        group = [p for p in roster if p.position == position]
        top = max((p.targets + p.carries for p in group), default=0.0)
        for p in group:
            relative = (p.targets + p.carries) / top if top > 0 else 0.0
            out[p.player_id] = SNAP_TOP[position] * relative ** SNAP_EXPONENT * exits.get(p.player_id, 1.0)
    if qb is not None:
        out[qb.player_id] = SNAP_TOP["QB"] * exits.get(qb.player_id, 1.0)
    return out


# ---------------- scoring ----------------

def _skill_points(box: BoxLine, fumbles: int, position: str, scoring: ScoringSettings) -> float:
    return fantasy_points({"passing_yards": box.pass_yds, "passing_tds": box.pass_td,
                           "passing_interceptions": box.pass_int, "rushing_yards": box.rush_yds,
                           "rushing_tds": box.rush_td, "receptions": box.rec, "receiving_yards": box.rec_yds,
                           "receiving_tds": box.rec_td, "rushing_fumbles_lost": fumbles}, position, scoring)


def _kick_points(fga: float, xp: float) -> float:
    kick = params()["kick"]
    made = {band: fga * share * rate for band, share, rate in zip(kdst_model.BANDS, kick["bands"], kick["make"])}
    missed = {band: fga * share * (1 - rate) for band, share, rate in zip(kdst_model.BANDS, kick["bands"], kick["make"])}
    return kdst_model._actual_k(SimpleNamespace(**{f"made_{b}": made[b] for b in kdst_model.BANDS},
                                                **{f"missed_{b}": missed[b] for b in kdst_model.BANDS},
                                                pat_made=xp * kick["xp"], pat_att=xp))


def _dst_points(sacks, ints, fumbles, tds, safeties, blocks, allowed) -> float:
    return kdst_model._actual_dst(SimpleNamespace(def_sacks=sacks, def_ints=ints, def_fumble_rec=fumbles,
                                                  def_tds=tds, def_safeties=safeties, def_blocks=blocks,
                                                  points_against=allowed))


@lru_cache(maxsize=1)
def _pa_points() -> np.ndarray:
    """D/ST points for each points-allowed value on PA_GRID."""
    return np.array([kdst_model.pa_points(pa, kdst_model.DEFAULT_DST) for pa in PA_GRID])


def fga_mean(yards: float, tds: float) -> float:
    kick, league = params()["kick"], params()["league"]
    return float(np.exp(kick["fga_log"] + kick["fga_yards"] * np.log(max(yards, 1.0) / league["yards"])
                        + kick["fga_tds"] * (tds - league["tds"])))


def _fga_drawn(yards: float, tds: float, scale: float = 1.0) -> float:
    """Mean field-goal attempts as sim_draw draws them (a binomial over fga_chances, so capped there); `scale`:
    the side's field-goal scale (sim_centre)."""
    return min(scale * fga_mean(yards, tds), float(round(params()["kick"]["fga_chances"])))


def _takeaways(against: dict | None) -> tuple[float, float, float]:
    """(sacks, INTs, fumbles recovered) a defense makes against offense means `against` (None: league average)."""
    league, extra = params()["league"], params()["dst"]["extra_fumbles"]
    if against is None:
        return league["sack_rate"] * league["targets"], league["int_rate"] * league["targets"], league["fumbles"] + extra
    return against["sacks"], against["ints"], against["fumbles"] + extra


def _mean_points(m: dict, against: dict | None) -> float:
    """A team's mean points as sim_draw scores them: offensive and return TDs with their tries, field goals at
    the real make rates, safeties (`against`: the opposing offense's means, None for a league average)."""
    kick, dst = params()["kick"], params()["dst"]
    _, ints, fumbles = _takeaways(against)
    tds = float(m["rec_td"].sum() + m["rush_td"].sum())
    scored = tds + ints * dst["int_td"] + fumbles * dst["fum_td"] + dst["st_td"]
    per_td = 6 + (1 - kick["two_point"]) * kick["xp"] + kick["two_point"] * 2 * TWO_POINT_SUCCESS
    make = float(np.dot(kick["bands"], kick["make"]))
    yards = float(m["rec_yds"].sum() + m["rush_yds"].sum())
    return scored * per_td + 3 * make * _fga_drawn(yards, tds, m.get("fg_scale", 1.0)) + 2 * dst["safety"]


# ---------------- the analytic mean ----------------

def _side_means(side: _Side) -> dict:
    targets = side.targets * side.target_w / side.target_w.sum()
    catches = targets * side.catch
    rec_yds, rec_td = catches * side.ypc, catches * np.minimum(side.rec_td, MAX_TD_RATE)
    carries = side.carries * side.carry_w / side.carry_w.sum()
    rush_yds, rush_td = carries * side.ypr, carries * np.minimum(side.rush_td, MAX_TD_RATE)
    attempts = side.targets * params()["league"]["attempts_per_target"]
    return {"targets": targets, "catches": catches, "rec_yds": rec_yds, "rec_td": rec_td, "carries": carries,
            "rush_yds": rush_yds, "rush_td": rush_td, "ints": attempts * side.int_rate,
            "sacks": attempts * side.sack_rate,
            "fumbles": float((carries * side.fumble).sum() + (catches * side.rec_fumble).sum()
                             + attempts * side.sack_rate * params()["league"]["sack_fumble"]),   # sack fumbles too
            "fg_scale": side.fg_scale}


def expected(world: World, home: str, away: str | None, out: Collection[int], scoring: ScoringSettings,
             exits: Mapping[int, float] | None = None, target: Mapping[str, float] | None = None,
             shift: Mapping[str, float] | None = None, factors: Mapping[str, float] | None = None) -> dict[int, Expected]:
    """Mean line and points of every active player of `home` (and of `away` when given) in their game.
    `away` None: `home` against a league-average opponent (only home's players are returned). `target`:
    {team: mean points} the game is centred on (see the module docstring); `shift`: {team: points} added to
    its own mean instead; `factors`: {team: factor} to apply as given (sim_centre.factor)."""
    from services.sim_centre import centred
    exits = exits or {}
    teams = [home] if away is None else [home, away]
    built = {team: _side(world, team, (away if team == home else home), out, exits) if exits
             else _side_of(world, team, (away if team == home else home), out) for team in teams}
    from services import sim_closing
    from services.sim_centre import ot_points
    sides = centred(built, target, shift, factors)
    means = {team: _side_means(side) for team, side in sides.items()}
    dst = params()["dst"]
    league = params()["league"]
    other = {home: away, away: home}
    before = {team: _mean_points(m, means.get(other[team])) for team, m in means.items()}
    lead = {team: before[team] - before[other[team]] if other[team] in before else 0.0 for team in sides}   # at the cut
    closing = {team: away is not None and not (factors and team in factors) for team in sides}   # the draw adds the window
    team_points = {team: before[team] + (sim_closing.window_points(lead[team]) + ot_points() if closing[team] else 0.0)
                   for team in means}
    out_lines: dict[int, Expected] = {}
    for team, side in sides.items():
        m, opp = means[team], (away if team == home else home)
        out_lines.update(_expected_players(side, m, scoring, lead[team] if closing[team] else None))
        if side.kicker is not None:
            tds = float(m["rec_td"].sum() + m["rush_td"].sum())
            yards = float(m["rec_yds"].sum() + m["rush_yds"].sum())
            kicks = _kick_points(_fga_drawn(yards, tds, side.fg_scale), tds)
            out_lines[side.kicker] = Expected(BoxLine(), round(
                kicks + (sim_closing.kicker_points(lead[team]) if closing[team] else 0.0), 2))
        if side.dst is not None:
            allowed = team_points.get(opp, league["points"])
            sacks, ints, fumbles = _takeaways(means.get(opp))
            tds = ints * dst["int_td"] + fumbles * dst["fum_td"] + dst["st_td"]
            allowed = min(max(allowed, 0.0), float(PA_GRID[-1]))
            grid = np.exp(-0.5 * ((PA_GRID - allowed) / league["points_sd"]) ** 2)
            pa = float(grid @ _pa_points() / grid.sum())
            points = _dst_points(sacks, ints, fumbles, tds, dst["safety"], dst["block"], 1000) - _dst_points(
                0, 0, 0, 0, 0, 0, 1000) + pa + (sim_closing.defense_points(lead[team]) if closing[team] else 0.0)
            out_lines[side.dst] = Expected(BoxLine(), round(points, 2))
    return out_lines


def _expected_players(side: _Side, m: dict, scoring: ScoringSettings, lead: float | None = None) -> dict[int, Expected]:
    """Every skill player's mean line and points; `lead`: with the closing window's mean events at that expected
    lead at the cut (sim_closing), None without."""
    lines: dict[int, dict[str, float]] = {}
    for i, pid in enumerate(side.receivers[:-1]):
        lines[pid] = {"targets": m["targets"][i], "rec": m["catches"][i], "rec_yds": m["rec_yds"][i],
                      "rec_td": m["rec_td"][i], "fumbles": m["catches"][i] * side.rec_fumble[i]}
    for i, pid in enumerate(side.rushers[:-1]):
        line = lines.setdefault(pid, {})
        line.update(carries=m["carries"][i], rush_yds=m["rush_yds"][i], rush_td=m["rush_td"][i],
                    fumbles=line.get("fumbles", 0.0) + m["carries"][i] * side.fumble[i])
    if side.qb is not None:
        line = lines.setdefault(side.qb, {})
        line.update(pass_yds=float(m["rec_yds"].sum()), pass_td=float(m["rec_td"].sum()), pass_int=m["ints"])
    if lead is not None:
        from services.sim_closing import credit_means
        credit_means(lines, side, m, lead)
    out = {}
    for pid, line in lines.items():
        fumbles = line.pop("fumbles", 0.0)
        box = BoxLine(**{key: float(value) for key, value in line.items()})
        out[pid] = Expected(box, round(_skill_points(box, fumbles, side.positions[pid], scoring), 2))
    return out


def on_field(world: World, team: str, out: Collection[int]) -> dict[int, float]:
    """{player: expected snap share 0-100} of the team's active QB starter, kicker and skill players."""
    side = _side_of(world, team, None, out)
    snaps = dict(side.snaps)
    if side.kicker is not None:
        snaps[side.kicker] = 100.0
    return snaps


def play(world: World, home: str, away: str, out: Collection[int], rng: np.random.Generator,
         scoring: ScoringSettings, exits: Mapping[int, float] | None = None,
         target: Mapping[str, float] | None = None, shift: Mapping[str, float] | None = None) -> GameResult:
    """One random draw of the game (see the module docstring); `target`: {team: mean points} it is centred on,
    or `shift`: {team: points} added to each side's own mean points under `world`."""
    from services.sim_centre import centred
    from services.sim_draw import draw_game      # the random half (split out for size)
    exits = exits or {}
    sides = centred({home: _side(world, home, away, out, exits), away: _side(world, away, home, out, exits)},
                    target, shift)
    return draw_game((sides[home], sides[away]), rng, scoring)
