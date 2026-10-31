"""Injury report for Ask AI: who is hurt, how likely he plays, and who inherits his work.

Everything comes from the already-built weekly rankings (`WeeklyRankings.context`), so nothing is
fetched here. Unlike the rankings, a ruled-out player still gets a row (play_prob 0): "is he
playing?" must have an answer even when he has no projection.

- `availability_row`: status, injury, final practice, chance to play, where the tag came from, and
  when inactives come out (about 90 minutes before his kickoff).
- `beneficiaries`: vacated-work boosts are stored by the player who gains; this inverts them to
  "who gains if <player> sits".
- `returning_rows`: starters coming back, and the fill-ins whose roles shrink.

Injury and status text come from Sleeper / the injury report, so they go through `untrusted()`.
"""
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from services.game_locks import INACTIVES_BEFORE, eastern_time
from services.injuries import InjuryContext
from services.labels import untrusted
from services.schedule_strength import Schedule

SOURCES = {"sleeper": "Sleeper live tag", "report": "official injury report"}
MAX_INHERITS = 3                 # beneficiaries listed per absent player
MAX_TEXT = 40                    # injury / status text from outside sources
TAGGED_BELOW = 1.0               # play probability under this counts as a designation


@dataclass(frozen=True)
class Person:
    """Display facts for one player (from the valuation, or the players table for anyone else)."""

    name: str
    position: str | None
    team: str | None

    def text(self) -> str:
        details = ", ".join(p for p in (self.position, self.team) if p)
        return f"{self.name} ({details})" if details else self.name


def _clean(text: str | None) -> str | None:
    return untrusted(text, MAX_TEXT) or None if text else None


def game_note(team: str | None, week: int, schedule: Schedule, now: datetime) -> dict:
    """{'bye': True} with no game; otherwise kickoff timing: inactives time, or that it has started."""
    game = schedule.game(team, week) if team else None
    if team and game is None and any(team in (g.home_team, g.away_team) for g in schedule.games):
        return {"bye": True}
    if game is None or game.kickoff is None:
        return {}
    if game.is_final or game.kickoff <= now:
        return {"game": "kicked off (locked)"}
    return {"kickoff": eastern_time(game.kickoff), "inactives_at": eastern_time(game.kickoff - INACTIVES_BEFORE)}


def availability_row(person: Person, player_id: int, context: InjuryContext, note: Mapping) -> dict:
    """One player's status row; healthy players read 'Healthy' with play_prob 1.0."""
    av = context.of(player_id)
    healthy = av.status is None and av.practice in (None, "FP") and av.play_prob >= TAGGED_BELOW
    row = {"player": person.text(), "status": "Healthy" if healthy else _clean(av.status) or "No designation",
           "injury": _clean(av.injury), "practice": av.practice, "play_prob": round(av.play_prob, 2),
           "source": SOURCES.get(av.source), **note}
    if av.play_prob <= 0:
        row["note"] = "ruled out: no projection this week"
    return {k: v for k, v in row.items() if v is not None}


def beneficiaries(context: InjuryContext, player_id: int, people: Callable[[int], Person],
                  limit: int = MAX_INHERITS) -> list[dict]:
    """Who gains if this player sits: +if_sits points if he's out for sure, expected = weighted by his odds."""
    gains = [b for boosts in context.boosts.values() for b in boosts if b.from_player_id == player_id]
    gains.sort(key=lambda b: -b.full_points)
    return [{"player": people(b.player_id).text(), "if_sits": round(b.full_points, 1),
             "expected": round(b.expected_points, 1), "method": b.method} for b in gains[:limit]]


def returning_rows(context: InjuryContext, fill_ins: Iterable[int], people: Callable[[int], Person],
                   starter: str | None = None) -> list[dict]:
    """Starters coming back and the fill-in whose role shrinks (optionally only for one starter's name)."""
    wanted = set(fill_ins)
    rows = []
    for fill_in, backs in context.returning.items():
        for back in backs:
            if (starter is not None and back.name == starter) or (starter is None and fill_in in wanted):
                rows.append({"starter_returning": back.name, "fill_in": people(fill_in).text(),
                             "fill_in_loses": round(back.expected_loss, 1)})
    return rows


def tagged_ids(context: InjuryContext, player_ids: Iterable[int]) -> list[int]:
    """Players with a designation or a chance to play under 100%, most at risk first."""
    tagged = [pid for pid in player_ids
              if pid in context.availability and (context.of(pid).play_prob < TAGGED_BELOW or context.of(pid).status)]
    return sorted(tagged, key=lambda pid: context.of(pid).play_prob)


def my_gains(context: InjuryContext, mine: Iterable[int], people: Callable[[int], Person]) -> list[dict]:
    """The user's players who inherit work because a teammate may sit."""
    rows = []
    for pid in mine:
        for b in context.boosts.get(pid, ()):
            rows.append({"player": people(pid).text(), "if_teammate_sits": b.from_name,
                         "teammate_status": _clean(b.from_status), "if_sits": round(b.full_points, 1),
                         "expected": round(b.expected_points, 1)})
    return sorted(rows, key=lambda r: -r["if_sits"])
