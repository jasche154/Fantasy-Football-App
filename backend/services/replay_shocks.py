"""Replay League opportunity shocks: the trade market's reaction to a change of role, on little or no game
sample (pure, point-in-time).

Trade values model the market (what managers would pay), not only the forecast: when a back's starter goes
on IR, or a backup takes the job in week 1, managers re-price him at once, long before his projections and
the experts' ranks catch up. Each event below moves a player's market rank (position rank) from his anchor
(the latest expert snapshot before the event; projection ranks for a season without experts) toward a rank.
From his chart's current position rank r (healthy_chart):

    log target = log r + GAMMA[kind, position] x m x (log toward - log r)

and his healthy chart is multiplied by f x (value_at(target) / value_at(r) - 1) + 1: a share of the gap
still open, so it shrinks to nothing as the experts and projections catch up (nothing counts twice), and
f = 1 - (W - week) / FADE_WEEKS fades the hype; his own injury discount still applies. Events at week W:
- teammate_out: a teammate at his position ranked inside RELEVANT, out (hard tag or no projection) since
  week s >= 2; the best-ranked teammate below him gains, m = the share of the remaining games the teammate
  misses (his news report when there is one: "out for the season" counts fully), at least MIN_MISSED.
- teammate_left: such a teammate played for another team (a trade or a signing; seen after his first game
  there); m = 1.
- starter_back: such a teammate back after MIN_ABSENCE+ weeks out with an injury tag (an untagged absence is a
  scratch, a trade or a release: Cam Akers' 2023 "return" was his first game for Minnesota): the one who
  stood in moves back toward his rank before the absence.
- role_won / role_lost (weeks ROLE_WEEKS): his share of his team's opportunities (RB carries + targets
  among its backs; WR/TE target share) at least ROLE_MARGIN above or below the share his preseason rank
  implies (SHARE_FIT); toward = the rank that implies the share he got. A loser needs a winning teammate.
One game's points never move anything: every shock needs an event. GAMMA is calibrated by
services/replay_shock_eval.py (leave one season out) on how far the market (expert ranks and projections,
minus every other player's drift) moved beyond the chart in the 1-3 weeks after: mostly a little, because
the chart already reacts at the event week about as far as that market does. Reads only `data.as_of(week)`; the real
return weeks only through replay_news.report (the caller's news).
"""
import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from services.replay_pool import SeasonData

POSITIONS = ("RB", "WR", "TE")
RELEVANT = {"RB": 36, "WR": 48, "TE": 18}  # the teammate behind teammate_out / left / back is ranked inside this
MIN_MISSED = 0.25              # teammate_out: the least share of the remaining games he must miss
MIN_ABSENCE = 2                # starter_back: non-bye weeks out before the return
FADE_WEEKS = 4                 # weeks a shock lasts: 1, 0.75, 0.5, 0.25 of its move
ROLE_WEEKS = (2, 3)            # role events: after week 1's or weeks 1-2's games
ROLE_MARGIN = {"RB": 0.15, "WR": 0.07, "TE": 0.07}       # share above / below what his preseason rank implies
# share = a + b x ln(preseason rank): fitted on weeks 1-2 of 2018-25 (replay_shock_eval)
SHARE_FIT = {"RB": (1.071, -0.192), "WR": (0.36, -0.049), "TE": (0.26, -0.048)}
MAX_RANK = 150.0
UNLISTED_GAP = 10              # missing from a snapshot: last listed + this
STAR_MIN = 1.0                 # healthy-chart points a shock must add before views star him as hype (`reason`)
RECENT_WEEKS = 3               # tie-break between unlisted teammates: mean projection over these weeks
# the share of the log gap to `toward` the market moved beyond the chart, fitted on 2018-25 (replay_shock_eval)
GAMMA = {
    ("teammate_out", "RB"): 0.15, ("teammate_out", "WR"): 0.01, ("teammate_out", "TE"): 0.09,
    ("teammate_left", "RB"): 0.17, ("teammate_left", "WR"): 0.17, ("teammate_left", "TE"): 0.17,
    ("starter_back", "RB"): 0.48, ("starter_back", "WR"): 0.2, ("starter_back", "TE"): 0.36,
    ("role_won", "RB"): 0.02, ("role_won", "WR"): 0.05, ("role_won", "TE"): 0.05,
    ("role_lost", "RB"): 0.0, ("role_lost", "WR"): 0.0, ("role_lost", "TE"): 0.01,
}


