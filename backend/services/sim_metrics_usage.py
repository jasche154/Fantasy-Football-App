"""Simulated seasons: how a team's plays follow the score and how its work is shared, per team-game (used by sim_eval).

A real season and a simulated one are read alike, from the box lines (`SeasonData.box`, each player's team from
`team_by_week`) and the final scores (`SeasonData.scores`):

- the play calling: the correlations of a team-game's final margin with its carries and with its targets, and of
  its carries with its targets; its carries and targets by MARGINS bucket (lost by 14+ ... won by 14+); their
  spreads; its targets + carries (and pass attempts + carries where box lines carry attempts); the share of losses
  by 14+ with 30+ carries (the user's example: a team losing 28-6 does not run 30 times).
- the sharing: players with a target and with only 1-2 targets; players other than QBs with a carry, backs with a
  carry; the game's top three backs' shares of the non-QB carries and the top five receivers' (and the rest's) of
  the targets; a back with 20+ / 25+ carries and a receiver with 10+ / 13+ targets (% of team-games).
- who carries: QB carries and rushing yards and non-QB carries per team-game.
- idle games (`idle`): the share of a player's available games with no touch, by position group and his mean
  touches (the game plan's lumpiness, judged without box-line selection: a real player with no stat has no box row).

A team-game counts when its team has 5+ carries and 5+ targets in the box (a partial box is left out).
"""
from collections import defaultdict
from collections.abc import Iterable

import numpy as np

from services.replay_pool import SeasonData
from services.sim_metrics_games import corr

MARGINS = (("lost by 14+", -99, -14), ("lost by 4-13", -13, -4), ("within 3", -3, 3), ("won by 4-13", 4, 13),
           ("won by 14+", 14, 99))
BIG_LOSS, MANY_CARRIES = -14, 30
MIN_VOLUME = 5


def _team_games(data: SeasonData, weeks: Iterable[int]) -> list[tuple[int, list]]:
    """[(final margin, [(position, box line)])] per team-game in `weeks` with a score for both teams."""
    weeks = set(weeks)
    team_at = {(pid, w): t for pid, games in (data.team_by_week or {}).items() for w, t in games}
    lines: dict[tuple[str, int], list] = defaultdict(list)
    for (pid, week), box in (data.box or {}).items():
        team = team_at.get((pid, week))
        if week in weeks and team is not None and pid in data.players:
            lines[(team, week)].append((data.players[pid].position, box))
    out = []
    for g in data.schedule.games:
        for team, other in ((g.home_team, g.away_team), (g.away_team, g.home_team)):
            mine, theirs = data.scores.get((team, g.week)), data.scores.get((other, g.week))
            if (team, g.week) in lines and mine is not None and theirs is not None:
                out.append((mine - theirs, lines[(team, g.week)]))
    return out


def _row(margin: int, lines: list) -> dict[str, float] | None:
    carries = sum(b.carries for _, b in lines)
    targets = sorted((b.targets for p, b in lines if p != "QB"), reverse=True)
    if carries < MIN_VOLUME or sum(targets) < MIN_VOLUME:
        return None
    backs = sorted((b.carries for p, b in lines if p == "RB"), reverse=True) + [0, 0, 0]
    non_qb = [b.carries for p, b in lines if p != "QB"]
    attempts = [getattr(b, "pass_att", None) for p, b in lines if p == "QB"]
    row = {"margin": margin, "carries": carries, "targets": sum(targets), "plays": carries + sum(targets),
           "players with a target": sum(t > 0 for t in targets), "players with 1-2 targets": sum(0 < t <= 2 for t in targets),
           "non-QBs with a carry": sum(c > 0 for c in non_qb), "RBs with a carry": sum(c > 0 for c in backs),
           "back with 20+ carries %": 100.0 * (backs[0] >= 20), "back with 25+ carries %": 100.0 * (backs[0] >= 25),
           "receiver with 10+ targets %": 100.0 * (targets[0] >= 10), "receiver with 13+ targets %": 100.0 * (targets[0] >= 13),
           "QB carries": sum(b.carries for p, b in lines if p == "QB"),
           "QB rush yds": sum(b.rush_yds for p, b in lines if p == "QB"), "non-QB carries": sum(non_qb)}
    if any(a is not None for a in attempts):
        row["attempts + carries"] = sum(a or 0 for a in attempts) + carries
    share = max(1, sum(non_qb))
    row.update({f"RB{i + 1} share of non-QB carries": backs[i] / share for i in range(3)})
    row.update({f"target share #{i + 1}": (targets[i] if len(targets) > i else 0) / sum(targets) for i in range(5)})
    row["target share #6+"] = sum(targets[5:]) / sum(targets)
    return row


