"""Find the players a piece of text is about: full names, last names, nicknames and typos.

Used by routing (does this question name a player?), name resolution (players.resolve_player)
and grounding (did the answer name a player no tool returned?). Rules:
- Full names match for every player, retired too ("A.J. Brown" = "AJ Brown" = "aj brown").
- Single words (last names, distinctive first names like "Puka") and typos only match *active*
  players (stats in the last two seasons or on an imported roster), so "Gibs" finds Jahmyr Gibbs
  and never a retired Gibson.
- A word that fits several players is returned as ambiguous, never guessed.
- Last names that are also English words ("Love", "Hill", "Worthy") count only when capitalized
  mid-sentence.
"""
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from weakref import WeakKeyDictionary

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Player, PlayerStatsWeekly, Roster
from services.league import POSITIONS

TYPO_CUTOFF = 0.85
MIN_TYPO_LETTERS = 4
ACTIVE_SEASONS = 2
MAX_NGRAM = 4
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
PARTICLES = frozenset({"st", "van", "von", "de", "del", "da", "le", "la", "mc"})

# Nicknames and shorthand -> full names (matched after normalize()). A target missing from the
# database is skipped, and an alias with two targets is ambiguous on purpose.
ALIASES: Mapping[str, tuple[str, ...]] = {
    "cmc": ("Christian McCaffrey",), "mccaffrey": ("Christian McCaffrey",),
    "arsb": ("Amon-Ra St. Brown",), "sun god": ("Amon-Ra St. Brown",), "amon ra": ("Amon-Ra St. Brown",),
    "jsn": ("Jaxon Smith-Njigba",), "mhj": ("Marvin Harrison Jr.",), "btj": ("Brian Thomas Jr.",),
    "ajb": ("A.J. Brown",), "dk": ("DK Metcalf",), "scary terry": ("Terry McLaurin",),
    "tmac": ("Terry McLaurin", "Tetairoa McMillan"), "t mac": ("Terry McLaurin", "Tetairoa McMillan"),
    "hollywood": ("Marquise Brown",), "hollywood brown": ("Marquise Brown",),
    "k9": ("Kenneth Walker III",), "etn": ("Travis Etienne",), "ceedee": ("CeeDee Lamb",),
    "jt": ("Jonathan Taylor",), "hock": ("T.J. Hockenson",), "jjettas": ("Justin Jefferson",),
    "jjetta": ("Justin Jefferson",), "nuk": ("DeAndre Hopkins",), "dhop": ("DeAndre Hopkins",),
    "obj": ("Odell Beckham Jr.",), "mvs": ("Marquez Valdes-Scantling",), "deebo": ("Deebo Samuel Sr.",),
    "kelce": ("Travis Kelce",), "achane": ("De'Von Achane",), "swift": ("D'Andre Swift",),
    "lamar": ("Lamar Jackson",), "mahomes": ("Patrick Mahomes",), "saquon": ("Saquon Barkley",),
    "bijan": ("Bijan Robinson",), "puka": ("Puka Nacua",), "kyren": ("Kyren Williams",),
    "breece": ("Breece Hall",), "bucky": ("Bucky Irving",), "jeanty": ("Ashton Jeanty",),
    "mondre": ("Rhamondre Stevenson",), "tet": ("Tetairoa McMillan",),
}
# Last names that are everyday words: they count only when capitalized and not starting a sentence.
ENGLISH_NAMES = frozenset({
    "love", "hill", "will", "young", "brown", "moore", "hunt", "rice", "worthy", "hurts", "swift", "fields",
    "cook", "lamb", "king", "best", "strong", "black", "white", "gray", "green", "golden", "field", "price",
    "bell", "banks", "mason", "miles", "lane", "ford", "booker", "payne", "house", "page", "power", "west",
    "day", "may", "hardy", "rich", "long", "short", "pierce", "cash", "deal", "lock", "free", "early",
    "worth", "case", "law", "ball", "gore", "justice", "rome", "hunter", "tank", "sky", "story",
})
# Ordinary question words never treated as a name or a typo of one.
COMMON_WORDS = frozenset("""
a an i is to of in on at my me do be if so up no as by he we it or vs am us
the and for with what who whom why how when where which should would could will can cant dont does did
this that these those there their them they him his her she you your yours mine our out over under about
start starting started sit sitting bench lineup flex play playing played week weeks weekly game games
trade trades trading waiver waivers pickup pick drop drops cut add adds stream streamer streaming bid bids
team teams league leagues roster rosters player players rank ranks ranking rankings best worst good bad
better worse top value points point score scoring ppr half standard matchup matchups opponent schedule
season rest injury injured hurt news update status questionable doubtful practice return returning
playoff playoffs odds win winning lose losing beat against than then just like look looking think
need needs want wants keep keeper hold sell buy low high upside floor ceiling safe risky boom bust
tight wide end ends receiver receivers running back backs quarterback defense kicker rookie rookies
compare versus right left after before still also only even more most less least much many some any
every each other another into from have has had been being were was are its it's yes not now next last
help thanks thank please hello hey good morning tonight today tomorrow sunday monday thursday night
fantasy football yards yard touchdown touchdowns target targets share snap snaps carries catches
""".split())

