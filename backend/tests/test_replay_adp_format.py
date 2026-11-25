"""Replay League ADP per draft format: FFC's format lists, the import, the schema change and which list a
league reads (no network)."""
import json
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base, ReplayAdp
from integrations import adp_history
from integrations.adp_history import HistoryClient, HistoryError
from services import replay_adp_format as af
from services import replay_chart, replay_data, replay_pool, replay_state as rs
from services.replay_scoring import ScoringRules
from tests import replay_fakes as fakes
from tests.test_replay_data import FFC_PAYLOAD, SEASON, Sites, adp_by_gsis, db  # noqa: F401  (db is a fixture)

# Trimmed real 2023 answers (top three, each format) from /api/v1/adp/{format}?teams=12&year=2023.
FFC_2023 = {
    "ppr": ("PPR", [("Justin Jefferson", "WR", "MIN", 1.4, 0.6), ("Christian McCaffrey", "RB", "SF", 2.4, 1.0),
                    ("Ja'Marr Chase", "WR", "CIN", 2.4, 0.8)]),
    "half": ("Half-PPR", [("Justin Jefferson", "WR", "MIN", 1.4, 0.7), ("Christian McCaffrey", "RB", "SF", 2.3, 1.0),
                          ("Austin Ekeler", "RB", "LAC", 2.5, 2.0)]),
    "std": ("Non-PPR", [("Justin Jefferson", "WR", "MIN", 1.2, 0.4), ("Christian McCaffrey", "RB", "SF", 1.4, 0.6),
                        ("Nick Chubb", "RB", "CLE", 3.1, 1.0)]),
    "2qb": ("2 QB", [("Patrick Mahomes", "QB", "KC", 1.7, 1.0), ("Justin Jefferson", "WR", "MIN", 2.4, 1.4),
                     ("Josh Allen", "QB", "BUF", 2.7, 1.0)]),
}
STANDARD = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}
SUPERFLEX = {**STANDARD, "SUPERFLEX": 1}
TWO_QB = {**STANDARD, "QB": 2}


def ffc_payload(adp_format: str) -> dict:
    kind, rows = FFC_2023[adp_format]
    return {"status": "Success", "meta": {"type": kind, "teams": 12, "rounds": 15},
            "players": [{"player_id": n, "name": name, "position": pos, "team": team, "adp": adp, "stdev": sd}
                        for n, (name, pos, team, adp, sd) in enumerate(rows)]}


# ---------------- FFC lists ----------------

@pytest.mark.parametrize("adp_format", list(FFC_2023))
def test_every_ffc_format_parses_in_draft_order(adp_format):
    entries = adp_history.parse_ffc(ffc_payload(adp_format), adp_format)

    assert [(e.name, e.position, e.adp) for e in entries] == [(n, p, a) for n, p, _, a, _ in FFC_2023[adp_format][1]]


def test_a_list_of_another_format_is_refused():
    with pytest.raises(HistoryError, match="another format"):
        adp_history.parse_ffc(ffc_payload("ppr"), "2qb")


def test_each_format_is_fetched_from_its_own_path():
    sites = Sites(formats={f: ffc_payload(f) for f in FFC_2023})
    client = HistoryClient(sites.client(), pause=0)

    firsts = {f: adp_history.fetch_ffc(client, 2023, f)[0].name for f in FFC_2023}

    assert firsts == {"ppr": "Justin Jefferson", "half": "Justin Jefferson", "std": "Justin Jefferson",
                      "2qb": "Patrick Mahomes"}
    assert [r.url.path for r in sites.requests] == ["/api/v1/adp/ppr", "/api/v1/adp/half-ppr",
                                                    "/api/v1/adp/standard", "/api/v1/adp/2qb"]
    assert all(r.url.params["year"] == "2023" and r.url.params["teams"] == "12" for r in sites.requests)


# ---------------- the import ----------------

