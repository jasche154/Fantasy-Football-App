"""Replay League: the IR slot (one per team).

A player tagged O, IR or PUP this week (`Knowledge.by_id[id].injury`) may be moved from a team's active
roster (`Team.roster`) to its IR (`Team.ir`), which frees his roster spot. Activating needs no tag, but a
team whose IR player has lost his tag is blocked from adds, claims and trades until it activates or drops
him. Dropping an IR player is `replay_state.release`. Imports only state, values and the pool: replay_waivers
and replay_trades import this module.
"""
from dataclasses import replace

from services import replay_state as rs
from services.replay_pool import IR_TAGS
from services.replay_state import Settings, State
from services.replay_values import Knowledge

IR_SLOTS = 1
IR_PHASES = ("waivers", "open")


def _name(know: Knowledge, player_id: int | None) -> str:
    value = know.by_id.get(player_id)
    return value.name if value is not None else "that player"


def _names(know: Knowledge, player_ids) -> str:
    return ", ".join(_name(know, pid) for pid in player_ids)


def eligible(know: Knowledge, player_id: int) -> bool:
    """His tag this week is O, IR or PUP (no projection alone never qualifies). Never at the draft (week 0),
    whose preseason tags are shown but IR slots open with week 1."""
    value = know.by_id.get(player_id)
    return know.week >= 1 and value is not None and value.injury is not None and value.injury.tag in IR_TAGS


def ineligible(state: State, know: Knowledge, team_id: int) -> tuple[int, ...]:
    """That team's IR players who are not eligible now, in IR order."""
    return tuple(pid for pid in rs.team(state, team_id).ir if not eligible(know, pid))


def blocked_reason(state: State, know: Knowledge, team_id: int) -> str | None:
    stale = ineligible(state, know, team_id)
    if not stale:
        return None
    team = rs.team(state, team_id)
    if team.is_user:
        return (f"Activate {_names(know, stale)} from IR to trade or claim: he no longer has an O, IR or PUP tag "
                "(or drop him).")
    return (f"{team.name} can't add, claim or trade for players while {_names(know, stale)} "
            "is on IR without an O, IR or PUP tag: activate or drop him first.")


def require_unblocked(state: State, know: Knowledge, team_id: int) -> None:
    reason = blocked_reason(state, know, team_id)
    if reason:
        raise ValueError(reason)


def _check_phase(state: State) -> None:
    if state.phase not in IR_PHASES:
        raise ValueError("Players can only be moved to or from IR during the season.")


def _check_stashable(team: rs.Team, know: Knowledge, player_id: int) -> None:
    """He is on the active roster and eligible: P5, P6."""
    if player_id not in team.roster:
        raise ValueError(f"{_name(know, player_id)} is not on {team.name}'s roster.")
    if not eligible(know, player_id):
        raise ValueError(f"{_name(know, player_id)} can't go on IR: only a player tagged O, IR or PUP this week can.")


def place(state: State, know: Knowledge, team_id: int, player_id: int) -> State:
    """Move an eligible player from the active roster to the (empty) IR slot."""
    _check_phase(state)
    team = rs.team(state, team_id)
    if player_id not in know.by_id:
        raise ValueError("That player is not in this season's player pool.")
    if player_id in team.ir:
        raise ValueError(f"{_name(know, player_id)} is already on IR.")
    _check_stashable(team, know, player_id)
    if len(team.ir) >= IR_SLOTS:
        raise ValueError(f"{team.name}'s IR slot is taken by {_names(know, team.ir)}: activate or drop him first.")
    moved = replace(team, roster=tuple(pid for pid in team.roster if pid != player_id), ir=(*team.ir, player_id))
    return rs.add_event(rs.with_team(state, moved), "ir", team_id,
                        f"{team.name} moved {_name(know, player_id)} to IR", (player_id,))


def activate(state: State, settings: Settings, know: Knowledge, team_id: int, player_id: int,
             drop: int | None = None, swap: int | None = None) -> State:
    """Bring a player back from IR: into an open spot, dropping `drop`, or trading places with `swap`."""
    _check_phase(state)
    team = rs.team(state, team_id)
    name = _name(know, player_id)
    if player_id not in team.ir:
        raise ValueError(f"{name} is not on {team.name}'s IR.")
    if drop is not None and swap is not None:
        raise ValueError("Pick either a player to drop or a player to swap onto IR, not both.")
    if swap is not None:
        _check_stashable(team, know, swap)
        back = (*(pid for pid in team.roster if pid != swap), player_id)
        stashed = (*(pid for pid in team.ir if pid != player_id), swap)
        moved = replace(team, roster=back, ir=stashed)
        return rs.add_event(rs.with_team(state, moved), "ir", team_id,
                            f"{team.name} activated {name} from IR and moved {_name(know, swap)} to IR",
                            (player_id, swap))
    if drop is not None and drop not in team.roster:
        raise ValueError(f"{team.name} can't drop {_name(know, drop)}: he isn't on the active roster.")
    if len(team.roster) - (drop is not None) >= settings.roster_size:
        raise ValueError(f"The roster is full ({settings.roster_size} players): pick a player to drop to activate {name}.")
    if drop is not None:
        state = rs.release(state, settings, team_id, drop, log=False)     # one event for the move, below
        team = rs.team(state, team_id)
    moved = replace(team, roster=(*team.roster, player_id), ir=tuple(pid for pid in team.ir if pid != player_id))
    text = f"{team.name} activated {name} from IR" + (f", dropping {_name(know, drop)}" if drop is not None else "")
    return rs.add_event(rs.with_team(state, moved), "ir", team_id, text,
                        (player_id,) if drop is None else (player_id, drop))
