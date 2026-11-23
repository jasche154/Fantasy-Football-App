"""Replay League: the league chat. CPU managers talk; the user reads (one-way).

Lines come from the checked-in bank `replay_chat_lines.json`: {kind: {voice: [line, ...]}}, one voice per
persona (replay_personas.PERSONAS), so a manager sounds the same all season. A line's placeholders are the
ones its kind provides (PLACEHOLDERS). The rendered text is copied into `State.chat`, so editing the bank
never rewrites an old chat. Within a game a (kind, voice) bucket never repeats a line until every line in it
has been used (`Chat.line`); the choice is seeded (`rs.rng`), so a reload never re-rolls.

When lines are posted (replay_game calls these; each reads only what is known when it posts):
- `after_week`, once a week is scored: every CPU may say one thing about the week just played, from that
  week's result and the standings with and without it. Kinds by priority: champion / runner_up (the final),
  clinch / eliminated (newly, regular season), upset (beat a team UPSET_GAP or more places above him),
  streak_win / streak_loss (STREAK games), then one of boom (a starter's huge game), bust (a starter's dud;
  never a player who is hurt or did not score) and the result itself: close_* under CLOSE_MARGIN points,
  blowout_* from BLOWOUT_MARGIN. The user's opponent always talks to the user (a kind that names him, by
  nearest margin when the game was in between); the others post with POST_CHANCE; at most MAX_WEEK_LINES.
- `shop_lines`: a worried manager's direct message when he starts shopping a player to the user (replay_mood).
- `trade_reactions`: a third manager may react to a new trade (at most MAX_REACTIONS a week).
- `label_offers`: every new CPU offer to the user gets his one-line reason (Offer.message): worried about a
  player he sends, high on a player he asks for, short at a position, a counter, else a plain value pitch.
Text is built from team names, player names and numbers only. Pure.
"""
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services import replay_mood, replay_state as rs
from services.replay_pool import SKILL
from services.replay_state import Chat, Offer, Settings, Shop, State, Team, WeekResult
from services.replay_values import Knowledge

BANK_FILE = Path(__file__).with_name("replay_chat_lines.json")
RESULT = ("me", "you", "margin", "my_score", "their_score", "week")
PLACEHOLDERS = MappingProxyType({
    "blowout_win": RESULT, "close_win": RESULT, "close_loss": RESULT, "blowout_loss": RESULT, "upset": RESULT,
    "champion": RESULT, "runner_up": RESULT,
    "boom": ("me", "player", "points", "week"), "bust": ("me", "player", "points", "week"),
    "streak_win": ("me", "streak", "week"), "streak_loss": ("me", "streak", "week"),
    "clinch": ("me", "week"), "eliminated": ("me", "week"),
    "trade_reaction": ("me", "you", "them", "player", "week"),
    "shop": ("me", "you", "player", "week"),
    "offer_worried": ("me", "you", "player"), "offer_high": ("me", "you", "player"),
    "offer_need": ("me", "you", "position"), "offer_value": ("me", "you", "player"), "offer_counter": ("me", "you"),
})
KINDS = tuple(PLACEHOLDERS)
VOICES = ("balanced", "panicky", "shark", "steady", "gambler")      # replay_personas.PERSONAS
QUIET = MappingProxyType({"shop": ("shark", "steady")})            # voices that never post that kind (no bucket)
MIN_LINES = 12                  # per (kind, voice)
MIN_LINES_COMMON = 20           # for the kinds below, which come up every week
COMMON = ("blowout_win", "close_win", "close_loss", "blowout_loss", "boom", "bust")
MAX_CHARS = 140
CLOSE_MARGIN = 10.0             # points: a game decided by less is close
BLOWOUT_MARGIN = 25.0           # and by this or more a blowout
UPSET_GAP = 4                   # places in the table before the week
UPSET_FROM_WEEK = 4
STREAK = 3
BOOM_POINTS, BOOM_RATE = 25.0, 1.75     # a starter's points >= both 25 and 1.75 x his expected rate
BUST_RATE, BUST_MIN_PPG = 0.4, 10.0     # <= 0.4 x a rate of at least 10 (and more than 0)
MAX_WEEK_LINES = 5
MAX_REACTIONS = 2
REACT_CHANCE = {"cpu": 0.35, "user": 0.75}      # a trade between two CPUs / with the user
POST_CHANCE = {"champion": 1.0, "runner_up": 1.0, "clinch": 0.9, "eliminated": 0.6, "upset": 0.7,
               "streak_win": 0.45, "streak_loss": 0.45}
