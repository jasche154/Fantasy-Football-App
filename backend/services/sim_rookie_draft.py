"""Simulated seasons: a seeded NFL draft for a future season's rookie class (sim_data/offseason_<season>.json).

The file lists the prospects with their consensus projected pick (`pick`, the prospect's rank) and a mock
landing team. Unless a prospect is `lock`ed to that team and pick (a hand override), where he goes depends on
the league's seed, decided once when the league is built (sim_setup._future_base):

1. `final_wins`: the season so far is real (every final game); each game left is won by the home team with
   P = Phi((rating_home - rating_away + HOME_EDGE) / MARGIN_SD), a team's rating its points differential a
   game shrunk by RATING_PRIOR_GAMES pseudo-games of 0.
2. `draft_order`: fewest wins picks first, ties by a seeded coin. Shortcut: playoff results, the NFL's
   strength-of-schedule tiebreak, traded and compensatory picks are not modeled; round r's picks are
   32 x (r - 1) + the round-1 order.
3. `draft`: each prospect's board spot is his rank x lognormal(BOARD_SD) (consensus boards miss real picks
   by about a third of the pick in the first rounds). At each slot the team on the clock takes the best
   prospect whose board spot has come up and that it needs (`needs`); one it does not need it takes with
   `take_anyway` (near 1 for a consensus top pick, TAKE_WITHOUT_NEED from the end of round 1), except a QB in the first QB_NEED_ROUNDS rounds, which slides to a team that needs one.
   A team needs a QB unless its August starter (sim_next_year.restore_starters, after the moves) is
   established (starts weight STARTER_ROLE+, ESTABLISHED_GAMES games
   of evidence, quality ESTABLISHED_QUALITY+), and an RB / WR / TE when its best one (WR: second-best) has
   less volume than the league's median team. A drafted position is no longer needed. A prospect nobody took
   goes with the first open slot after the last one.

The drafted rookie keeps everything from the file but `team` and `pick`, so his pick drives his role, the
market's rookie premium (sim_next_year) and his place on the depth chart. Pure; every draw comes from
replay_state.rng(seed, "draft", ...).
"""
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from statistics import median

from services import replay_state
from services.schedule_strength import Game
from services.sim_offseason import MAX_PICK, Rookie
from services.sim_schedule import TEAMS
from services.sim_world import STARTER_ROLE, World

HOME_EDGE = 1.5                 # points
MARGIN_SD = 13.5                # an NFL game's margin around the line
RATING_PRIOR_GAMES = 4.0
BOARD_SD = 0.3
TAKE_WITHOUT_NEED = 0.35
QB_NEED_ROUNDS = 2
ESTABLISHED_GAMES = 8.0
ESTABLISHED_QUALITY = 0.95
VOLUME_RANK = {"RB": 0, "WR": 1, "TE": 0}     # the player whose volume measures a room (0 = its best)


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def final_wins(games: Sequence[Game], seed: int) -> dict[str, float]:
    """{team: regular-season wins} with the games still to play simulated (a tie is half a win)."""
    diff, played = dict.fromkeys(TEAMS, 0.0), dict.fromkeys(TEAMS, 0)
    wins = dict.fromkeys(TEAMS, 0.0)
    for g in games:
        if g.is_final and g.home_team in wins and g.away_team in wins:
            margin = g.home_score - g.away_score
            diff[g.home_team] += margin
            diff[g.away_team] -= margin
            played[g.home_team] += 1
            played[g.away_team] += 1
            wins[g.home_team] += 1.0 if margin > 0 else 0.5 if margin == 0 else 0.0
            wins[g.away_team] += 1.0 if margin < 0 else 0.5 if margin == 0 else 0.0
    rating = {t: diff[t] / (played[t] + RATING_PRIOR_GAMES) for t in TEAMS}
    dice = replay_state.rng(seed, "draft", "standings")
    for g in sorted((g for g in games if not g.is_final), key=lambda g: (g.week, g.home_team)):
        if g.home_team in wins and g.away_team in wins:
            home = dice.random() < _phi((rating[g.home_team] - rating[g.away_team] + HOME_EDGE) / MARGIN_SD)
            wins[g.home_team if home else g.away_team] += 1.0
    return wins


