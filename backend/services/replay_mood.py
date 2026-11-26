"""Replay League: how CPU managers feel about their players, and the worried ones shopping a player to the user.

Feelings come from the same numbers `replay_personas.private_value` prices with, so what a manager says never
contradicts what he accepts:

- signal = form_chart (his recent real games) + hype x shock_chart (an opportunity shock, `Team.hype` for an
  emotional manager), each side scaled by the persona's EMOTION: a panicky manager feels a slump hard, a
  gambler a hot streak, a steady one shrugs bad weeks off (and adds the signed STEADY_STAR x star x
  (pre_chart - chart) his price carries: high on a slumping star, cooler on one who outran draft day).
- strength = signal / max(chart, CHART_FLOOR): worried at <= -WORRY, high at >= HIGH, and only on the side of
  the chart his own price is on (`private_value - chart`: worried only at or below it, high only at or above),
  so the feeling and the price can never point different ways. Only his own healthy skill players worth
  FEEL_MIN_CHART or more: an injured player is never something to be "worried" about, nor one who has not had
  SLUMP_GAMES games in a row below his rate, nor one his team got this week (`acquired`).
- `mood_line`: "Worried about X. High on Y." from his strongest feeling each way (None without one). Views
  show it once `moods_known` (MOOD_WEEKS NFL weeks of results before the current week: week 4 on, so a league
  that jumped in at week 4 or later has it from its first week: its earlier real weeks are known);
  the persona's name stays hidden. A tendency (replay_personas) describes his behaviour in this league and
  still waits for league weeks.

Shopping (`refresh_shops`, once a week at its start, after the CPUs' IR and free-agency moves): a CPU worried
at >= SHOP_WORRY about one of his SHOP_TOP most valuable skill players, whose last SLUMP_GAMES games fell short of his rate
and who nobody could think was hurt (`might_be_hurt`, on last week's game), shops him to the user with
probability SHOP_RATE (panicky most; steady and shark never). At most one shop per CPU and MAX_SHOPS in the
league. A shop holds a seeded `discount` (DISCOUNT_BASE by persona + worry, +- jitter, within
DISCOUNT_RANGE) that `replay_trade_value.tally` takes off his price for the user only, via
`replay_state.markdowns`. While it is open the player is the user's to buy: no other CPU may trade for him
(replay_trade_ai). It stays open week to week while he stays that worried and the player is on his active
roster and doesn't look hurt; a good game or the deadline closes it, and a trade, drop or IR move removes it
at once (`replay_state.with_team`). He sends the user his shop offer for a player once per shop (`Shop.opened`,
kept when it renews; replay_trade_ai).
Nothing here posts chat: replay_game does, with replay_chat (which also uses `might_be_hurt` for bust lines).
Pure: a Knowledge and the state in, new data out.
"""
import statistics
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace

from services import replay_fans, replay_personas, replay_state as rs
from services.replay_pool import SKILL, SeasonData
from services.replay_state import Settings, Shop, State, Team
from services.replay_values import Knowledge, ReplayValue

FEEL_MIN_CHART = 150.0          # a player worth less than this isn't worth a feeling
CHART_FLOOR = 250.0             # strength divides by at least this, so a cheap player's wobble stays small
WORRY = 0.08                    # strength at or below -WORRY: worried
HIGH = 0.08                     # at or above HIGH: high on him
EMOTION = {                     # persona -> (multiple on a bad signal, on a good one)
    "balanced": (1.0, 1.0),
    "panicky": (1.8, 1.0),
    "shark": (1.0, 1.0),
    "steady": (0.3, 1.0),
    "gambler": (1.0, 1.8),
    # the newer styles feel like balanced (a tilted manager on a skid feels like panicky: replay_personas.now)
    "fan": (1.0, 1.0), "stasher": (1.0, 1.0), "haggler": (1.0, 1.0), "contender": (1.0, 1.0),
    "dealer": (1.0, 1.0), "tilted": (1.0, 1.0),
}
SLUMP_GAMES = 2                 # a worried feeling or a shop needs this many games in a row below his rate
SHOP_WORRY = 0.15               # worry that makes him shop a player
SHOP_TOP = 6                    # only one of his SHOP_TOP most valuable skill players
SHOP_RATE = {"balanced": 0.25, "panicky": 0.75, "gambler": 0.35, "steady": 0.0, "shark": 0.0,     # the user's
             "fan": 0.25, "stasher": 0.2, "haggler": 0.3, "contender": 0.15, "dealer": 0.4, "tilted": 0.4}