def test_every_format_is_stored_each_with_mfls_tail(db):
    two_qb = {**FFC_PAYLOAD, "players": [{**p, "adp": 3.0} if p["name"] == "Mike Williams" else p
                                         for p in FFC_PAYLOAD["players"]]}
    summary = replay_data.load_adp(db, SEASON, HistoryClient(Sites(formats={"2qb": two_qb}).client(), pause=0))

    assert summary["formats"] == {"ppr": 7, "half": 7, "std": 7, "2qb": 7} and summary["errors"] == {}
    assert summary["rows"] == 7 and summary["unmatched"] == ["Nobody Unknown (WR, ffc 160.0)"]
    assert adp_by_gsis(db, "2qb")["00-MW1"] == (3.0, "ffc") and adp_by_gsis(db)["00-MW1"] == (80.0, "ffc")
    assert adp_by_gsis(db, "std")["00-ZUER"] == (153.0, "mfl")


def test_a_format_ffc_cannot_give_is_reported_and_keeps_what_is_stored(db):
    replay_data.load_adp(db, SEASON, HistoryClient(Sites().client(), pause=0))
    stored_2qb = adp_by_gsis(db, "2qb")

    forced = replay_data.load_adp(db, SEASON, HistoryClient(Sites(broken="2qb").client(), pause=0), force=True)

    assert forced["errors"] == {"adp ffc 2qb": "HistoryError"} and set(forced["formats"]) == {"ppr", "half", "std"}
    assert adp_by_gsis(db, "2qb") == stored_2qb


def test_a_season_stored_before_formats_fetches_the_missing_ones_on_the_next_run(db):
    sites = Sites()
    replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))
    db.execute(ReplayAdp.__table__.delete().where(ReplayAdp.format != "ppr"))   # as the migration leaves it
    db.commit()

    sites.requests.clear()
    again = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))

    assert "skipped" not in again and again["formats"] == {"half": 7, "std": 7, "2qb": 7} and again["rows"] == 7
    assert ffc_paths(sites) == ["half-ppr", "standard", "2qb"]                    # PPR was complete: not fetched
    assert set(adp_by_gsis(db)) == set(adp_by_gsis(db, "2qb"))


def ffc_paths(sites: Sites) -> list[str]:
    return [r.url.path.rsplit("/", 1)[1] for r in sites.requests if "fantasyfootballcalculator" in r.url.host]


NO_DATA = {"status": "Error", "errors": "No ADP data found."}     # FFC's answer for a list it does not have
NO_LIST = "FfcNoDataError"


def test_a_format_ffc_has_no_list_for_is_not_asked_for_again_until_a_forced_run(db):
    sites = Sites(formats={"2qb": NO_DATA})
    first = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))
    sites.requests.clear()

    again = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))
    forced = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0), force=True)

    assert first["errors"] == {"adp ffc 2qb": NO_LIST} and set(first["formats"]) == {"ppr", "half", "std"}
    assert again == {"rows": 7, "skipped": True}
    assert ffc_paths(sites) == ["ppr", "half-ppr", "standard", "2qb"]              # only the forced run asked
    assert forced["errors"] == {"adp ffc 2qb": NO_LIST}


@pytest.mark.parametrize("glitch", [{"status": "Error", "errors": "Too many requests"}, {"status": "Error"},
                                    {k: v for k, v in FFC_PAYLOAD.items() if k != "meta"}])  # throttled, bare, no type
def test_an_ffc_glitch_is_not_remembered_as_no_list_and_is_asked_again(db, glitch):
    sites = Sites(formats={"2qb": glitch})
    first = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))
    sites.requests.clear()

    again = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))

    assert set(first["errors"]) == {"adp ffc 2qb"} and first["errors"]["adp ffc 2qb"] == "HistoryError"
    assert "2qb" in ffc_paths(sites) and "skipped" not in again


def test_a_failed_request_for_a_format_is_retried_on_the_next_run(db):
    replay_data.load_adp(db, SEASON, HistoryClient(Sites(broken="2qb").client(), pause=0))
    sites = Sites()

    again = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))

    assert again["formats"] == {"2qb": 7} and ffc_paths(sites) == ["2qb"]


def without(name: str) -> dict:
    return {**FFC_PAYLOAD, "players": [p for p in FFC_PAYLOAD["players"] if p["name"] != name]}