@dataclass(frozen=True)
class Shock:
    player_id: int
    position: str
    kind: str
    week: int                  # first week it is known: the fade counts from here
    anchor: float              # his market rank before it
    toward: float              # the rank it points him to
    up: bool
    other: int | None = None   # the teammate behind it (role events: None)
    before: int = 0            # the anchor is the market before this week


def _absent(data: SeasonData, pid: int, week: int) -> bool | None:
    """True when out that week (a hard tag, even on a bye, or no projection), None on a bye without one."""
    tag = data.injuries.get((pid, week))
    if tag is not None and tag.hard:
        return True
    return None if data.on_bye(pid, week) else data.projection(pid, week) <= 0


def absence(data: SeasonData, pid: int, week: int) -> int | None:
    """First week of the absence he is in at `week` (untagged byes skipped); None if he plays or has an
    untagged bye that week, or was never projected before it."""
    if not _absent(data, pid, week):
        return None
    since = week
    for w in range(week - 1, 0, -1):
        state = _absent(data, pid, w)
        if state is False:
            return since
        since = w if state else since
    return None


@lru_cache(maxsize=512)
def market_ranks(data: SeasonData, before: int) -> Mapping[int, float]:
    """{id: position rank} of the market before week `before`: the latest expert snapshot of an earlier week,
    else the order of his mean projection over the weeks before it (week 1's at the start)."""
    snaps = [w for (_, w) in data.expert_ranks if w < before]
    ranks: dict[int, float] = {}
    if snaps:
        latest = max(snaps)
        listed = {pid: r for (pid, w), r in data.expert_ranks.items() if w == latest and pid in data.players}
        last: dict[str, int] = defaultdict(int)
        for pid, r in listed.items():
            last[data.players[pid].position] = max(last[data.players[pid].position], r)
        for pid, p in data.players.items():
            if p.position in POSITIONS:
                ranks[pid] = float(listed.get(pid, last[p.position] + UNLISTED_GAP))
        return MappingProxyType(ranks)
    weeks = range(max(1, before - 4), max(2, before))
    by_position: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for pid, p in data.players.items():
        found = [data.projection(pid, w) for w in weeks if data.projection(pid, w) > 0]
        if p.position in POSITIONS and found:
            by_position[p.position].append((-sum(found) / len(found), pid))
    for group in by_position.values():
        ranks.update({pid: float(n) for n, (_, pid) in enumerate(sorted(group), start=1)})
    return MappingProxyType(ranks)


@lru_cache(maxsize=256)
def _teams(data: SeasonData, week: int) -> Mapping[tuple[str, str], tuple[int, ...]]:
    """{(team, position): ids} as known before `week`."""
    found: dict[tuple[str, str], list[int]] = defaultdict(list)
    for pid, p in data.players.items():
        team = data.team_at(pid, week)
        if p.position in POSITIONS and team is not None:
            found[(team, p.position)].append(pid)
    return MappingProxyType({key: tuple(ids) for key, ids in found.items()})


def _next_in_line(data: SeasonData, gone: int, team: str, week: int) -> tuple[int, Mapping[int, float]] | None:
    """(the best-ranked teammate below `gone` at his position before `week`, those ranks) if `gone` mattered."""
    ranks, position = market_ranks(data, week), data.players[gone].position
    mine = ranks.get(gone)
    if mine is None or mine > RELEVANT[position]:
        return None
    below = [pid for pid in _teams(data, week).get((team, position), ()) if pid != gone and ranks.get(pid, 0) > mine]
    return (min(below, key=lambda pid: (ranks[pid], -_recent(data, pid, week), pid)), ranks) if below else None