OTHER_CHANCE = 0.3              # boom, bust and plain results
TO_OPPONENT = ("champion", "runner_up", "upset", "blowout_win", "close_win", "close_loss", "blowout_loss")


@lru_cache(maxsize=1)
def bank() -> Mapping[str, Mapping[str, tuple[str, ...]]]:
    """The line bank, read once."""
    raw = json.loads(BANK_FILE.read_text(encoding="utf-8"))
    return MappingProxyType({kind: MappingProxyType({voice: tuple(lines) for voice, lines in voices.items()})
                             for kind, voices in raw.items()})


def _voice(team: Team, kind: str) -> str:
    return team.persona if team.persona in bank()[kind] else "balanced"


def post(state: State, team: Team, kind: str, values: Mapping[str, object], seed: int, *, to_team: int | None = None,
         player_ids: Sequence[int] = (), week: int | None = None) -> State:
    """The state with one line from `team` added (dated `week`, default the current one): the least-used line
    of its (kind, voice) bucket, seeded."""
    voice = _voice(team, kind)
    lines, voices = bank()[kind][voice], {t.id: _voice(t, kind) for t in state.teams}
    used = [0] * len(lines)
    for chat in state.chat:
        if chat.kind == kind and voices.get(chat.team_id) == voice and 0 <= chat.line < len(lines):
            used[chat.line] += 1
    fresh = [n for n, count in enumerate(used) if count == min(used)]
    line = fresh[int(rs.rng(seed, "chat-line", len(state.chat), team.id, kind).integers(len(fresh)))]
    text = lines[line].format_map({key: values[key] for key in PLACEHOLDERS[kind]})
    chat = Chat(id=len(state.chat) + 1, week=state.week if week is None else week, team_id=team.id, to_team=to_team, kind=kind, text=text,
                player_ids=tuple(player_ids), line=line)
    return replace(state, chat=(*state.chat, chat))


# ---------------- after a week is scored ----------------

def _wins(state: State) -> dict[int, float]:
    return {row["team_id"]: row["wins"] + 0.5 * row["ties"] for row in rs.standings(state)}


def _playoff_status(state: State, settings: Settings) -> tuple[set[int], set[int]]:
    """(clinched, eliminated) on the regular-season table as it stands; a tie on wins counts against a team."""
    played = sum(not r.playoff for r in state.results)
    left = settings.regular_season_weeks - played
    rows, spots = rs.standings(state), settings.playoff_teams
    if left == 0:
        return ({r["team_id"] for r in rows if r["rank"] <= spots}, {r["team_id"] for r in rows if r["rank"] > spots})
    wins = _wins(state)
    clinched = {t for t in wins if sum(o != t and wins[o] + left >= wins[t] for o in wins) < spots}
    out = {t for t in wins if sum(wins[o] > wins[t] + left for o in wins) >= spots}
    return clinched, out


def _streak(state: State, team_id: int) -> int:
    """+n for n straight wins, -n for n straight losses, ending with the latest result (a tie ends it)."""
    run = 0
    for result in reversed(state.results):
        pair = next((p for p in result.matchups if team_id in p), None)
        if pair is None:
            continue
        other = pair[0] if pair[1] == team_id else pair[1]
        mine, theirs = result.scores[team_id], result.scores[other]
        step = 1 if mine > theirs else -1 if mine < theirs else 0
        if step == 0 or (run and (run > 0) != (step > 0)):
            break
        run += step
    return run


def _stars(result: WeekResult, know: Knowledge, team_id: int) -> tuple[tuple[int, float] | None, tuple[int, float] | None]:
    """(his starter with the biggest boom, his worst healthy bust) in that week, as (player id, points)."""
    starters = [pid for ids in result.starters.get(team_id, {}).values() for pid in ids
                if pid in know.by_id and know.by_id[pid].position in SKILL]
    booms, busts = [], []
    for pid in starters:
        value, points = know.by_id[pid], result.points.get(pid, 0.0)
        rate = value.ros_ppg
        if points >= max(BOOM_POINTS, BOOM_RATE * rate):
            booms.append((points, pid))
        hurt = value.out or value.injury is not None
        if rate >= BUST_MIN_PPG and 0 < points <= BUST_RATE * rate and not hurt:
            busts.append((points / rate, pid))
    boom = max(booms, default=None)
    bust = min(busts, default=None)
    return ((boom[1], boom[0]) if boom else None,
            (bust[1], result.points[bust[1]]) if bust else None)