MAX_SHOPS = 2                   # open in the whole league at once
DISCOUNT_BASE = {"balanced": 0.06, "panicky": 0.09, "gambler": 0.07,
                 "fan": 0.06, "stasher": 0.06, "haggler": 0.05, "contender": 0.06, "dealer": 0.07, "tilted": 0.09}
DISCOUNT_PER_WORRY = 0.20       # extra discount per unit of worry beyond SHOP_WORRY
DISCOUNT_JITTER = 0.02
DISCOUNT_RANGE = (0.05, 0.15)
MOOD_WEEKS = 3                  # NFL weeks of results known before a mood shows or a shop opens
LEFT_EARLY = 0.6                # a snap share under this x his season median: he may have left the game hurt


@dataclass(frozen=True)
class Feeling:
    player_id: int
    kind: str                   # "worried" | "high"
    strength: float             # signed, as above


def strength(value: ReplayValue, persona: str, hype: float = 1.0) -> float:
    """How a manager with this persona feels about the player: signed, a share of his chart."""
    down, up = EMOTION.get(persona, EMOTION["balanced"])
    signal = value.form_chart + hype * value.shock_chart
    signal *= down if signal < 0 else up
    if persona == "steady":
        signal += replay_personas.STEADY_STAR * value.star * (value.pre_chart - value.chart)
    return signal / max(value.chart, CHART_FLOOR)


def feeling(value: ReplayValue, persona: str, hype: float = 1.0) -> Feeling | None:
    """His feeling about one player, or None when he feels nothing much (or the player is hurt or cheap)."""
    if value.position not in SKILL or value.out or value.injury is not None or value.healthy_chart < FEEL_MIN_CHART:
        return None
    felt = strength(value, persona, hype)
    lean = replay_personas.private_value(value, persona, hype) - value.chart     # the side of the chart he prices on
    kind = ("worried" if felt <= -WORRY and lean <= 0 and _slumping(value)
            else "high" if felt >= HIGH and lean >= 0 else None)
    return None if kind is None else Feeling(value.player_id, kind, round(felt, 3))


def feelings(know: Knowledge, team: Team, persona: str | None = None,
             fresh: Collection[int] = ()) -> tuple[Feeling, ...]:
    """A CPU's feelings about his own roster, strongest first. `persona`: the one his prices follow now
    (replay_personas.now: a tilted manager on a skid feels like panicky), default his own. Never worried about
    his own club's player or one in `fresh` (just acquired: `acquired`)."""
    found = [felt for pid in team.roster if pid in know.by_id
             if (felt := feeling(know.by_id[pid], persona or team.persona, team.hype)) is not None
             and not (felt.kind == "worried" and (replay_fans.cheers_for(team, know.by_id[pid].team) or pid in fresh))]
    return tuple(sorted(found, key=lambda f: (-abs(f.strength), f.player_id)))


def acquired(state: State) -> frozenset[int]:
    """Players their team traded for or added this week: nobody is worried about a player he just got."""
    return frozenset(pid for pid, week in state.traded.items() if week == state.week) | rs.pickups(state)


def mood_line(know: Knowledge, team: Team, persona: str | None = None, fresh: Collection[int] = ()) -> str | None:
    """"Worried about X. High on Y." from his strongest feeling each way; None when he has none."""
    felt = feelings(know, team, persona, fresh)
    worried = next((f for f in felt if f.kind == "worried"), None)
    high = next((f for f in felt if f.kind == "high"), None)
    parts = [f"{label} {know.by_id[f.player_id].name}." for label, f in (("Worried about", worried),
                                                                         ("High on", high)) if f is not None]
    return rs.tidy(" ".join(parts)) or None


def moods_known(week: int) -> bool:
    """Are enough played NFL weeks known before `week` for moods and shops? Results of weeks 1..week - 1 are."""
    return week - 1 >= MOOD_WEEKS


def might_be_hurt(know: Knowledge, played: SeasonData, player_id: int, week: int) -> bool:
    """Could he have been hurt in `week`'s game, as far as anyone knows once it is played? A tag for that week
    or the next, no projection next week (not a bye), or a snap share under LEFT_EARLY x his season median. No
    snap count to tell (or no earlier game to compare with) is a maybe too: then nobody mocks or shops him. A
    bye that week is no game to get hurt in: unless he carries a tag, his last game before it is judged instead
    (a game cut short before a bye still counts)."""
    value = know.by_id[player_id]
    if value.out or value.injury is not None or (player_id, week) in played.injuries:
        return True
    if played.on_bye(player_id, week):
        last = next((w for w in range(week - 1, 0, -1) if not played.on_bye(player_id, w)), None)
        return last is not None and might_be_hurt(know, played, player_id, last)
    game = played.usage.get((player_id, week))
    earlier = [u.snap_pct for w in range(1, week) if (u := played.usage.get((player_id, w))) and u.snap_pct]
    if game is None or game.snap_pct is None or not earlier:
        return True
    return game.snap_pct < LEFT_EARLY * statistics.median(earlier)


