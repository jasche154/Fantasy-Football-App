"""Replay League: what a CPU's trading style makes of one deal, beyond `replay_personas.private_value`.

`mind_for` reads the state once per side of a deal and returns a `Mind`; `price` is then what that CPU thinks a
player is worth in it (replay_trade_value.tally uses it for both sides):

- pricing persona: a tilted manager on a skid prices like panicky (`replay_personas.pricing_persona`).
- fan: his favourite team's players FAN_BONUS more (his own and the ones he trades for).
- contender: while he holds a playoff spot from league week CONTEND_FROM (`contending`), a player who would
  start for him this week (his best lineup before the deal for what he sends, after it for what he gets)
  counts CONTEND_START more, anyone else (bench depth, out players) CONTEND_BENCH less.
- feelings (replay_mood.feeling, on his own players): worried caps his price at the chart, and "high on"
  (`markup`) asks ATTACH_LOW over max(price, chart) at the threshold, rising to ATTACH_HIGH at ATTACH_STRONG,
  whoever buys. So a feeling and his price always point the same way, whatever the style adds.
- needs and deep (`position_needs`): for each position with its own starting slots (QB, RB, WR, TE; n of
  them), a team's weakest starter there is its n-th best healthy player's chart and the league bar the median
  of every team's. It needs P when its weakest starter is below NEED_SHARE x the bar (or it has fewer than n
  healthy), and is deep at P when n + 1 or more of its healthy players there are at or above the bar. A CPU pays
  NEED_BONUS for a player at a position it needs, NEED_DEEP_BONUS when everything it sends is from positions
  where it is deep; that and its lineup gain together add at most NEED_CAP of what it gives (`tally`).
All numbers are the user's choices, inside the brief's ranges. Pure: the state and a Knowledge in, numbers out.
"""
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from services import replay_fans, replay_mood, replay_personas, replay_state as rs, replay_values as rv
from services.replay_state import Settings, State, Team
from services.replay_values import Knowledge

FAN_BONUS = 0.10                # "about 10% higher"
CONTEND_FROM = 4                # league week: three regular-season weeks played
CONTEND_START = 0.09            # "about 8-10% more"
CONTEND_BENCH = 0.10            # "about 10% less"
ATTACH_LOW, ATTACH_HIGH = 0.08, 0.15
ATTACH_STRONG = 3 * replay_mood.HIGH    # a feeling this strong (0.24) asks the full ATTACH_HIGH
NEED_SHARE = 0.5                # a weakest starter below half the league's median one is a weak spot
NEED_BONUS, NEED_DEEP_BONUS, NEED_CAP = 0.05, 0.10, 0.10
NEED_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True)
class Mind:
    """One CPU's view of one deal."""
    persona: str                         # his style (Team.persona)
    pricing: str                         # the persona his prices follow now
    hype: float = 1.0
    fan_of: str | None = None
    contending: bool = False
    starting: frozenset[int] = frozenset()      # who would start for him (contender only)
    needs: frozenset[str] = frozenset()
    deep: frozenset[str] = frozenset()


def plain(persona: str, hype: float = 1.0) -> Mind:
    """A Mind with no state: the persona's private values only."""
    return Mind(persona, persona, hype)


def contending(state: State, settings: Settings, team_id: int) -> bool:
    """Does he hold a playoff spot, from league week CONTEND_FROM on?"""
    played = sum(not result.playoff for result in state.results)
    if played < CONTEND_FROM - 1:
        return False
    return any(row["team_id"] == team_id and row["rank"] <= settings.playoff_teams for row in rs.standings(state))


def _healthy(know: Knowledge, roster: Sequence[int], position: str) -> list[float]:
    return sorted((know.by_id[pid].chart for pid in roster if pid in know.by_id
                   and know.by_id[pid].position == position and not know.by_id[pid].out), reverse=True)