def _game_lines(state: State, settings: Settings, know: Knowledge, before: State, result: WeekResult,
                seed: int) -> list[tuple[int, Team, str, dict, tuple[int, ...]]]:
    """Every CPU's candidate line for the week: (order, team, kind, values, player ids), best first."""
    week, user = result.week, rs.user_team(state)
    seat = {row["team_id"]: row["rank"] for row in rs.standings(before)}
    now_in, now_out = _playoff_status(state, settings) if not result.playoff else (set(), set())
    was_in, was_out = _playoff_status(before, settings) if not result.playoff else (set(), set())
    final = state.phase == "complete" and state.champion is not None
    found = []
    for pair in result.matchups:
        for me_id in pair:
            me = rs.team(state, me_id)
            if me.is_user:
                continue
            you_id = pair[0] if pair[1] == me_id else pair[1]
            mine, theirs = result.scores[me_id], result.scores[you_id]
            margin = abs(mine - theirs)
            values = {"me": me.name, "you": rs.team(state, you_id).name, "margin": f"{margin:.1f}",
                      "my_score": f"{mine:.1f}", "their_score": f"{theirs:.1f}", "week": week}
            to_user = you_id == user.id
            kinds: list[tuple[str, dict, tuple[int, ...]]] = []
            if final and pair == result.matchups[0]:
                kinds.append(("champion" if state.champion == me_id else "runner_up", values, ()))
            if me_id in now_in - was_in:
                kinds.append(("clinch", values, ()))
            if me_id in now_out - was_out:
                kinds.append(("eliminated", values, ()))
            won = mine > theirs
            if won and not result.playoff and week >= UPSET_FROM_WEEK and seat[me_id] - seat[you_id] >= UPSET_GAP:
                kinds.append(("upset", values, ()))
            streak = _streak(state, me_id)
            if abs(streak) >= STREAK:
                kinds.append(("streak_win" if streak > 0 else "streak_loss", {**values, "streak": abs(streak)}, ()))
            boom, bust = _stars(result, know, me_id)
            dice = rs.rng(seed, "chat", week, me_id)
            lesser = []
            if boom:
                lesser.append(("boom", {**values, "player": know.by_id[boom[0]].name, "points": f"{boom[1]:.1f}"},
                               (boom[0],)))
            if bust:
                lesser.append(("bust", {**values, "player": know.by_id[bust[0]].name, "points": f"{bust[1]:.1f}"},
                               (bust[0],)))
            if mine != theirs:
                close = margin < (CLOSE_MARGIN if not to_user else (CLOSE_MARGIN + BLOWOUT_MARGIN) / 2)
                if to_user or close or margin >= BLOWOUT_MARGIN:
                    kind = ("close_" if close else "blowout_") + ("win" if won else "loss")
                    lesser.append((kind, values, ()))
            if lesser:
                kinds.append(lesser[int(dice.integers(len(lesser)))])
            if to_user:                     # he talks to the user about their game (a tie: whatever he has)
                kinds = [k for k in (*kinds, *lesser) if k[0] in TO_OPPONENT] or kinds
            if not kinds:
                continue
            kind, picked, players = kinds[0]
            chance = 1.0 if to_user else POST_CHANCE.get(kind, OTHER_CHANCE)
            if dice.random() < chance:
                found.append((0 if to_user else 1 + KINDS.index(kind), me, kind, picked, players))
    return sorted(found, key=lambda item: (item[0], item[1].id))


def after_week(state: State, settings: Settings, know: Knowledge, seed: int) -> State:
    """The chat about the week just played (`state.results[-1]`). `know` is what is known now, after it."""
    if not state.results:
        return state
    result = state.results[-1]
    before = replace(state, results=state.results[:-1])
    user = rs.user_team(state)
    for _, team, kind, values, players in _game_lines(state, settings, know, before, result, seed)[:MAX_WEEK_LINES]:
        opponent = next((p for p in result.matchups if team.id in p and user.id in p), None)
        to_team = user.id if opponent and kind in TO_OPPONENT else None
        state = post(state, team, kind, values, seed, to_team=to_team, player_ids=players, week=result.week)
    return state


