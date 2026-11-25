"""Replay League trades: what a deal does to the rosters (re-exported by replay_trades, which documents the rules).

`landing` puts each player where he arrives (a player from the other team's IR slot takes a free IR slot), a CPU
cuts with `cpu_drops` and the user names his own (`user_drops`), and `cover` finds the free agents
(`free_agents`) for the starting slots a deal leaves empty. Pure.
"""
from collections import Counter
from collections.abc import Sequence

from services import replay_ir, replay_state as rs, replay_values as rv
from services.replay_pool import SKILL
from services.replay_state import Settings, State, Team
from services.replay_values import Knowledge


def positions(know: Knowledge, player_ids: Sequence[int]) -> list[str]:
    return [know.by_id[pid].position for pid in player_ids]


def landing(team: Team, other: Team, sends: tuple[int, ...], receives: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(active roster, IR) after the deal, before drops: a player from `other`'s IR slot takes a free IR slot."""
    roster = [pid for pid in team.roster if pid not in sends]
    ir = [pid for pid in team.ir if pid not in sends]
    for pid in receives:
        (ir if pid in other.ir and len(ir) < replay_ir.IR_SLOTS else roster).append(pid)
    return tuple(roster), tuple(ir)


def user_drops(team: Team, settings: Settings, kept: Sequence[int], drops: tuple[int, ...]) -> tuple[int, ...]:
    """The user's own choice of who to cut, which must cover the overflow exactly."""
    over = max(0, len(kept) - settings.roster_size)
    if len(set(drops)) != len(drops) or any(pid not in team.roster or pid not in kept for pid in drops):
        raise ValueError("You can only drop players who are on your roster and not part of the trade.")
    if len(drops) != over:
        raise ValueError(f"This trade leaves you {over} over the roster limit of {settings.roster_size}: "
                         f"choose exactly {over} to drop." if over else "This trade doesn't need you to drop anyone.")
    return drops


def _over_limit(settings: Settings, know: Knowledge, roster: Sequence[int], ir: Sequence[int],
                before: Sequence[int]) -> str | None:
    """A skill position the roster and IR slot hold more of than its limit and than `before` the deal did."""
    had, held = Counter(positions(know, before)), Counter(positions(know, [*roster, *ir]))
    return next((pos for pos in SKILL if held[pos] > max(rv.position_cap(pos, settings), had[pos])), None)


def cpu_drops(settings: Settings, know: Knowledge, kept: Sequence[int], ir: Sequence[int] = (),
              before: Sequence[int] = ()) -> tuple[int, ...] | None:
    """Who a CPU cuts to get back to the roster limit, and within every position limit the deal takes it past
    (`before`: what it held; Colston Loveland as a third tight end was cut the next week): its lowest
    healthy-chart skill players (at that position) that leave the lineup legal. Never a kicker or a defense.
    None when nobody can go."""
    roster, drops = list(kept), []
    while len(roster) > settings.roster_size or _over_limit(settings, know, roster, ir, before):
        crowded = _over_limit(settings, know, roster, ir, before)
        legal = rv.can_field_lineup(positions(know, roster), settings)
        skill = sorted((know.by_id[pid] for pid in roster if know.by_id[pid].position in SKILL
                        and crowded in (None, know.by_id[pid].position)),
                       key=lambda v: (v.healthy_chart, v.healthy_value, v.ros_ppg, v.player_id))
        cut = next((v.player_id for v in skill if not legal or rv.can_field_lineup(
            positions(know, [pid for pid in roster if pid != v.player_id]), settings)), None)
        if cut is None:
            return None
        roster.remove(cut)
        drops.append(cut)
    return tuple(drops)


def free_agents(state: State, know: Knowledge) -> list[int]:
    """Who could fill a slot a trade empties, best first: unrostered, not out, and free once waivers clear.
    In the open phase that excludes players on waivers (`state.locked`). In the waivers phase everyone
    unrostered is on waivers and the cover waits for them to clear (`replay_cover.cpu_cover`), so it is
    anyone the user hasn't claimed (a CPU's own claim may take one; the next best then covers)."""
    claimed = {claim.add for claim in state.claims if state.phase == "waivers"}
    taken = rs.rostered(state) | (claimed if state.phase == "waivers" else set(state.locked))
    pool = (v for v in know.values if v.player_id not in taken and not v.out)
    return [v.player_id for v in sorted(pool, key=lambda v: (-v.chart, -v.ros_ppg, v.player_id))]


def cover(know: Knowledge, settings: Settings, roster: Sequence[int], pool: Sequence[int],
          used: frozenset[int] = frozenset()) -> tuple[int, ...] | None:
    """One free agent from `pool` (best first) per starting slot `roster` cannot fill, none in `used`; None
    when a slot has nobody eligible. () for a legal roster."""
    picked: list[int] = []
    for slot in rv.missing_slots(positions(know, roster), settings):
        eligible = rv.eligible(slot)
        found = next((pid for pid in pool if pid not in used and pid not in picked
                      and know.by_id[pid].position in eligible), None)
        if found is None:
            return None
        picked.append(found)
    return tuple(picked)