_POSSESSIVE = re.compile(r"['’]s\b", re.IGNORECASE)
_DROP_CHARS = re.compile(r"['’‘`.]")
_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_ZERO_WIDTH = re.compile("[​-‏⁠﻿]")


def normalize(name: str) -> str:
    """Lowercase, NFKC, no apostrophes/periods, hyphens as spaces, no Jr/Sr/II/III/IV."""
    text = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", name or ""))
    text = _DROP_CHARS.sub("", _POSSESSIVE.sub("", text)).lower()
    return " ".join(w for w in _WORD.findall(text) if w not in SUFFIXES)


@dataclass(frozen=True)
class Mention:
    text: str                      # the words as the user wrote them
    names: tuple[str, ...]         # candidate full names (database spelling)
    ambiguous: bool


@dataclass(frozen=True)
class _Token:
    norm: str
    cased: str
    sentence_start: bool


def _tokens(text: str) -> list[_Token]:
    """Words of the text, normalized, remembering case and whether each starts a sentence."""
    out: list[_Token] = []
    starts = True
    for raw in unicodedata.normalize("NFKC", _ZERO_WIDTH.sub("", text or "")).split():
        cleaned = _DROP_CHARS.sub("", _POSSESSIVE.sub("", raw))
        parts = [p for p in _WORD.findall(cleaned) if p.lower() not in SUFFIXES]
        for i, part in enumerate(parts):
            out.append(_Token(part.lower(), part, starts and i == 0))
        letters = re.sub(r"[^A-Za-z]", "", raw)
        starts = bool(re.search(r"[!?]['\")\]]*$", raw)) or (
            bool(re.search(r"\.['\")\]]*$", raw)) and len(letters) > 2)
    return out


def _last_name_keys(full: str) -> set[str]:
    words = normalize(full).split()
    if len(words) < 2:
        return set()
    keys = {words[-1]}
    if len(words) >= 3 and words[-2] in PARTICLES:
        keys.add(" ".join(words[-2:]))                      # "st brown"
    last_raw = full.split()[-1] if full.split() else ""
    if "-" in last_raw and normalize(last_raw).count(" "):
        keys.add(normalize(last_raw))                       # "smith njigba"
    return keys


