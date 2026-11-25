"""Replay League: the team fan's favourite NFL team and its rival (the "fan" persona, replay_personas).

A fan CPU gets one favourite team at `replay_draft.new_game` (seeded, stored as `Team.fan_of`; None for every
other manager and in old saves). He values that team's players FAN_BONUS higher (replay_styles) and never takes
a player of its rival (RIVALS, the user's table): he declines such a trade and never proposes one, never drafts
him, never claims or adds him, unless no other legal move can field his lineup (`replay_draft`,
`replay_waiver_ai`, `replay_cover`). Team codes are the data's: LA is the Rams; 2018-19 seasons write the
Raiders as OAK, which counts as LV (`club`). Pure.
"""
from types import MappingProxyType

from services import replay_state as rs
from services.kdst_rankings import TEAM_NAMES
from services.replay_state import Team

RIVALS = MappingProxyType({      # favourite -> rival (the user's table)
    "CHI": "GB", "GB": "CHI", "MIN": "GB", "DET": "CHI", "DAL": "PHI", "PHI": "DAL", "NYG": "PHI", "WAS": "DAL",
    "NO": "ATL", "ATL": "NO", "TB": "NO", "CAR": "ATL", "SF": "SEA", "SEA": "SF", "LA": "SF", "ARI": "SEA",
    "PIT": "BAL", "BAL": "PIT", "CLE": "PIT", "CIN": "PIT", "NE": "NYJ", "NYJ": "NE", "BUF": "MIA", "MIA": "BUF",
    "JAX": "TEN", "TEN": "JAX", "HOU": "IND", "IND": "HOU", "KC": "LV", "LV": "KC", "DEN": "LV", "LAC": "KC",
})
FAVOURITES = tuple(sorted(RIVALS))
ALIASES = MappingProxyType({"OAK": "LV"})


def club(code: str | None) -> str | None:
    """The team code as RIVALS writes it (OAK is LV)."""
    return ALIASES.get(code, code) if code else None


def pick_team(seed: int, team_id: int) -> str:
    """A fan's favourite team, seeded per game and team."""
    return FAVOURITES[int(rs.rng(seed, "fan", team_id).integers(len(FAVOURITES)))]


def rival(team: Team) -> str | None:
    """The team a fan won't take players from; None for anyone who isn't a fan."""
    return RIVALS.get(team.fan_of) if team.fan_of else None


def banned(team: Team, nfl_team: str | None) -> bool:
    """Is a player of that NFL team one this manager refuses to take?"""
    hated = rival(team)
    return hated is not None and club(nfl_team) == hated


def nickname(code: str | None) -> str:
    """"Bears" for CHI; the code itself when unknown."""
    found = club(code)
    return TEAM_NAMES.get(found, found or "")