def position_needs(state: State, settings: Settings, know: Knowledge) -> dict[int, tuple[frozenset[str], frozenset[str]]]:
    """{team id: (positions it needs, positions where it is deep)} for every team, from its active roster."""
    found: dict[int, tuple[set[str], set[str]]] = {team.id: (set(), set()) for team in state.teams}
    for position in NEED_POSITIONS:
        slots = settings.starters.get(position, 0)
        if slots <= 0:
            continue
        charts = {team.id: _healthy(know, team.roster, position) for team in state.teams}
        weakest = {tid: (c[slots - 1] if len(c) >= slots else 0.0) for tid, c in charts.items()}
        bar = statistics.median(weakest.values())
        for tid, held in charts.items():
            if weakest[tid] < NEED_SHARE * bar or len(held) < slots:
                found[tid][0].add(position)
            elif bar > 0 and sum(chart >= bar for chart in held) >= slots + 1:
                found[tid][1].add(position)
    return {tid: (frozenset(needs), frozenset(deep)) for tid, (needs, deep) in found.items()}


def _starters(know: Knowledge, settings: Settings, roster: Sequence[int]) -> frozenset[int]:
    return frozenset(pid for ids in rv.best_lineup(know, roster, settings).values() for pid in ids)


def mind_for(state: State, settings: Settings, know: Knowledge, team: Team, sends: Sequence[int] = (),
             kept: Sequence[int] = (), needs: Mapping[int, tuple[frozenset[str], frozenset[str]]] | None = None) -> Mind:
    """The CPU's Mind for sending `sends` and keeping `kept` of what it gets (`needs`: position_needs, if known)."""
    needed, deep = (needs if needs is not None else position_needs(state, settings, know))[team.id]
    mind = Mind(team.persona, replay_personas.now(state, team), team.hype, team.fan_of, needs=needed, deep=deep)
    if team.persona != "contender" or not contending(state, settings, team.id):
        return mind
    after = [pid for pid in team.roster if pid not in sends] + list(kept)
    starting = (_starters(know, settings, team.roster) & set(sends)) | (_starters(know, settings, after) & set(kept))
    return replace(mind, contending=True, starting=frozenset(starting))


def markup(know: Knowledge, mind: Mind, player_id: int) -> float:
    """The share he asks over his price for his own player he is high on (0 without the feeling)."""
    felt = replay_mood.feeling(know.by_id[player_id], mind.pricing, mind.hype)
    if felt is None or felt.kind != "high":
        return 0.0
    reach = min(1.0, max(0.0, (felt.strength - replay_mood.HIGH) / (ATTACH_STRONG - replay_mood.HIGH)))
    return ATTACH_LOW + (ATTACH_HIGH - ATTACH_LOW) * reach


def price(know: Knowledge, mind: Mind, player_id: int, own: bool = False, attach: bool = True) -> float:
    """What he thinks the player is worth in this deal. `own`: one of his, where his feelings apply (`attach`
    False leaves out the "high on" mark-up, to tell whether it alone sank a deal)."""
    value = know.by_id[player_id]
    worth = replay_personas.private_value(value, mind.pricing, mind.hype)
    if replay_fans.club(value.team) == mind.fan_of and mind.fan_of is not None:
        worth *= 1 + FAN_BONUS
    if mind.contending:
        worth *= 1 + CONTEND_START if player_id in mind.starting else 1 - CONTEND_BENCH
    if not own:
        return worth
    felt = replay_mood.feeling(value, mind.pricing, mind.hype)
    if felt is not None and felt.kind == "worried":
        return min(worth, value.chart)
    if felt is not None and felt.kind == "high":
        return max(worth, value.chart) * (1 + (markup(know, mind, player_id) if attach else 0.0))
    return worth


def need_bonus(know: Knowledge, mind: Mind, sends: Sequence[int], kept: Sequence[int],
               kept_worth: Mapping[int, float]) -> float:
    """NEED_BONUS (NEED_DEEP_BONUS when it pays only from positions where it is deep) of what it gets at
    positions it needs, before NEED_CAP."""
    filling = [pid for pid in kept if know.by_id[pid].position in mind.needs]
    if not filling:
        return 0.0
    from_depth = bool(sends) and all(know.by_id[pid].position in mind.deep for pid in sends)
    return (NEED_DEEP_BONUS if from_depth else NEED_BONUS) * sum(kept_worth[pid] for pid in filling)