# ---------------- shops, trades and offers ----------------

def shop_lines(state: State, shops: Sequence[Shop], know: Knowledge, seed: int) -> State:
    """A direct message to the user from each manager who just started shopping a player."""
    user = rs.user_team(state)
    for shop in shops:
        team = rs.team(state, shop.team_id)
        values = {"me": team.name, "you": user.name, "player": know.by_id[shop.player_id].name, "week": state.week}
        state = post(state, team, "shop", values, seed, to_team=user.id, player_ids=(shop.player_id,))
    return state


def trade_reactions(state: State, offer_ids: Sequence[int], know: Knowledge, seed: int) -> State:
    """A third CPU may react to each accepted trade among `offer_ids` (at most MAX_REACTIONS a week)."""
    wanted = set(offer_ids)
    for offer in (o for o in state.offers if o.id in wanted):
        done = sum(chat.kind == "trade_reaction" and chat.week == state.week for chat in state.chat)
        if offer.status != "accepted" or done >= MAX_REACTIONS:
            continue
        sides = {offer.from_team, offer.to_team}
        user_in = any(rs.team(state, t).is_user for t in sides)
        dice = rs.rng(seed, "react", state.week, offer.id)
        watchers = [t for t in state.teams if not t.is_user and t.id not in sides]
        if not watchers or dice.random() >= REACT_CHANCE["user" if user_in else "cpu"]:
            continue
        moved = [(know.by_id[pid].chart, pid, offer.to_team if pid in offer.give else offer.from_team)
                 for pid in (*offer.give, *offer.get) if pid in know.by_id]
        if not moved:
            continue
        _, star, receiver = max(moved)
        other = offer.from_team if receiver == offer.to_team else offer.to_team
        me = watchers[int(dice.integers(len(watchers)))]
        values = {"me": me.name, "you": rs.team(state, receiver).name, "them": rs.team(state, other).name,
                  "player": know.by_id[star].name, "week": state.week}
        state = post(state, me, "trade_reaction", values, seed, player_ids=(star,))
    return state


def _offer_kind(state: State, know: Knowledge, offer: Offer) -> tuple[str, int | None]:
    """Why this CPU (offer.from_team) makes the offer: (kind, the player the line names)."""
    cpu = rs.team(state, offer.from_team)
    shopped = rs.markdowns(state, cpu.id)
    worried = [pid for pid in offer.give if pid in shopped or (
        (felt := replay_mood.feeling(know.by_id[pid], cpu.persona, cpu.hype)) and felt.kind == "worried")]
    if worried:
        return "offer_worried", worried[0]
    if offer.counter_to is not None:
        return "offer_counter", None
    loved = [pid for pid in offer.get if (felt := replay_mood.feeling(know.by_id[pid], cpu.persona, cpu.hype))
             and felt.kind == "high"]
    if loved:
        return "offer_high", loved[0]
    sent = {know.by_id[pid].position for pid in offer.give}
    needs = [pid for pid in offer.get if know.by_id[pid].position not in sent]
    if needs:
        return "offer_need", max(needs, key=lambda pid: (know.by_id[pid].chart, -pid))
    return "offer_value", max(offer.get, key=lambda pid: (know.by_id[pid].chart, -pid))


def label_offers(state: State, know: Knowledge, seed: int) -> State:
    """Every pending CPU offer to the user without a message gets one in its sender's voice (not in the feed)."""
    user = rs.user_team(state)
    for offer in state.offers:
        if offer.to_team != user.id or offer.status != "pending" or offer.message:
            continue
        cpu = rs.team(state, offer.from_team)
        kind, pid = _offer_kind(state, know, offer)
        named = know.by_id[pid] if pid is not None else None
        values = {"me": cpu.name, "you": user.name, "player": named.name if named else "",
                  "position": named.position if named else ""}
        posted = post(state, cpu, kind, values, seed, to_team=user.id)
        text = posted.chat[-1].text
        # the line is used up in the bucket (kept in chat history as kind offer_*) so offers don't repeat either
        state = replace(posted, offers=tuple(replace(o, message=text) if o.id == offer.id else o for o in posted.offers))
    return state


def chat_for(state: State) -> tuple[Chat, ...]:
    """The feed the user reads: everything but offer messages (those show on the offer)."""
    return tuple(chat for chat in state.chat if not chat.kind.startswith("offer_"))
