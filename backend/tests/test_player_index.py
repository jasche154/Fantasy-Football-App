"""Player name index: nicknames, last names, typos, ambiguity, and name resolution built on it."""
import pytest

from database.models import Player, PlayerStatsWeekly
from services import player_index
from services.player_index import PlayerIndex, get_index, normalize
from services.players import AmbiguousPlayer, PlayerNotFound, resolve_player, search_players
from tests.conftest import CURRENT

ACTIVE = ("Christian McCaffrey", "Luke McCaffrey", "Amon-Ra St. Brown", "Equanimeous St. Brown", "A.J. Brown",
          "Jahmyr Gibbs", "Puka Nacua", "Justin Jefferson", "Van Jefferson", "Jordan Love", "Jalen Hurts",
          "Marvin Harrison Jr.", "Breece Hall", "Terry McLaurin", "Tetairoa McMillan", "Jaxon Smith-Njigba")
RETIRED = ("Antonio Gibson", "Antonio Brown")
INDEX = PlayerIndex(ACTIVE + RETIRED, ACTIVE)


def _names(text: str) -> list[tuple[str, ...]]:
    return [m.names for m in INDEX.mentions(text)]


@pytest.mark.parametrize("raw, expected", [
    ("Ja'Marr Chase", "jamarr chase"), ("A.J. Brown", "aj brown"), ("Amon-Ra St. Brown", "amon ra st brown"),
    ("Marvin Harrison Jr.", "marvin harrison"), ("Kenneth Walker III", "kenneth walker"),
    ("Ja’Marr​ Chase", "jamarr chase"), ("", ""),
])
def test_normalize_drops_punctuation_suffixes_and_case(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("text, expected", [
    ("Is CMC playing?", [("Christian McCaffrey",)]),
    ("ARSB or Puka?", [("Amon-Ra St. Brown",), ("Puka Nacua",)]),
    ("should I start gibbs", [("Jahmyr Gibbs",)]),
    ("Is Jahmyr Gibs worth it over Breece Hal?", [("Jahmyr Gibbs",), ("Breece Hall",)]),
    ("AJ Brown or St Brown", [("A.J. Brown",), ("Amon-Ra St. Brown", "Equanimeous St. Brown")]),
    ("Puka's snap share", [("Puka Nacua",)]),
    ("JSN and Smith-Njigba", [("Jaxon Smith-Njigba",), ("Jaxon Smith-Njigba",)]),
    ("Who should I pick up on waivers this week?", []),
])
def test_mentions_find_nicknames_last_names_typos_and_punctuation(text, expected):
    assert _names(text) == expected


def test_shared_last_names_and_two_target_aliases_are_ambiguous_not_guessed():
    jefferson, = INDEX.mentions("Should I trade for Jefferson?")
    assert jefferson.ambiguous and jefferson.names == ("Justin Jefferson", "Van Jefferson")
    tmac, = INDEX.mentions("is TMac a buy?")
    assert tmac.ambiguous and set(tmac.names) == {"Terry McLaurin", "Tetairoa McMillan"}
    assert not INDEX.mentions("Is Puka good?")[0].ambiguous


def test_english_word_last_names_count_only_capitalized_mid_sentence():
    assert _names("Love or Hurts this week?") == [("Jalen Hurts",)]          # sentence-initial "Love" skipped
    assert _names("Should I start Love?") == [("Jordan Love",)]
    assert _names("i love this team") == []


def test_retired_players_match_by_full_name_only():
    assert _names("Antonio Gibson") == [("Antonio Gibson",)]
    assert _names("gibson stats") == []                                      # last name: active players only
    assert _names("Gibs") == [("Jahmyr Gibbs",)]                             # typo never lands on the retiree


def test_resolve_and_aliases_without_a_target_are_skipped():
    assert INDEX.resolve("mhj") == ("Marvin Harrison Jr.",)
    assert INDEX.resolve("marvin harrison") == ("Marvin Harrison Jr.",)
    assert INDEX.resolve("Zzyzx") == () and INDEX.resolve("Gibbs and Puka") == ()
    assert PlayerIndex(["Jahmyr Gibbs"]).resolve("cmc") == ()                # McCaffrey isn't in this index
    assert INDEX.last_name_map["st brown"] == ("Amon-Ra St. Brown", "Equanimeous St. Brown")
    assert "Antonio Gibson" in INDEX.full_names and "gibson" not in INDEX.last_name_map


def _add(session, name: str, position: str = "RB", season: int | None = CURRENT) -> int:
    player = Player(full_name=name, position=position, nfl_team="AAA", gsis_id=f"00-{name}")
    session.add(player)
    session.flush()
    if season is not None:
        session.add(PlayerStatsWeekly(player_id=player.id, season=season, week=1, season_type="REG"))
    session.commit()
    return player.id


def test_index_from_the_database_uses_recent_stats_and_rebuilds_when_players_change(session, seeded):
    assert get_index(session).resolve("Chase") == ("Ja'Marr Chase",)
    retired = _add(session, "Antonio Gibson", season=CURRENT - 5)
    gibbs = _add(session, "Jahmyr Gibbs")
    index = get_index(session)
    assert index.resolve("Gibbs") == ("Jahmyr Gibbs",) and "gibson" not in index.last_name_map
    assert index is get_index(session)                                      # cached
    player_index.clear_index_cache()
    assert get_index(session) is not index
    assert resolve_player(session, "Gibs").id == gibbs
    assert resolve_player(session, "Antonio Gibson").id == retired


def test_resolve_player_never_guesses_a_lone_partial_match(session, seeded):
    _add(session, "Antonio Gibson", season=None)                            # retired, no recent stats
    with pytest.raises(PlayerNotFound, match="Closest: Antonio Gibson"):
        resolve_player(session, "Gibs")
    assert resolve_player(session, "Antonio Gib").name == "Antonio Gibson"  # a prefix is still fine


def test_resolve_player_handles_nicknames_and_punctuation(session, seeded):
    brown = _add(session, "A.J. Brown", "WR")
    cmc = _add(session, "Christian McCaffrey")
    assert resolve_player(session, "AJ Brown").id == brown
    assert resolve_player(session, "CMC").id == cmc
    assert search_players(session, "cmc")[0].id == cmc
    with pytest.raises(AmbiguousPlayer):
        resolve_player(session, "mike williams")
