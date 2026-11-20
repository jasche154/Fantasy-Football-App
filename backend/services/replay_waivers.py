"""Replay League: the waiver wire's rules and the user's own roster moves.

A week has two parts. While the phase is "waivers" every unrostered player is on waivers: teams put in
claims, and `process_claims` awards them by rolling priority (the first team in `state.waiver_order` with a
claim that still works gets its player and goes to the back of the line; the list is never reset). Once the
phase is "open" the players left are free agents, first come first served (`add_drop`), except those dropped
since the last waiver run (`state.locked`), who wait for the next one.

Every drop goes through `replay_state.release`, so a dropped player is always locked. Nothing here changes
the phase: the orchestrator (services/replay_game.py) owns the week flow. CPU decisions are in
services/replay_waiver_ai.py.
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace

from services import replay_state as rs
from services.replay_pool import ALL_POSITIONS
from services.replay_state import Claim, Settings, State
from services.replay_values import Knowledge, ReplayValue, can_field_lineup

ROSTERED, WAIVERS, FREE_AGENT = "rostered", "waivers", "free_agent"
EVERYONE = "all"
STATUS_FILTERS = (WAIVERS, FREE_AGENT, ROSTERED, EVERYONE)
ROSTER_PHASES = ("waivers", "open")           # the phases in which rosters can change


def _status(state: State, rostered: frozenset[int], player_id: int) -> str:
    if player_id in rostered:
        return ROSTERED
    return WAIVERS if state.phase == "waivers" or player_id in state.locked else FREE_AGENT


def player_status(state: State, player_id: int) -> str:
    """"rostered", "waivers" or "free_agent".

    Phase "waivers": every unrostered player is on waivers. Otherwise an unrostered player is a free agent
    unless he was dropped since the last waiver run (`state.locked`)."""
    return _status(state, rs.rostered(state), player_id)


def available_players(state: State, know: Knowledge, position: str | None = None, search: str | None = None,
                      status: str | None = None) -> list[ReplayValue]:
    """The Players screen: unrostered players, best first (the order of `know.values`).

    `position` is one of QB/RB/WR/TE/K/DST, `search` matches any part of the name (case-insensitive) and
    `status` narrows to "waivers" or "free_agent"; "rostered" and "all" widen the list to rostered players."""
    if position is not None and position not in ALL_POSITIONS:
        raise ValueError(f"Unknown position '{position}'. Valid: {list(ALL_POSITIONS)}")
    if status is not None and status not in STATUS_FILTERS:
        raise ValueError(f"Unknown status '{status}'. Valid: {list(STATUS_FILTERS)}")
    rostered, text = rs.rostered(state), (search or "").strip().lower()

    def wanted(value: ReplayValue) -> bool:
        found = _status(state, rostered, value.player_id)
        return ((position is None or value.position == position) and text in value.name.lower()
                and (status == EVERYONE or (found != ROSTERED if status is None else found == status)))

    return [value for value in know.values if wanted(value)]


def _name(know: Knowledge, player_id: int | None) -> str:
    value = know.by_id.get(player_id)
    return value.name if value is not None else "that player"


def _problem(state: State, settings: Settings, know: Knowledge, team_id: int, add: int,
             drop: int | None) -> str | None:
    """Why this team can't add `add` (dropping `drop`) right now, as a sentence; None when it can."""
    team = rs.team(state, team_id)
    if add not in know.by_id:
        return "That player is not in this season's player pool."
    owner = next((t for t in state.teams if add in t.roster), None)
    if owner is not None:
        return f"{_name(know, add)} is already on a roster ({owner.name})."
    if drop is not None and drop not in team.roster:
        return f"{team.name} can't drop {_name(know, drop)}: he isn't on that roster."
    if drop is None and len(team.roster) >= settings.roster_size:
        return f"The roster is full ({settings.roster_size} players): pick a player to drop."
    return None


def execute_move(state: State, settings: Settings, know: Knowledge, team_id: int, add: int,
                 drop: int | None = None, kind: str = "add") -> State:
    """Put an unrostered player on a team, releasing `drop` first (he goes on waivers). No phase or
    waiver-status check: `add_drop`, `process_claims` and the CPU free-agency pass do their own.

    Logs the release's "drop" event, then one `kind` event ("add" or "claim") naming both players."""
    problem = _problem(state, settings, know, team_id, add, drop)
    if problem:
        raise ValueError(problem)
    if drop is not None:
        state = rs.release(state, settings, team_id, drop, _name(know, drop))
    team = rs.team(state, team_id)
    state = rs.with_team(state, replace(team, roster=(*team.roster, add)))
    verb = "claimed" if kind == "claim" else "added"
    dropped = f", dropping {_name(know, drop)}" if drop is not None else ""
    return rs.add_event(state, kind, team_id, f"{team.name} {verb} {_name(know, add)}{dropped}",
                        (add,) if drop is None else (add, drop))


# ---------------- the user's claims ----------------

def submit_claim(state: State, settings: Settings, know: Knowledge, add: int, drop: int | None) -> State:
    """The user's waiver claim, added last in their preference order. Phase "waivers" only.

    `drop` may be None only while the roster has room. Several claims may name the same drop: the first one
    that is awarded uses him, the others are then skipped."""
    if state.phase != "waivers":
        raise ValueError("Waiver claims can only be made before the weekly waiver run. "
                         "Once it has run, add free agents directly.")
    user = rs.user_team(state)
    problem = _problem(state, settings, know, user.id, add, drop)
    if problem:
        raise ValueError(problem)
    if any(claim.add == add for claim in state.claims):
        raise ValueError(f"You already have a claim in for {_name(know, add)}.")
    claim = Claim(id=state.next_id, team_id=user.id, add=add, drop=drop)
    return replace(state, claims=(*state.claims, claim), next_id=state.next_id + 1)


