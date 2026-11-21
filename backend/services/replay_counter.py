"""Replay League counter-offers: after a value-only decline a CPU may answer with a deal it would take.

Candidates: add one of the user's players, swap a requested player for a lesser one at his position, or
drop one requested player; it picks the one it accepts that costs the user least (never a "big_loss" for
the user, never one needing a user drop) and stores it as a pending Offer(counter_to=declined.id). It never
counters a counter of its own counter. See .plans/pr1-plan.md section D.
"""
from services.replay_state import Offer, Settings, State
from services.replay_values import Knowledge


def counter_offer(state: State, settings: Settings, know: Knowledge, declined: Offer, seed: int) -> State:
    raise NotImplementedError
