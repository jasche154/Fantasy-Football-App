"""Game-day lineup rules: locked players, late-kickoff FLEX, and Questionable-player warnings.

- A player whose game has kicked off (or is final) is locked: a locked starter stays in the
  lineup and a locked bench player can't come in. Advice only moves unlocked players.
- Among starters, the latest-kicking FLEX-eligible player goes in FLEX. It costs nothing and
  keeps a late swap open if news breaks before his game.
- A Questionable (or untagged but risky) starter gets a warning: when inactives come out
  (about 90 minutes before his kickoff) and whether a bench player at his position plays
  later, so you can still swap.
"""
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

INACTIVES_BEFORE = timedelta(minutes=90)
FLEX_SLOTS = ("FLEX",)
FLEX_POSITIONS = ("RB", "WR", "TE")
WARN_BELOW = 0.9                  # play probability under this gets a warning
EASTERN_OFFSETS = (4, 5)          # daylight / standard hours behind UTC


def kickoffs(players: Sequence, schedule, week: int) -> dict[int, datetime]:
    out = {}
    for p in players:
        game = schedule.game(p.team, week) if p.team else None
        if game is not None and game.kickoff is not None:
            out[p.player_id] = game.kickoff
    return out


def locked_ids(players: Sequence, kickoff: Mapping[int, datetime], now: datetime | None = None) -> set[int]:
    now = now or datetime.now(timezone.utc)
    return {p.player_id for p in players
            if getattr(p, "actual", None) is not None or (p.player_id in kickoff and kickoff[p.player_id] <= now)}


def lock_key(locked_starters: set[int], key=None):
    """A ranking key that keeps locked starters in the lineup ahead of everyone else."""
    base = key or (lambda p: p.projection)
    return lambda p: 1e6 if p.player_id in locked_starters else base(p)


def prefer_late_flex(lineup: Mapping[str, list[int]], positions: Mapping[int, str],
                     kickoff: Mapping[int, datetime], locked: set[int]) -> dict[str, list[int]]:
    """Swap the FLEX starter with a same-position starter who kicks off later (both unlocked)."""
    out = {slot: list(ids) for slot, ids in lineup.items()}
    for flex_slot in FLEX_SLOTS:
        for index, flex_id in enumerate(out.get(flex_slot, [])):
            if flex_id in locked or flex_id not in kickoff:
                continue
            position = positions.get(flex_id)
            if position not in FLEX_POSITIONS:
                continue
            later = [(kickoff[pid], i, pid) for i, pid in enumerate(out.get(position, []))
                     if pid not in locked and pid in kickoff and kickoff[pid] > kickoff[flex_id]]
            if later:
                _, slot_index, late_id = max(later)
                out[flex_slot][index], out[position][slot_index] = late_id, flex_id
    return out


def eastern_time(moment: datetime) -> str:
    """'Sun 4:25 PM ET' (standard or daylight time by the US rule)."""
    from services.schedule_strength import _eastern_dst

    for hours in EASTERN_OFFSETS:
        local = moment.replace(tzinfo=None) - timedelta(hours=hours)
        if _eastern_dst(local) == (hours == EASTERN_OFFSETS[0]):
            return f"{local:%a} {local:%I:%M %p}".replace(" 0", " ") + " ET"
    return f"{moment:%a %H:%M} UTC"


def alerts(starters: Sequence, bench: Sequence, kickoff: Mapping[int, datetime], locked: set[int]) -> list[str]:
    """Warnings for risky starters whose games haven't started."""
    out = []
    for player in starters:
        if player.player_id in locked or player.play_prob >= WARN_BELOW or player.player_id not in kickoff:
            continue
        start = kickoff[player.player_id]
        backups = [b for b in bench if b.position == player.position and b.player_id not in locked
                   and b.play_prob >= WARN_BELOW and b.player_id in kickoff]
        best = max(backups, key=lambda b: b.projection, default=None)
        status = player.status or "Questionable"
        note = f"{player.name} ({status}, {round(player.play_prob * 100)}% to play) kicks off {eastern_time(start)}; " \
               f"inactives about {eastern_time(start - INACTIVES_BEFORE)}."
        if best is None:
            note += f" No healthy {player.position} on your bench."
        elif kickoff[best.player_id] >= start:
            note += f" Backup {best.name} plays {eastern_time(kickoff[best.player_id])}, so you can still swap."
        else:
            note += f" Your backup {best.name} plays earlier ({eastern_time(kickoff[best.player_id])}): decide before then."
        out.append(note)
    return out
