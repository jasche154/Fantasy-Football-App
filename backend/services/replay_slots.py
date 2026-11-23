"""Replay League lineup slots: which positions play where, the format presets and the bounds a league's
starting lineup must keep.

FLEX takes a RB, WR or TE; SUPERFLEX (shown as "SFLX") also takes a QB. Bounds: 0-MAX_PER_SLOT per slot, at
least one QB or SUPERFLEX, at least one RB/WR/TE/FLEX, at most MAX_STARTERS starters in all.
"""
from collections.abc import Mapping
from types import MappingProxyType

SLOTS = ("QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DST")
FLEX_POSITIONS = ("RB", "WR", "TE")
SUPERFLEX_POSITIONS = ("QB", "RB", "WR", "TE")
ELIGIBLE = MappingProxyType({"FLEX": FLEX_POSITIONS, "SUPERFLEX": SUPERFLEX_POSITIONS})
MAX_PER_SLOT = 3
MAX_STARTERS = 12

STANDARD = MappingProxyType({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1})
PRESETS = MappingProxyType({
    "standard": STANDARD,
    "superflex": MappingProxyType({**STANDARD, "SUPERFLEX": 1}),
    "double_flex": MappingProxyType({**STANDARD, "FLEX": 2}),
})


def eligible(slot: str) -> tuple[str, ...]:
    """The positions that can play in a slot."""
    return ELIGIBLE.get(slot, (slot,))


def superflex(starters: Mapping[str, int]) -> int:
    return starters.get("SUPERFLEX", 0)


def problem(starters: Mapping[str, int]) -> str | None:
    """Why a starting lineup is not allowed, or None when it is."""
    unknown = sorted(set(starters) - set(SLOTS))
    if unknown:
        return f"Unknown lineup slots {unknown}. Valid: {list(SLOTS)}"
    if any(not 0 <= count <= MAX_PER_SLOT for count in starters.values()):
        return f"Each lineup slot takes 0 to {MAX_PER_SLOT} starters."
    if starters.get("QB", 0) + superflex(starters) < 1:
        return "A lineup needs a QB or SUPERFLEX slot."
    if sum(starters.get(slot, 0) for slot in ("RB", "WR", "TE", "FLEX")) < 1:
        return "A lineup needs at least one RB, WR, TE or FLEX slot."
    if sum(starters.values()) > MAX_STARTERS:
        return f"A lineup has at most {MAX_STARTERS} starters, not {sum(starters.values())}."
    return None
