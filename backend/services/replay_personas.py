"""Replay League CPU personas: each CPU manager trades with a style, assigned once from the game's seed.

`private_value` is what a persona thinks a player is worth (the chart, nudged, clamped to +-PRIVATE_CLAMP);
`offer_rate` / `counter_rate` how often it offers and counters; `tendency` a hint shown to the user. What a
style does with its own lineup, standings or favourite team is priced in replay_styles (`Mind`).

- balanced: the chart.
- panicky: marks a player down after bad games (0.8 x a negative form_chart): sells low.
- steady: trusts draft day (0.35 x star x (pre_chart - chart)): won't sell a slumping star cheap.
- gambler: chases hot streaks (0.5 x a positive form_chart) and pays more for the best player in a deal.
- shark: the chart, but wants a SHARK_MARGIN edge; hunts buy-low stars and offers the user the most.
- fan: a favourite NFL team (replay_fans): its players FAN_BONUS more, its rival's never taken.
- stasher: an out player with a return date loses only STASH_SHARE of the market's injury discount
  (chart + STASH_SHARE x (healthy_chart - chart), outside the clamp): pays the most for hurt stars, and goes
  after the user's (replay_trade_ai).
- haggler: his opening offer to the user is a lowball (OFFER_EDGE below 0: the user gets 10-15% less than he
  sends; the user's choice, the one exception to the 5-10% rule); declined, he always counters at
  HAGGLE_COUNTER_EDGE (replay_counter.haggle_counter).
- contender: while he holds a playoff spot from league week CONTEND_FROM, starters count more and bench depth
  and out players less (replay_styles); otherwise balanced.
- dealer: offers most weeks (OFFER_RATE 0.5), counters often, favours 2-for-2 swaps and makes extra CPU-CPU
  attempts (replay_trade_ai).
- tilted: balanced until he loses TILT_LOSSES league games in a row; then he prices like panicky
  (`pricing_persona`, which his moods follow too), offers more often (TILT_OFFER_RATE) and his own offers give
  more with every loss (TILT_EDGE, never above 15%).
Offers: when a CPU offers the user a deal for a player it wants, it gives more value than it gets, by a share
in its persona's `offer_edge` range (the user's call: careful managers nearer 5%, desperate or aggressive
ones nearer 10%).
Emotion: panicky and gambler managers are emotional (`hype_for`, stored once as Team.hype): they price an
opportunity shock (replay_shocks, ReplayValue.shock_chart) at HYPE x its calibrated move, at most HYPE_CAP
chart points beyond it, both when buying and when holding. Calm managers (and old saves) use 1.0.
Every number for the six newer styles is the user's choice (ranges from the brief; the value picked is noted).
Pure.
"""
from collections.abc import Sequence

from services import replay_fans, replay_state as rs
from services.replay_state import State, Team
from services.replay_values import ReplayValue

PERSONAS = ("balanced", "panicky", "shark", "steady", "gambler",
            "fan", "stasher", "haggler", "contender", "dealer", "tilted")
MAX_PER_STYLE = 2              # no style on more than two CPUs in a league
PRIVATE_CLAMP = 0.15           # private value stays within +-15% of the chart
TENDENCY_WEEKS = 3             # no tendency shown before this many weeks played
PANIC_FORM = 0.8
STEADY_STAR = 0.35
GAMBLER_FORM = 0.5
STASH_SHARE = 0.5              # a stasher takes off half the market's injury discount (the user's "about half")
HYPE = 1.25                    # an emotional manager's multiple on a shock's chart move (calm: 1.0)
HYPE_CAP = 8.0                 # at most this many chart points beyond the calibrated move
EMOTIONAL = ("panicky", "gambler")
TILT_LOSSES = 2                # straight league losses before a tilted manager tilts
TILT_OFFER_RATE = 0.25         # his offer rate while tilted
TILT_EDGE = {2: (0.08, 0.10), 3: (0.10, 0.13), 4: (0.12, 0.15)}     # by straight losses (4 = four or more)
OFFER_RATE = {"balanced": 0.08, "panicky": 0.12, "shark": 0.30, "steady": 0.05, "gambler": 0.12,
              "fan": 0.08, "stasher": 0.15, "haggler": 0.15, "contender": 0.10, "dealer": 0.50, "tilted": 0.08}
OFFER_EDGE = {                 # (least, most) extra value a CPU gives in its own offer to the user
    "steady": (0.05, 0.06), "shark": (0.05, 0.07), "balanced": (0.06, 0.08),
    "gambler": (0.08, 0.10), "panicky": (0.08, 0.10),
    "fan": (0.06, 0.08), "stasher": (0.06, 0.08), "contender": (0.06, 0.08), "dealer": (0.05, 0.07),
    "tilted": (0.06, 0.08), "haggler": (-0.15, -0.10),     # the haggler's opening lowball: 10-15% less
}
# A CPU's own offer to the user (want or shop) favours him on the chart with this chance, seeded per offer; else it
# is close: CLOSE_EDGE (up to 4% short). The user's choice: "something like 50/50", generous styles more, sharks less;
# measured over 3 seasons x 6 seeds: 51% of non-haggler offers favour the user (shops 56%, want offers 45%).
FAVOUR_SHARE = {"panicky": 0.65, "gambler": 0.65, "tilted": 0.65, "shark": 0.4}
FAVOUR_DEFAULT = 0.55
CLOSE_EDGE = (-0.04, 0.0)
SHOP_EDGE = (0.0, 0.10)                 # a shop offer that favours the user: up to 10% more back
HAGGLE_COUNTER_EDGE = (0.05, 0.07)      # the haggler's counter (and any counter of his): his normal edge
COUNTER_RATE = {"balanced": 0.5, "panicky": 0.4, "shark": 0.8, "steady": 0.3, "gambler": 0.6,
                "fan": 0.5, "stasher": 0.5, "haggler": 0.9, "contender": 0.5, "dealer": 0.9, "tilted": 0.4}