def draft_order(wins: Mapping[str, float], seed: int) -> list[str]:
    """Round 1's order: fewest wins first, ties by a seeded coin."""
    coin = dict(zip(sorted(wins), replay_state.rng(seed, "draft", "ties").random(len(wins))))
    return sorted(wins, key=lambda t: (wins[t], coin[t]))


def needs(world: World, moves: Mapping[int, str | None] | None = None,
          season_starts: Mapping[int, Sequence[tuple[int, str | None]]] | None = None) -> dict[str, set[str]]:
    """{team: positions it needs} from the belief at the end of the season, after the offseason `moves`; with
    `season_starts` (sim_next_year.starts_by_team) each team's August starter is restored first, so a starter
    whose decayed starts weight is low (hurt late) does not leave his team needing a QB."""
    from services.sim_next_year import restore_starters, volume

    players = {pid: replace(p, team=team) for pid, p in world.players.items()
               for team in [(moves or {}).get(pid, p.team)] if team is not None}
    if season_starts is not None:
        restore_starters(players, season_starts)
    rooms: dict[tuple[str, str], list] = {}
    for p in players.values():
        rooms.setdefault((p.team, p.position), []).append(p)
    out = {team: set() for team in TEAMS}
    for team in TEAMS:
        qbs = rooms.get((team, "QB"), [])
        if not any(q.starts >= STARTER_ROLE and q.games >= ESTABLISHED_GAMES and q.quality >= ESTABLISHED_QUALITY
                   for q in qbs):
            out[team].add("QB")
    for position, rank in VOLUME_RANK.items():
        level = {}
        for team in TEAMS:
            ranked = sorted((volume(p) for p in rooms.get((team, position), [])), reverse=True)
            level[team] = ranked[rank] if len(ranked) > rank else 0.0
        middle = median(level.values())
        for team in TEAMS:
            if level[team] < middle:
                out[team].add(position)
    return out


def take_anyway(rank: int) -> float:
    """The chance a team takes a non-QB prospect of consensus `rank` it does not need: near 1 for the top
    picks (the best player available), TAKE_WITHOUT_NEED from the end of round 1."""
    return max(TAKE_WITHOUT_NEED, 1.0 - rank / 32.0)


def draft(rookies: Sequence[Rookie], order: Sequence[str], need: Mapping[str, set[str]], seed: int) -> list[Rookie]:
    """The class with each unlocked prospect's drafted team and pick (see the module docstring)."""
    dice = replay_state.rng(seed, "draft", "board")
    board_noise = dice.lognormal(0.0, BOARD_SD, len(rookies))
    takes = dice.random(MAX_PICK)
    locked = [r for r in rookies if r.lock]
    taken_slots = {r.pick for r in locked}
    board = sorted(((r.pick * noise, r.pick, r) for r, noise in zip(rookies, board_noise) if not r.lock),
                   key=lambda row: row[:2])
    wants = {team: set(positions) for team, positions in need.items()}
    out, last = list(locked), 0
    for slot in range(1, MAX_PICK + 1):
        if not board:
            break
        if slot in taken_slots:
            continue
        team, rnd = order[(slot - 1) % len(order)], (slot - 1) // len(order) + 1
        for i, (spot, _, prospect) in enumerate(board):
            if spot > slot:
                break
            wanted = prospect.position in wants.get(team, ())
            if not wanted and (prospect.position == "QB" and rnd <= QB_NEED_ROUNDS
                               or takes[slot - 1] >= take_anyway(prospect.pick)):
                continue
            out.append(replace(prospect, team=team, pick=slot))
            wants.get(team, set()).discard(prospect.position)
            del board[i]
            last = slot
            break
    free = (s for s in range(last + 1, 10 * MAX_PICK) if s not in taken_slots)
    for _, _, prospect in board:                       # nobody took him: the first open slots after the last pick
        slot = next(free)
        out.append(replace(prospect, team=order[(slot - 1) % len(order)], pick=slot))
    return sorted(out, key=lambda r: r.pick)


def run(rookies: Sequence[Rookie], games: Sequence[Game], world: World, moves: Mapping[int, str | None],
        seed: int, season_starts: Mapping[int, Sequence[tuple[int, str | None]]] | None = None) -> list[Rookie]:
    """The seeded draft of `rookies`: standings from `games` (the season so far), needs from `world`."""
    return draft(rookies, draft_order(final_wins(games, seed), seed), needs(world, moves, season_starts), seed)