def test_a_player_a_thin_list_leaves_out_takes_his_adp_from_another_ffc_list_not_the_tail():
    # 2026 half-PPR: FFC's thin list left out Drake London (13.1 in PPR), so MFL's tail put him at pick 172
    half = [(adp_history.AdpEntry("ffc", "A", "RB", 1.5, 0.5), 1), (adp_history.AdpEntry("ffc", "B", "WR", 170.0), 2)]
    ppr = [(adp_history.AdpEntry("ffc", "London", "WR", 13.1, 2.4, "ATL"), 3), (adp_history.AdpEntry("ffc", "B", "WR", 160.0), 2)]
    std = [(adp_history.AdpEntry("ffc", "London", "WR", 20.0, 5.0), 3), (adp_history.AdpEntry("ffc", "C", "K", 180.0), 5)]
    mfl = [(adp_history.AdpEntry("mfl", "London", "WR", 12.0), 3), (adp_history.AdpEntry("mfl", "D", "TE", 25.0), 4)]

    rows = replay_data.adp_rows(SEASON, half, mfl, [ppr, std])

    assert [(r["player_id"], r["adp"], r["stdev"], r["source"], r["team"]) for r in rows] == [
        (1, 1.5, 0.5, "ffc", None), (2, 170.0, None, "ffc", None), (3, 13.1, 2.4, "ffc_borrowed", "ATL"),
        (5, 180.0, None, "ffc_borrowed", None), (4, 181.0, None, "mfl", None)]   # only a player in no FFC list goes last


def test_the_2qb_list_borrows_a_qb_from_a_1qb_list_at_superflex_prices():
    two_qb = [(adp_history.AdpEntry("ffc", "A", "QB", 1.5, 0.5), 1)]
    ppr = [(adp_history.AdpEntry("ffc", "Q", "QB", 60.0, 5.0), 2), (adp_history.AdpEntry("ffc", "W", "WR", 30.0, 2.0), 3)]

    rows = replay_data.adp_rows(SEASON, two_qb, [], [ppr], replay_chart.SUPERFLEX_QB_ADP)

    assert [(r["player_id"], r["adp"], r["stdev"], r["source"]) for r in rows] == [
        (1, 1.5, 0.5, "ffc"), (2, 60.0 * replay_chart.SUPERFLEX_QB_ADP, 5.0 * replay_chart.SUPERFLEX_QB_ADP,
                               "ffc_borrowed"), (3, 30.0, 2.0, "ffc_borrowed")]


def test_a_list_missing_a_player_borrows_the_ppr_lists_adp_first(db):
    std = {**FFC_PAYLOAD, "players": [{**p, "adp": 70.0} if p["name"] == "Mike Williams" else p
                                      for p in FFC_PAYLOAD["players"]]}
    sites = Sites(formats={"half": without("Mike Williams"), "std": std, "2qb": without("Mike Williams")})

    replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))

    assert adp_by_gsis(db, "half")["00-MW1"] == (80.0, "ffc_borrowed")
    assert adp_by_gsis(db, "2qb")["00-MW1"] == (80.0, "ffc_borrowed")
    assert adp_by_gsis(db, "half")["00-HURT"] == (151.0, "mfl")       # the tail still starts after the last pick


def test_a_format_fetched_alone_borrows_from_the_stored_ffc_lists(db):
    replay_data.load_adp(db, SEASON, HistoryClient(Sites().client(), pause=0))
    db.execute(ReplayAdp.__table__.delete().where(ReplayAdp.format == "half"))
    db.commit()

    again = replay_data.load_adp(db, SEASON, HistoryClient(Sites(formats={"half": without("Mike Williams")}).client(),
                                                           pause=0))

    assert again["formats"] == {"half": 7} and adp_by_gsis(db, "half")["00-MW1"] == (80.0, "ffc_borrowed")