# ---------------- shopping a player to the user ----------------

def _slumping(value: ReplayValue) -> bool:
    """His last SLUMP_GAMES games all fell short of his expected rate: a real slump, never one game (Justin
    Jefferson scoring 16-23 a week was shopped as "I age a year every time I check his stats"; a good game
    closes a shop)."""
    last = value.recent_points[:SLUMP_GAMES]
    return len(last) == SLUMP_GAMES and all(points < value.ros_ppg for points in last)


def _worry(state: State, value: ReplayValue, team: Team, hurt: Callable[[int], bool]) -> float | None:
    """His worry (> 0) when this manager would shop him now: worried enough, slumping, not trade locked and not
    maybe hurt (a manager never frets out loud about a player who may be injured), and not a fan's own team's
    player (he never shops those); else None."""
    if replay_fans.cheers_for(team, value.team):
        return None
    felt = feeling(value, replay_personas.now(state, team), team.hype)
    if felt is None or felt.strength > -SHOP_WORRY or not _slumping(value) or rs.retrade_locked(state, value.player_id):
        return None
    return None if hurt(value.player_id) else -felt.strength


def _shop_pick(state: State, know: Knowledge, team: Team, hurt: Callable[[int], bool]) -> tuple[int, float] | None:
    """(player id, worry) he would shop: his most worrying slumping player among his SHOP_TOP, if any."""
    top = sorted((know.by_id[pid] for pid in team.roster if pid in know.by_id and know.by_id[pid].position in SKILL),
                 key=lambda v: (-v.chart, v.player_id))[:SHOP_TOP]
    worries = [(-worry, value.player_id) for value in top if (worry := _worry(state, value, team, hurt)) is not None]
    return None if not worries else (min(worries)[1], -min(worries)[0])


def discount(seed: int, week: int, team: Team, worry: float) -> float:
    """The share off his price for the user, seeded so a reload never re-rolls it."""
    jitter = rs.rng(seed, "shop-discount", week, team.id).uniform(-DISCOUNT_JITTER, DISCOUNT_JITTER)
    raw = DISCOUNT_BASE.get(team.persona, DISCOUNT_RANGE[0]) + DISCOUNT_PER_WORRY * (worry - SHOP_WORRY) + jitter
    return round(min(max(raw, DISCOUNT_RANGE[0]), DISCOUNT_RANGE[1]), 3)


def _still_open(state: State, know: Knowledge, shop: Shop, hurt: Callable[[int], bool]) -> bool:
    team = rs.team(state, shop.team_id)
    value = know.by_id.get(shop.player_id)
    return value is not None and shop.player_id in team.roster and _worry(state, value, team, hurt) is not None


def refresh_shops(state: State, settings: Settings, know: Knowledge, seed: int,
                  played: SeasonData) -> tuple[State, tuple[Shop, ...]]:
    """(the state with this week's shops, the ones newly opened). Closes the shops that no longer hold, renews
    the rest for `state.week`, then lets worried CPUs (in seeded order, each rolling SHOP_RATE) open new ones
    up to MAX_SHOPS. No shops before `moods_known(state.week)` or after the trade deadline week (from then on no
    CPU sends the user an offer). `played` is the
    season as known now (data.as_of(state.week)): `might_be_hurt` reads last week's game (state.week - 1)."""
    trading = state.phase in ("waivers", "open") and state.week <= settings.trade_deadline_week
    if not trading or not moods_known(state.week):
        return replace(state, shops=()), ()

    def hurt(player_id: int) -> bool:
        return might_be_hurt(know, played, player_id, state.week - 1)

    kept = tuple(replace(shop, week=state.week) for shop in state.shops if _still_open(state, know, shop, hurt))
    shopping = {shop.team_id for shop in kept}
    cpus: Sequence[Team] = [team for team in state.teams if not team.is_user and team.id not in shopping]
    order = rs.rng(seed, "shops", state.week).permutation(len(cpus))
    opened: list[Shop] = []
    for index in order:
        if len(kept) + len(opened) >= MAX_SHOPS:
            break
        team = cpus[int(index)]
        if rs.rng(seed, "shop", state.week, team.id).random() >= SHOP_RATE.get(team.persona, 0.0):
            continue
        pick = _shop_pick(state, know, team, hurt)
        if pick is not None:
            opened.append(Shop(team.id, pick[0], state.week, discount(seed, state.week, team, pick[1]), state.week))
    return replace(state, shops=(*kept, *opened)), tuple(opened)
