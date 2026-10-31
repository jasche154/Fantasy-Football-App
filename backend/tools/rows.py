"""Compact, labeled rows for agent tool output.

The UI serializers (services/serialize.py) carry every field the pages need, about 800 characters
a weekly row. A model on a free tier needs a fraction of that, with directions spelled out in words
(">1 = easier" is easy to flip). These helpers build the agent's rows; the UI keeps its own.
"""
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from services.labels import label

SOURCE_SHORT = {"vegas_props": "props", "yahoo_consensus": "Yahoo", "josh_hayden": "J&H", "espn": "ESPN",
                "model": "model"}
SOURCE_ORDER = ("vegas_props", "yahoo_consensus", "josh_hayden", "espn", "model")


def player_str(name: str, position: str | None = None, team: str | None = None) -> str:
    """'Puka Nacua (WR, LA)'. Grounding treats the text before ' (' as the player's name."""
    details = ", ".join(part for part in (position, team) if part)
    return f"{name} ({details})" if details else name


def percent_words(factor: float) -> tuple[int, str]:
    percent = round((factor - 1) * 100)
    return abs(percent), "easier" if percent > 0 else "harder"


def matchup_words(factor: float, position: str | None = None) -> str:
    """A defense factor in words: '6% harder than avg vs RB' (>1 means easier)."""
    against = f" vs {position}" if position else ""
    amount, direction = percent_words(factor)
    return f"average matchup{against}" if amount == 0 else f"{amount}% {direction} than avg{against}"


def schedule_words(factor: float) -> str:
    """A playoff schedule factor in words, so the model can't flip its direction (>1 = easier)."""
    amount, direction = percent_words(factor)
    if amount == 0:
        return "average (fantasy playoff weeks)"
    return f"{amount}% {direction} than average (fantasy playoff weeks)"


def opponent_str(matchup: Any) -> str:
    """'vs LAC' at home, '@ LAC' away, with '(final)' once the game is over."""
    text = f"{'vs' if matchup.home else '@'} {matchup.opponent}"
    return f"{text} (final)" if matchup.game_final else text


def range_str(floor: float | None, ceiling: float | None) -> str | None:
    return f"{floor:.1f}-{ceiling:.1f}" if floor is not None and ceiling is not None else None


def sources_str(sources: Mapping[str, Any]) -> str | None:
    """'props 21.8 | Yahoo 21.1 | ESPN 22.4 | model 22.8'."""
    keys = [k for k in SOURCE_ORDER if k in sources] + [k for k in sources if k not in SOURCE_ORDER]
    parts = [f"{SOURCE_SHORT.get(k, k)} {sources[k].points:.1f}" for k in keys]
    return " | ".join(parts) or None


def kickoff_str(moment: datetime | None) -> str | None:
    from services.game_locks import eastern_time

    return eastern_time(moment) if moment else None


def weekly_row(player: Any, *, flags: Iterable[Any] = (), kickoff: datetime | None = None,
               sources: bool = True, rank: int | None = None) -> dict:
    """One projected player for a week, about 230 characters once empty fields are dropped.

    proj already includes his chance to play; range (10th-90th percentile) assumes he plays.
    """
    matchup = player.matchup
    row = {"rank": rank, "player": player_str(player.name, player.position, player.team),
           "pos_rank": player.pos_rank or None, "proj": round(player.projection, 1),
           "range": range_str(player.floor, player.ceiling), "play_prob": round(player.play_prob, 2),
           "status": player.status, "opp": opponent_str(matchup) if matchup.opponent else None,
           "matchup": matchup_words(matchup.sos_factor, player.position) if matchup.opponent else None,
           "implied_total": matchup.implied_total, "kickoff_et": kickoff_str(kickoff),
           "flags": [f.label for f in flags] or None,
           "sources": sources_str(player.sources) if sources else None,
           "actual": player.actual}
    return {k: v for k, v in row.items() if v is not None}


def header(scoring: str, week: int | None = None, league: str | None = None, **extra: Any) -> dict:
    """The context every ranking/projection result starts with; `week` is the lineup week."""
    head = {"league": label(league) if league else None, "scoring": scoring, "week": week, **extra}
    return {k: v for k, v in head.items() if v is not None}