def test_a_borrowed_row_is_never_lent_again(db):
    # half borrowed Mike Williams from PPR; PPR alone, refetched without him, must not take him back from half
    replay_data.load_adp(db, SEASON, HistoryClient(Sites(formats={"half": without("Mike Williams"),
                                                                  "std": without("Mike Williams"),
                                                                  "2qb": without("Mike Williams")}).client(), pause=0))
    db.execute(ReplayAdp.__table__.delete().where(ReplayAdp.format == "ppr"))
    db.commit()

    replay_data.load_adp(db, SEASON, HistoryClient(Sites(formats={"ppr": without("Mike Williams")}).client(),
                                                           pause=0))

    assert adp_by_gsis(db, "half")["00-MW1"] == (80.0, "ffc_borrowed")
    assert "00-MW1" not in adp_by_gsis(db)                                 # in no FFC list of its own, nor MFL


# ---------------- which list a league reads ----------------

@pytest.mark.parametrize(("scoring", "rules", "starters", "want"), [
    ("ppr", None, STANDARD, "ppr"), ("half", None, STANDARD, "half"), ("std", None, None, "std"),
    ("half", None, SUPERFLEX, "2qb"), ("std", None, TWO_QB, "2qb"),
    ("half", ScoringRules(reception=0.75), STANDARD, "ppr"), ("half", ScoringRules(reception=0.25), STANDARD, "half"),
    ("ppr", ScoringRules(reception=0.0, pass_td=6.0), STANDARD, "std"),
    ("ppr", ScoringRules(reception=1.5), STANDARD, "ppr"),
])
def test_wanted_list(scoring, rules, starters, want):
    assert af.wanted(scoring, rules, starters) == want


@pytest.mark.parametrize(("want", "scoring", "available", "chosen"), [
    ("2qb", "half", {"ppr", "half", "std", "2qb"}, "2qb"),
    ("2qb", "half", {"ppr", "half", "std"}, "half"),
    ("2qb", "std", {"ppr"}, "ppr"),
    ("half", "half", {"ppr", "std"}, "ppr"),
    ("std", "std", {"ppr", "half"}, "half"),
    ("ppr", "ppr", set(), None),
])
def test_missing_list_falls_back_to_the_nearest(want, scoring, available, chosen):
    assert af.choose(want, scoring, available) == chosen