class PlayerIndex:
    """Name lookups over a fixed set of players. Build with get_index(session) in the app."""

    def __init__(self, full_names: Iterable[str], active_names: Iterable[str] | None = None,
                 aliases: Mapping[str, tuple[str, ...]] | None = None):
        self.full_names = frozenset(n for n in full_names if n and " " in n.strip())
        active = self.full_names if active_names is None else frozenset(active_names) & self.full_names
        self._by_full = _group((normalize(n), n) for n in self.full_names)
        self._active_full = _group((normalize(n), n) for n in active)
        self.last_name_map: dict[str, tuple[str, ...]] = _group(
            (key, n) for n in active for key in _last_name_keys(n))
        firsts = _group((normalize(n).split()[0], n) for n in active if normalize(n))
        self._first = {k: v for k, v in firsts.items() if len(v) == 1 and len(k) >= 3}
        self._aliases = {normalize(k): tuple(n for n in v if n in self.full_names)
                         for k, v in (ALIASES if aliases is None else aliases).items()}
        self._aliases = {k: v for k, v in self._aliases.items() if v}

    def mentions(self, text: str) -> tuple[Mention, ...]:
        """Players named in the text, in order. Longest match wins at each position."""
        tokens, found, i = _tokens(text), [], 0
        while i < len(tokens):
            hit = self._match_at(tokens, i)
            if hit is None:
                i += 1
                continue
            width, names = hit
            unique = tuple(dict.fromkeys(names))
            found.append(Mention(" ".join(t.cased for t in tokens[i:i + width]), unique, len(unique) > 1))
            i += width
        return tuple(found)

    def resolve(self, token: str) -> tuple[str, ...]:
        """Candidate full names for one reference ("Gibbs", "CMC", "aj brown"); () if none."""
        key = normalize(token)
        if key in self._by_full:
            return self._by_full[key]
        found = self.mentions(token)
        return found[0].names if len(found) == 1 else ()

    def _match_at(self, tokens: list[_Token], i: int) -> tuple[int, tuple[str, ...]] | None:
        for width in range(min(MAX_NGRAM, len(tokens) - i), 0, -1):
            key = " ".join(t.norm for t in tokens[i:i + width])
            if width > 1 and key in self._by_full:
                return width, self._by_full[key]
            if key in self._aliases and (width > 1 or self._word_ok(tokens[i])):
                return width, self._aliases[key]
            if width > 1 and key in self.last_name_map:      # "st brown", "smith njigba"
                return width, self.last_name_map[key]
        if i + 1 < len(tokens):
            pair = self._typo_pair(tokens[i], tokens[i + 1])
            if pair:
                return 2, pair
        token = tokens[i]
        if not self._word_ok(token):
            return None
        names = self.last_name_map.get(token.norm, ()) + self._first.get(token.norm, ())
        if names:
            return 1, names
        return (1, self._typo_word(token.norm)) if self._typo_word(token.norm) else None

    def _word_ok(self, token: _Token) -> bool:
        if len(token.norm) < 2 or token.norm.isdigit():
            return False
        if token.norm in ENGLISH_NAMES or token.norm in COMMON_WORDS:
            return token.cased[:1].isupper() and not token.sentence_start
        return True

    def _typo_pair(self, first: _Token, second: _Token) -> tuple[str, ...]:
        words = (first.norm, second.norm)
        if any(w in COMMON_WORDS or not w.isalpha() for w in words) or len(first.norm + second.norm) < 7:
            return ()
        close = get_close_matches(" ".join(words), list(self._active_full), n=2, cutoff=TYPO_CUTOFF)
        return tuple(n for key in close for n in self._active_full[key])

    def _typo_word(self, word: str) -> tuple[str, ...]:
        if len(word) < MIN_TYPO_LETTERS or not word.isalpha() or word in COMMON_WORDS or word in ENGLISH_NAMES:
            return ()
        close = get_close_matches(word, list(self.last_name_map), n=2, cutoff=TYPO_CUTOFF)
        return tuple(n for key in close for n in self.last_name_map[key])


def _group(pairs: Iterable[tuple[str, str]]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for key, name in pairs:
        if key and name not in grouped.setdefault(key, []):
            grouped[key].append(name)
    return {k: tuple(sorted(v)) for k, v in grouped.items()}


_cache: "WeakKeyDictionary[object, tuple[int, PlayerIndex]]" = WeakKeyDictionary()


def get_index(session: Session) -> PlayerIndex:
    """The index for this database, cached per engine and rebuilt when the player count changes."""
    engine = session.get_bind()
    count = session.scalar(select(func.count(Player.id))) or 0
    cached = _cache.get(engine)
    if cached is None or cached[0] != count:
        cached = (count, _build(session))
        _cache[engine] = cached
    return cached[1]


def clear_index_cache() -> None:
    _cache.clear()


def _build(session: Session) -> PlayerIndex:
    rows = session.execute(select(Player.id, Player.full_name).where(Player.position.in_(POSITIONS))).all()
    latest = session.scalar(select(func.max(PlayerStatsWeekly.season)))
    active_ids: set[int] = set(session.scalars(select(Roster.player_id).distinct()))
    if latest is not None:
        active_ids |= set(session.scalars(select(PlayerStatsWeekly.player_id).distinct()
                                          .where(PlayerStatsWeekly.season > latest - ACTIVE_SEASONS)))
    return PlayerIndex((r.full_name for r in rows), (r.full_name for r in rows if r.id in active_ids))
