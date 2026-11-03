"""Roster check for Ask AI answers: moves must fit who is actually on which team in the league.

"Pick up / add / claim / bid on X" is wrong when X is already on a roster in the league; "drop /
bench / start X" is wrong when X isn't on the user's roster. Verb detection is deliberately narrow
(an advice-shaped clause that starts with one of those verbs, followed straight by one player's
name), so a description like "Rival will start X" or "don't drop X" is never flagged. No LLM call.
"""
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.labels import label
from services.player_index import normalize

ADD_VERBS = ("pick up", "add", "claim", "bid on")
MINE_VERBS = ("drop", "bench", "start")
# What may come between the start of a clause and the verb: advice wording only. Anything else
# ("Rival will", "don't", "if you") makes it a description or a negation, which is never flagged.
_ADVICE = (r"i'd|i would|you should|you could|you can|you need to|you want to|you must|should|go ahead and|"
           r"consider|i recommend|i suggest|recommend|suggest|also|then|so|and|instead|definitely|still|just|"
           r"lean|my advice is to|the move is to|best move is to|i'd also|you'll want to")
_ADVICE_PREFIX = re.compile(rf"^(?:(?:{_ADVICE})\b[\s,]*)*$")
_ACTION = re.compile(r"(?<![\w'])(?P<verb>pick\s+up|add|claim|bid\s+on|drop|bench|start)(?:\s*:)?\s+"
                     r"(?P<rest>[^.;!?\n,()]{1,60})", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[.;:!?\n,()•]|(?:^|\s)-\s")
# A list marker that opens the clause ("- ", "* ", "• ", "1. ", "2) "): layout, not wording.
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s*")


class _Index(Protocol):
    """What the check needs from services.player_index.PlayerIndex."""
    def mentions(self, text: str) -> Sequence: ...

    def resolve(self, token: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class LeagueRosters:
    league: str                          # label, for messages
    mine: frozenset[str]                 # normalized names on the user's team
    owners: Mapping[str, str]            # normalized name -> team label, every team in the league


def from_names(league: str, mine: Iterable[str], by_team: Mapping[str, Iterable[str]]) -> LeagueRosters:
    owners = {normalize(name): label(team) for team, names in by_team.items() for name in names}
    return LeagueRosters(label(league), frozenset(normalize(n) for n in mine), owners)


def load(session: Session, league, my_team) -> LeagueRosters:
    """Every team's latest imported roster in the league (names from our own database)."""
    from database.models import Player, Team
    from services.my_team import rosters

    spots = rosters(session, league)
    ids = {s.player_id for team_spots in spots.values() for s in team_spots}
    names = dict(session.execute(select(Player.id, Player.full_name).where(Player.id.in_(ids))).all()) if ids else {}
    teams = dict(session.execute(select(Team.id, Team.name).where(Team.league_id == league.id)).all())

    def players(team_id: int) -> list[str]:
        return [names[s.player_id] for s in spots.get(team_id, []) if s.player_id in names]
    by_team: dict[str, list[str]] = {}
    for team_id in spots:
        by_team.setdefault(teams.get(team_id) or "another team", []).extend(players(team_id))
    return from_names(league.name, players(my_team.id), by_team)


def in_scope(session: Session, focus_league_id: int | None, named_leagues: Iterable[str] = ()) -> LeagueRosters | None:
    """The league this answer is about: the chat's league, else the only imported league, else the one
    league every league tool call this turn named. None when no single league is in scope."""
    from services.my_team import LeagueNotFound, find_league, my_leagues

    leagues = my_leagues(session)
    if focus_league_id is not None:
        found = next(((lg, team) for lg, team in leagues if lg.id == focus_league_id), None)
    elif len(leagues) == 1:
        found = leagues[0]
    else:
        names = {n.strip().lower() for n in named_leagues if n and n.strip()}
        try:
            found = find_league(session, names.pop()) if len(names) == 1 else None
        except LeagueNotFound:
            found = None
    return load(session, *found) if found else None


def _advice_clause(answer: str, verb_start: int) -> bool:
    before = answer[:verb_start]
    ends = list(_CLAUSE_END.finditer(before))
    clause = _LIST_MARKER.sub("", before[ends[-1].end():] if ends else before)
    return _ADVICE_PREFIX.match(clause.strip().lower().replace("’", "'")) is not None


def _named_player(rest: str, index: _Index) -> str | None:
    """The one player whose name opens `rest` ("Bijan Robinson over Gibbs" -> Bijan Robinson)."""
    found = index.mentions(rest)
    if not found:
        return None
    first = found[0]
    if len(first.names) != 1 or not normalize(rest).startswith(normalize(first.text)):
        return None
    if re.search(rf"\b{re.escape(first.text.split()[-1])}['’]s\b", rest, re.IGNORECASE):
        return None      # "add Bijan Robinson's backup" is about someone else
    words = set(normalize(first.text).split())
    if not words or not words <= set(normalize(first.names[0]).split()):
        return None      # typo guesses and nicknames are too loose to flag an answer on
    return first.names[0]


def incoming_keys(names: Iterable[str], index: _Index) -> set[str]:
    """Normalized full names of players the user would receive (an evaluate_trade `get` side)."""
    keys = set()
    for name in names:
        found = index.resolve(str(name))
        if len(found) == 1:
            keys.add(normalize(found[0]))
    return keys


def check(answer: str, rosters: LeagueRosters | None, index: _Index,
          incoming: Iterable[str] = ()) -> tuple[str, ...]:
    """Short problem notes ("add Bijan Robinson: already on Rival"), empty when every move fits the rosters.

    Players the answer tells the user to add, and the `incoming` players of a trade this turn priced,
    count as the user's for a later start/bench/drop ("Add X, then start X over Gibbs")."""
    if rosters is None or not answer:
        return ()
    issues: list[str] = []
    mine = set(rosters.mine) | incoming_keys(incoming, index)
    for match in _ACTION.finditer(answer):
        verb = " ".join(match.group("verb").lower().split())
        if not _advice_clause(answer, match.start()):
            continue
        player = _named_player(match.group("rest"), index)
        if player is None:
            continue
        key = normalize(player)
        if verb in ADD_VERBS:
            if key in rosters.owners:
                owner = rosters.owners[key]
                where = "already on your roster" if key in rosters.mine else f"already on {owner}'s roster"
                issues.append(f"{verb} {player} ({where} in {rosters.league})")
            mine.add(key)    # flagged or not, a later "start him" follows from this add
        elif verb in MINE_VERBS and key not in mine:
            issues.append(f"{verb} {player} (not on your roster in {rosters.league})")
    return tuple(dict.fromkeys(issues))