TENDENCY = {
    "balanced": "Trades on fair value",
    "panicky": "Sells after a bad week",
    "shark": "Hunts for buy-low stars",
    "steady": "Slow to give up on stars",
    "gambler": "Chases hot streaks",
    "stasher": "Buys injured stars",
    "haggler": "Opens low, then meets you",
    "contender": "Pays up to win now",
    "dealer": "Always looking to deal",
    "tilted": "Gets reckless after losses",
}


def assign(seed: int, cpu_ids: Sequence[int]) -> dict[int, str]:
    """{cpu team id: persona}: at least one shark and one panicky when there are two or more CPUs, and no style
    on more than MAX_PER_STYLE CPUs (while the league has room: 11 styles cover 22 CPUs)."""
    dice = rs.rng(seed, "personas")
    order = [cpu_ids[int(i)] for i in dice.permutation(len(cpu_ids))]
    picked: dict[int, str] = dict(zip(order, ("shark", "panicky")))
    for team_id in order[len(picked):]:
        used = list(picked.values())
        room = [p for p in PERSONAS if used.count(p) < MAX_PER_STYLE] or list(PERSONAS)
        picked[team_id] = room[int(dice.integers(len(room)))]
    return {team_id: picked[team_id] for team_id in cpu_ids}


def hype_for(persona: str) -> float:
    """The shock multiple a CPU with this persona keeps for the season (Team.hype)."""
    return HYPE if persona in EMOTIONAL else 1.0


def hyped(value: ReplayValue, hype: float) -> float:
    """Signed chart points an emotional manager adds to a shocked player beyond the calibrated move."""
    extra = (hype - 1.0) * value.shock_chart
    return min(max(extra, -HYPE_CAP), HYPE_CAP)


def stashable(value: ReplayValue) -> bool:
    """Out with a return week in the news: a stasher's kind of player."""
    return value.out and value.news is not None and value.news.back is not None


def private_value(value: ReplayValue, persona: str, hype: float = 1.0) -> float:
    chart = value.chart
    if persona == "panicky":
        nudged = chart + PANIC_FORM * min(0.0, value.form_chart)
    elif persona == "steady":
        nudged = chart + STEADY_STAR * value.star * (value.pre_chart - chart)
    elif persona == "gambler":
        nudged = chart + GAMBLER_FORM * max(0.0, value.form_chart)
    else:
        nudged = chart
    clamped = min(max(nudged, chart * (1 - PRIVATE_CLAMP)), chart * (1 + PRIVATE_CLAMP))
    stash = STASH_SHARE * max(0.0, value.healthy_chart - chart) if persona == "stasher" and stashable(value) else 0.0
    return clamped + stash + hyped(value, hype)


def pricing_persona(persona: str, losses: int = 0) -> str:
    """The persona his prices (and moods) follow now: a tilted manager on a TILT_LOSSES skid prices like panicky."""
    return "panicky" if persona == "tilted" and losses >= TILT_LOSSES else persona


def now(state: State, team: Team) -> str:
    """`pricing_persona` for a team in this state."""
    return pricing_persona(team.persona, rs.losses_in_a_row(state, team.id))


def offer_rate(persona: str, losses: int = 0) -> float:
    if persona == "tilted" and losses >= TILT_LOSSES:
        return TILT_OFFER_RATE
    return OFFER_RATE.get(persona, OFFER_RATE["balanced"])


def offer_edge(persona: str, losses: int = 0, counter: bool = False) -> tuple[float, float]:
    """(least, most) extra share his own offer gives the user. `counter`: an offer that answers another (the
    haggler then gives his normal edge); `losses`: a tilted manager's straight losses."""
    if persona == "haggler" and counter:
        return HAGGLE_COUNTER_EDGE
    if persona == "tilted" and losses >= TILT_LOSSES:
        return TILT_EDGE[min(losses, max(TILT_EDGE))]
    return OFFER_EDGE.get(persona, OFFER_EDGE["balanced"])


def edge(state: State, team: Team, counter: bool = False) -> tuple[float, float]:
    """`offer_edge` for a team in this state."""
    return offer_edge(team.persona, rs.losses_in_a_row(state, team.id), counter)


def favours(seed: int, week: int, team: Team) -> bool:
    """Does this week's offer from `team` favour the user (FAVOUR_SHARE by persona), or only come close?"""
    return rs.rng(seed, "offer-lean", week, team.id).random() < FAVOUR_SHARE.get(team.persona, FAVOUR_DEFAULT)


def counter_rate(persona: str) -> float:
    return COUNTER_RATE.get(persona, COUNTER_RATE["balanced"])


def tendency(persona: str, weeks_played: int, fan_of: str | None = None) -> str | None:
    """A one-line hint about how this manager trades; None before TENDENCY_WEEKS weeks."""
    if weeks_played < TENDENCY_WEEKS:
        return None
    if persona == "fan" and fan_of:
        hated = replay_fans.RIVALS.get(fan_of)
        return f"{replay_fans.nickname(fan_of)} fan. Wants no {replay_fans.nickname(hated)}."
    return TENDENCY.get(persona, TENDENCY["balanced"])
