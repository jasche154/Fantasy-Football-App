"""Replay League: a CPU filling the starting slots a trade left it unable to fill (`cpu_cover`).

Called by replay_game after an accepted trade (and by `start_week` for every CPU once waivers clear). Only
unlocked free agents who are not out; no dice roll, so the same state gives the same moves.
"""
from collections.abc import Iterable

from services import replay_ir, replay_state as rs, replay_values as rv, replay_waivers
from services.replay_pool import SKILL
from services.replay_state import Settings, State
from services.replay_values import Knowledge, ReplayValue

Move = tuple[int, int | None]     # (player id to add, player id to drop)


def cpu_cover(state: State, settings: Settings, know: Knowledge, team_ids: Iterable[int]) -> State:
    """After a trade: each of those CPUs adds a free agent for every starting slot its roster can no longer
    fill (`replay_values.missing_slots`): the most valuable unlocked free agent who isn't out and can play
    there, dropping its lowest healthy-chart skill player that no starting slot needs when the roster is
    full. In the waivers phase every unrostered player is on waivers, so nothing happens: `start_week` covers
    every CPU once they clear."""
    if state.phase == "waivers":
        return state
    for team_id in team_ids:
        if rs.team(state, team_id).is_user or replay_ir.blocked_reason(state, know, team_id):
            continue
        for _ in range(sum(settings.starters.values())):              # one per starting slot at the very most
            move = _cover_move(state, settings, know, team_id)
            if move is None:
                break
            state = replay_waivers.execute_move(state, settings, know, team_id, *move)
    return state


def _by_chart(value: ReplayValue) -> tuple[float, float, int]:
    return value.chart, value.healthy_value, -value.player_id


def _cover_move(state: State, settings: Settings, know: Knowledge, team_id: int) -> Move | None:
    """The first move that fills one of the team's missing slots, trying each slot in turn. Drops go by
    healthy chart (like `replay_trades._cpu_drops`), so a false out-for-season report never makes one."""
    roster = [know.by_id[pid] for pid in rs.team(state, team_id).roster]
    missing = rv.missing_slots([v.position for v in roster], settings)
    taken = rs.rostered(state) | set(state.locked)
    free = [v for v in know.values if v.player_id not in taken and not v.out]
    cuts = sorted((v for v in roster if v.position in SKILL),
                  key=lambda v: (v.healthy_chart, v.healthy_value, v.ros_ppg, v.player_id))
    for slot in missing:
        eligible = rv.eligible(slot)
        add = max((v for v in free if v.position in eligible), key=_by_chart, default=None)
        if add is None:
            continue
        if len(roster) < settings.roster_size:
            return add.player_id, None
        for out in cuts:
            after = [v.position for v in roster if v is not out] + [add.position]
            if len(rv.missing_slots(after, settings)) < len(missing):
                return add.player_id, out.player_id
    return None