def _recent(data: SeasonData, pid: int, week: int) -> float:
    """His mean projection over the RECENT_WEEKS before `week` (week 1's at the start): breaks ties between
    teammates the snapshot leaves unlisted."""
    weeks = range(max(1, week - RECENT_WEEKS), max(2, week))
    return sum(data.projection(pid, w) for w in weeks) / len(weeks)


def _teammate_out(data: SeasonData, week: int) -> list[Shock]:
    found = []
    for gone in market_ranks(data, week):
        since = absence(data, gone, week)
        if since is None or since < 2 or week - since >= FADE_WEEKS:
            continue
        team = data.team_at(gone, since)
        pick = _next_in_line(data, gone, team, since) if team else None
        if pick:
            pid, ranks = pick
            found.append(Shock(pid, data.players[pid].position, "teammate_out", since, ranks[pid], ranks[gone],
                               True, gone, since))
    return found


def _teammate_left(data: SeasonData, week: int) -> list[Shock]:
    found = []
    for gone, games in data.team_by_week.items():
        moves = [(w2, t1) for (_, t1), (w2, t2) in zip(games, games[1:]) if t1 != t2 and w2 < week]
        if not moves or data.players.get(gone) is None or data.players[gone].position not in POSITIONS:
            continue
        switched, old = moves[-1]
        if week - (switched + 1) >= FADE_WEEKS:
            continue
        pick = _next_in_line(data, gone, old, switched)
        if pick:
            pid, ranks = pick
            found.append(Shock(pid, data.players[pid].position, "teammate_left", switched + 1, ranks[pid],
                               ranks[gone], True, gone, switched))
    return found


def _starter_back(data: SeasonData, week: int) -> list[Shock]:
    found = []
    for back in market_ranks(data, week):
        if _absent(data, back, week):                       # out again: teammate_out speaks for this week
            continue
        for returned in range(week, max(1, week - FADE_WEEKS + 1) - 1, -1):
            if _absent(data, back, returned) is not False:
                continue
            last = next((w for w in range(returned - 1, 0, -1) if _absent(data, back, w) is not None), 0)
            since = absence(data, back, last) if last else None
            gap = sum(bool(_absent(data, back, w)) for w in range(since, returned)) if since else 0
            team = data.team_at(back, returned)
            hurt = since is not None and any(data.injuries.get((back, w)) for w in range(since, returned))
            pick = _next_in_line(data, back, team, since) if hurt and since >= 2 and gap >= MIN_ABSENCE and team else None
            if pick:
                pid, old = pick
                now = market_ranks(data, returned).get(pid, old[pid])
                if now < old[pid]:
                    found.append(Shock(pid, data.players[pid].position, "starter_back", returned, now, old[pid],
                                       False, back, returned))
            break                                            # only his latest game counts
    return found


def shares(data: SeasonData, week: int) -> dict[int, float]:
    """{id: his share of his team's opportunities in games before `week`}: RB carries + targets among the
    team's backs, WR/TE mean target share."""
    team_of = {pid: dict(games) for pid, games in data.team_by_week.items()}
    own: dict[int, float] = defaultdict(float)
    games: dict[int, list[tuple[str | None, int]]] = defaultdict(list)
    team_total: dict[tuple[str | None, int], float] = defaultdict(float)
    target_shares: dict[int, list[float]] = defaultdict(list)
    for (pid, w), use in data.usage.items():
        player = data.players.get(pid)
        if w >= week or player is None or player.position not in POSITIONS:
            continue
        if player.position == "RB":
            game = (team_of.get(pid, {}).get(w), w)
            own[pid] += use.carries + use.targets
            games[pid].append(game)
            team_total[game] += use.carries + use.targets
        elif use.target_share is not None:
            target_shares[pid].append(use.target_share)
    out = {pid: sum(found) / len(found) for pid, found in target_shares.items()}
    for pid, opportunities in own.items():
        total = sum(team_total[game] for game in games[pid])
        out[pid] = opportunities / total if total else 0.0
    return out


def implied_share(position: str, rank: float) -> float:
    a, b = SHARE_FIT[position]
    return min(0.95, max(0.02, a + b * math.log(max(1.0, rank))))


def implied_rank(position: str, share: float) -> float:
    a, b = SHARE_FIT[position]
    return min(MAX_RANK, max(1.0, math.exp((share - a) / b))) if b else MAX_RANK