def cancel_claim(state: State, claim_id: int) -> State:
    if all(claim.id != claim_id for claim in state.claims):
        raise ValueError(f"There is no pending claim {claim_id} to cancel.")
    return replace(state, claims=tuple(claim for claim in state.claims if claim.id != claim_id))


def reorder_claims(state: State, ids: Sequence[int]) -> State:
    """The user's claims in a new preference order: `ids` must list every pending claim id exactly once."""
    by_id = {claim.id: claim for claim in state.claims}
    order = list(ids)
    if len(order) != len(by_id) or set(order) != set(by_id):
        raise ValueError("To reorder claims, list every pending claim exactly once.")
    return replace(state, claims=tuple(by_id[claim_id] for claim_id in order))


# ---------------- the waiver run ----------------

def _keeps_lineup(state: State, settings: Settings, know: Knowledge, claim: Claim) -> bool:
    """After this claim the team can still fill every starting slot."""
    roster = rs.team(state, claim.team_id).roster
    after = [pid for pid in roster if pid != claim.drop] + [claim.add]
    return can_field_lineup([know.by_id[pid].position for pid in after], settings)


def _refusal(state: State, settings: Settings, know: Knowledge, claim: Claim) -> str | None:
    """Why this claim can't be awarded now; None when it can. A CPU is never left without a legal lineup
    (its second claim was planned on top of its first, which may have failed); the user may do that."""
    problem = _problem(state, settings, know, claim.team_id, claim.add, claim.drop)
    if problem is not None or rs.team(state, claim.team_id).is_user:
        return problem
    return None if _keeps_lineup(state, settings, know, claim) else "It would leave the team without a lineup."


def _queues(state: State, claims_by_team: Mapping[int, Sequence[Claim]]) -> dict[int, list[Claim]]:
    """Each team's claims in its own order: CPUs' from the caller, the user's from the state."""
    users = {team.id for team in state.teams if team.is_user}
    queues = {team_id: list(claims) for team_id, claims in claims_by_team.items() if team_id not in users}
    for claim in state.claims:
        queues.setdefault(claim.team_id, []).append(claim)
    return queues


def _next_award(state: State, settings: Settings, know: Knowledge,
                queues: dict[int, list[Claim]]) -> tuple[Claim | None, list[tuple[Claim, str]]]:
    """(the claim to award next, the claims skipped on the way with the reason). Takes them off the queues.

    A skipped claim never comes back: within one run a taken player stays taken and a full roster stays full."""
    skipped: list[tuple[Claim, str]] = []
    for team_id in state.waiver_order:
        queue = queues.get(team_id, [])
        while queue:
            claim = queue.pop(0)
            reason = _refusal(state, settings, know, claim)
            if reason is None:
                return claim, skipped
            skipped.append((claim, reason))
    return None, skipped


def _log_failures(state: State, know: Knowledge, skipped: Sequence[tuple[Claim, str]]) -> State:
    """A short note for each of the USER's claims that failed; CPU failures are not logged."""
    for claim, reason in skipped:
        if rs.team(state, claim.team_id).is_user:
            state = rs.add_event(state, "claim", claim.team_id,
                                 f"Your claim for {_name(know, claim.add)} failed. {reason}", (claim.add,))
    return state


def process_claims(state: State, settings: Settings, know: Knowledge,
                   claims_by_team: Mapping[int, Sequence[Claim]]) -> State:
    """The weekly waiver run, by rolling priority.

    Repeatedly: the first team in `state.waiver_order` that has a claim which still works (the player is
    unrostered, the drop is still on its roster or there is room) gets that player and moves to the end of
    the order. A team's claims are tried in its own order; one that no longer works is skipped. The user's
    claims are `state.claims`, the CPUs' come in `claims_by_team` ({team id: claims, best first}).
    Clears `state.claims`; does not change the phase or `state.locked`."""
    queues = _queues(state, claims_by_team)
    while True:
        claim, skipped = _next_award(state, settings, know, queues)
        state = _log_failures(state, know, skipped)
        if claim is None:
            return replace(state, claims=())
        state = execute_move(state, settings, know, claim.team_id, claim.add, claim.drop, kind="claim")
        order = state.waiver_order
        state = replace(state, waiver_order=(*(t for t in order if t != claim.team_id), claim.team_id))


# ---------------- free agency ----------------

def add_drop(state: State, settings: Settings, know: Knowledge, team_id: int, add: int,
             drop: int | None = None) -> State:
    """Add a free agent now, dropping `drop` (needed when the roster is full). Phase "open" only.

    The dropped player goes on waivers. The move is not checked for lineup sense: a team may leave itself
    without a kicker."""
    if state.phase != "open":
        raise ValueError("Free agents can be added once this week's waivers have run. "
                         "Until then, put in a waiver claim.")
    if add in state.locked and add not in rs.rostered(state):
        raise ValueError(f"{_name(know, add)} was just dropped and is on waivers until next week.")
    return execute_move(state, settings, know, team_id, add, drop, kind="add")


def drop(state: State, settings: Settings, know: Knowledge, team_id: int, player_id: int) -> State:
    """Drop a player without adding anyone; he goes on waivers until the next start of week."""
    if state.phase not in ROSTER_PHASES:
        raise ValueError("Players can only be dropped during the season.")
    return rs.release(state, settings, team_id, player_id, _name(know, player_id))