def usage(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    """The rows of the module docstring over the team-games of `weeks`."""
    weeks = list(weeks)
    rows = [r for margin, lines in _team_games(data, weeks) if (r := _row(margin, lines)) is not None]
    if len(rows) < MIN_VOLUME:
        return {}
    col = {k: np.array([r[k] for r in rows if k in r], dtype=float) for k in rows[0] if k != "margin"}
    margin = np.array([r["margin"] for r in rows], dtype=float)
    out = {f"usage {k}": float(v.mean()) for k, v in col.items() if len(v)}
    out.update({"usage corr(margin, carries)": corr(margin, col["carries"]),
                "usage corr(margin, targets)": corr(margin, col["targets"]),
                "usage corr(carries, targets)": corr(col["carries"], col["targets"]),
                "usage carries sd": float(col["carries"].std()), "usage targets sd": float(col["targets"].std())})
    for name, low, high in MARGINS:
        k = (margin >= low) & (margin <= high)
        if k.any():
            out[f"usage carries, {name}"] = float(col["carries"][k].mean())
            out[f"usage targets, {name}"] = float(col["targets"][k].mean())
    lost = margin <= BIG_LOSS
    if lost.any():
        out["usage 30+ carries in a 14+ loss %"] = 100.0 * float((col["carries"][lost] >= MANY_CARRIES).mean())
    return {**out, **idle(data, weeks)}


IDLE_BANDS = ((0.5, 1.5), (1.5, 3.0), (3.0, 6.0), (6.0, 99.0))
IDLE_GAMES = 4                   # a player counts with this many available games in the weeks


def idle(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    """The share of a player's available games with no touch (no target, no carry), by position group (RB, and WR
    with TE) and his mean touches over those games: available = his team played, he was on it (his team as known
    the week after) and had no hard injury tag. Read alike from a real and a simulated season's box lines, so a
    healthy scratch or an untouched dressed game both count as none."""
    weeks = sorted(set(weeks))
    played = {(team, w) for (team, w) in data.scores}
    games: dict[int, list[int]] = defaultdict(list)
    for pid, player in data.players.items():
        group = "RB" if player.position == "RB" else "WR/TE" if player.position in ("WR", "TE") else None
        if group is None:
            continue
        for w in weeks:
            team = data.team_at(pid, w + 1)
            tag = data.injuries.get((pid, w))
            if team is None or (team, w) not in played or (tag is not None and tag.hard):
                continue
            line = data.box.get((pid, w))
            games[pid].append((line.targets + line.carries) if line else 0)
    out: dict[str, list[float]] = defaultdict(list)
    for pid, touches in games.items():
        if len(touches) < IDLE_GAMES:
            continue
        group = "RB" if data.players[pid].position == "RB" else "WR/TE"
        mean = float(np.mean(touches))
        for low, high in IDLE_BANDS:
            if low <= mean < high:
                out[f"usage no-touch games {group} {low:g}-{high:g} touches %"] += [100.0 * (t == 0) for t in touches]
    return {k: float(np.mean(v)) for k, v in out.items()}