@pytest.fixture
def seeded():
    """The fake season (PPR rows only, as every database had before formats)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    data = fakes.fake_season()
    replay_pool.clear_cache()
    with Session(engine) as session:
        fakes.seed_db(session, data)
        yield session, data
    replay_pool.clear_cache()
    engine.dispose()


def add_format(session: Session, data, adp_format: str, factor: dict[str, float]) -> None:
    session.execute(insert(ReplayAdp), [
        {"season": data.season, "player_id": pid, "format": adp_format, "adp": p.adp * factor.get(p.position, 1.0),
         "stdev": p.adp_stdev, "source": "ffc", "team": p.adp_team}
        for pid, p in data.players.items() if p.adp is not None])
    session.commit()
    replay_pool.clear_cache()


def adps(data) -> dict[int, float]:
    return {pid: p.adp for pid, p in data.players.items() if p.adp is not None}


def test_a_ppr_only_season_loads_exactly_as_before_for_every_league(seeded):
    session, data = seeded

    for scoring, starters in (("ppr", STANDARD), ("half", None), ("std", STANDARD), ("half", SUPERFLEX)):
        loaded = replay_pool.load_season_data(session, data.season, scoring, None,
                                              af.wanted(scoring, None, starters))
        assert loaded.adp_format == "ppr" and adps(loaded) == adps(data)

    superflex = replay_pool.load_season_data(session, data.season, "half", None, af.wanted("half", None, SUPERFLEX))
    qb = next(pid for pid, p in data.players.items() if p.position == "QB" and p.adp is not None)
    assert replay_chart.superflex_view(superflex).players[qb].adp == pytest.approx(
        data.players[qb].adp * replay_chart.SUPERFLEX_QB_ADP)                 # no 2-QB list: the stopgap applies


def test_each_league_reads_its_own_list_and_the_2qb_list_replaces_the_qb_boost(seeded):
    session, data = seeded
    add_format(session, data, "2qb", {"QB": 0.3})
    add_format(session, data, "half", {"RB": 0.9})
    qb = next(pid for pid, p in data.players.items() if p.position == "QB" and p.adp is not None)
    back = next(pid for pid, p in data.players.items() if p.position == "RB" and p.adp is not None)

    superflex = replay_pool.load_season_data(session, data.season, "half", None, af.wanted("half", None, SUPERFLEX))
    two_qb = replay_pool.load_season_data(session, data.season, "std", None, af.wanted("std", None, TWO_QB))
    half = replay_pool.load_season_data(session, data.season, "half", None, af.wanted("half", None, STANDARD))
    custom_rules = ScoringRules(reception=0.75)
    custom = replay_pool.load_season_data(session, data.season, "half", custom_rules,
                                          af.wanted("half", custom_rules, STANDARD))
    full = replay_pool.load_season_data(session, data.season, "ppr")

    assert (superflex.adp_format, two_qb.adp_format, half.adp_format) == ("2qb", "2qb", "half")
    assert superflex.players[qb].adp == pytest.approx(data.players[qb].adp * 0.3)
    assert replay_chart.superflex_view(superflex) is superflex                  # the 2-QB list already prices QBs
    assert half.players[back].adp == pytest.approx(data.players[back].adp * 0.9)
    assert (custom.adp_format, full.adp_format) == ("ppr", "ppr") and adps(full) == adps(data)
    assert superflex.as_of(3).adp_format == "2qb"                              # ADP is preseason: every week
    assert replay_pool.readiness(session, data.season)["adp_players"] == len(adps(data))


def test_a_league_saved_before_adp_lists_keeps_drafting_from_ppr(seeded):
    session, data = seeded
    add_format(session, data, "half", {"RB": 0.9})
    saved = json.loads(rs.to_json(rs.Settings(season=data.season, scoring="half")))
    saved.pop("adp_format")                                         # the JSON a league saved before this field had

    old = rs.settings_from_json(json.dumps(saved))
    new = replace(old, adp_format=af.wanted(old.scoring, old.scoring_rules, old.starters))
    loads = [replay_pool.load_season_data(session, s.season, s.scoring, s.scoring_rules, s.adp_format)
             for s in (old, new)]

    assert (old.adp_format, new.adp_format) == ("ppr", "half")
    assert [d.adp_format for d in loads] == ["ppr", "half"] and adps(loads[0]) == adps(data)
    with pytest.raises(ValueError, match="ADP list"):
        rs.Settings(season=data.season, adp_format="dynasty")


# ---------------- schema ----------------

def test_migration_backfills_ppr_and_keys_rows_by_format(tmp_path):
    from alembic import command
    from sqlalchemy import inspect, text

    from database.create_tables import alembic_config, ensure_schema
    from tests.test_database import _legacy_database

    legacy = _legacy_database(tmp_path / "adp.db")
    ensure_schema(legacy)
    config = alembic_config(legacy)
    command.downgrade(config, "c1e3a5b7d9f2")
    with legacy.begin() as connection:
        connection.execute(text("INSERT INTO players (id, full_name, position) VALUES (1, 'A', 'RB')"))
        connection.execute(text("INSERT INTO replay_adp (season, player_id, adp, source) VALUES (2023, 1, 4.5, 'ffc')"))

    command.upgrade(config, "head")
    command.upgrade(config, "head")                                             # a second run is a no-op

    indexes = {i["name"]: i for i in inspect(legacy).get_indexes("replay_adp")}
    assert "ux_replay_adp_season_player" not in indexes
    assert indexes["ux_replay_adp_season_player_format"]["column_names"] == ["season", "player_id", "format"]
    with Session(legacy) as session:
        assert session.scalars(select(ReplayAdp.format)).all() == ["ppr"]
        session.add(ReplayAdp(season=2023, player_id=1, format="2qb", adp=2.0, source="ffc"))
        session.commit()                                                        # one row per format now fits


def test_stored_donor_lists_carry_the_players_position(db):
    # a QB borrowed from a stored 1-QB list must be priced as a QB (adp_rows scales position == "QB" only)
    replay_data.load_adp(db, SEASON, HistoryClient(Sites().client(), pause=0))
    stored = replay_data._stored_ffc(db, SEASON, set())
    positions = {entry.position for rows in stored.values() for entry, _ in rows}
    assert positions and "" not in positions and "RB" in positions