@lru_cache(maxsize=64)
def _role(data: SeasonData, at: int) -> tuple[Shock, ...]:
    """Role events from the games before week `at` (data as of `at` or later)."""
    pre, anchors = market_ranks(data, 1), market_ranks(data, at)
    got = shares(data, at)
    gaps = {pid: share - implied_share(data.players[pid].position, pre[pid]) for pid, share in got.items()
            if pid in pre and pid in anchors}
    winners = {pid for pid, gap in gaps.items() if gap >= ROLE_MARGIN[data.players[pid].position]}
    won_teams = {(data.team_at(pid, at), data.players[pid].position) for pid in winners}
    found = []
    for pid, gap in gaps.items():
        position = data.players[pid].position
        lost = gap <= -ROLE_MARGIN[position] and (data.team_at(pid, at), position) in won_teams
        if pid in winners or lost:
            found.append(Shock(pid, position, "role_won" if pid in winners else "role_lost", at, anchors[pid],
                               implied_rank(position, got[pid]), pid in winners, None, at))
    return tuple(found)


def _roles(data: SeasonData, week: int) -> list[Shock]:
    first: dict[int, Shock] = {}
    for at in ROLE_WEEKS:
        if at <= week < at + FADE_WEEKS:
            for shock in _role(data.as_of(at), at):
                first.setdefault(shock.player_id, shock)
    return list(first.values())


@lru_cache(maxsize=128)
def events(data: SeasonData, week: int) -> tuple[Shock, ...]:
    """Every live shock at `week` (seed-free; teammate_out's size waits for the news)."""
    if week < 2:
        return ()
    data = data.as_of(week)
    return tuple([*_teammate_out(data, week), *_teammate_left(data, week), *_starter_back(data, week),
                  *_roles(data, week)])


def clear_cache() -> None:
    events.cache_clear()
    _role.cache_clear()
    _teams.cache_clear()
    market_ranks.cache_clear()


def target_rank(shock: Shock, current: float, missed: float = 1.0) -> float:
    """The rank the market moves him to from his chart's `current` rank: GAMMA x m of the log gap to `toward`."""
    gamma = GAMMA[(shock.kind, shock.position)] * missed
    return math.exp(math.log(current) + gamma * (math.log(shock.toward) - math.log(current)))


def moves(data: SeasonData, week: int, availability: Mapping[int, float], healthy: Mapping[int, float],
          rank: Mapping[int, float], value: Callable[[str, float], float]) -> dict[int, tuple[float, Shock]]:
    """{id: (signed healthy-chart points, the shock)}, the largest move per player. `availability` is the
    share of his team's games left a player plays (his news report's when he has one), `healthy` the healthy
    chart before shocks and `rank` his position rank on it, `value(position, rank)` a rank's chart value."""
    best: dict[int, tuple[float, Shock]] = {}
    for shock in events(data, week):
        pid = shock.player_id
        missed = 1.0 - availability.get(shock.other, 1.0) if shock.kind == "teammate_out" else 1.0
        current = rank.get(pid)
        if missed < MIN_MISSED or current is None or (shock.toward < current) != shock.up:
            continue
        fade = 1.0 - (week - shock.week) / FADE_WEEKS
        moved = value(shock.position, target_rank(shock, current, missed)) / value(shock.position, current)
        delta = fade * healthy[pid] * (moved - 1.0)
        if delta != 0 and abs(delta) > abs(best.get(pid, (0.0,))[0]):
            best[pid] = (delta, shock)
    return best


def reason(shock: Shock, other: str | None, missed: float) -> str | None:
    """Public words for why his role jumped (the views' hype star): `other` the teammate's name, `missed` the
    share of the remaining games that teammate misses; None for a move down."""
    who = other or "a teammate"
    if not shock.up:
        return None
    if shock.kind == "teammate_out":
        return f"{who} went out" + (" for the season" if missed >= 1.0 else "")
    if shock.kind == "teammate_left":
        return f"{who} left the team"
    return "he won the backfield" if shock.position == "RB" else "he won a bigger target share"
