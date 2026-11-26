"""Replay League over HTTP: one whole game driven through the API on a seeded fake season, the error codes,
and proof that no response shows a week that has not been played.

`story` plays the game once (module scope) and keeps what each step answered; the tests read from it.
"""
import json
import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api import replay_routes
from config import get_settings
from database.db import get_session
from database.models import Base
from services import replay_data, replay_pool
from tests import replay_fakes as fakes
from tests.test_replay_views import PLAYER_ROW, SIDE, SUMMARY, WIRE_ROW

DATA = fakes.fake_season()
SEASON, API = fakes.SEASON, "/api/replay"
SEED, SLOT, PARTNER = 32, 5, 9          # with this seed the CPUs send the user several trade offers
LEAK_WEEK = 4
OLD_PLAYOFFS = {"playoff_end_week": 16, "playoff_teams": 4}     # the pre-PR-2 league: 14 weeks + a 4-team bracket
SKILL = ("QB", "RB", "WR", "TE")


def _client(data, url: str = "sqlite://") -> Iterator[TestClient]:
    """The real app on its own database holding `data`. (Loaded seasons are cached per database URL, so two
    different seasons need two different URLs.)"""
    engine = create_engine(url, connect_args={"check_same_thread": False},
                           **({"poolclass": StaticPool} if url == "sqlite://" else {}))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fakes.seed_db(session, data, scores=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("API_TOKEN", raising=False)
        get_settings.cache_clear()
        from main import create_app

        app = create_app(warm_cache=False)
    app.dependency_overrides[get_session] = override
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client
    engine.dispose()


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    yield from _client(DATA)


def get(client: TestClient, path: str, **params) -> dict:
    response = client.get(API + path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def post(client: TestClient, path: str, body: dict | None = None, **params) -> dict:
    response = client.post(API + path, json=body, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def refused(client: TestClient, path: str, body: dict | None = None, method: str = "POST", **params) -> tuple[int, str]:
    """(status, detail) of a request that must fail."""
    response = client.request(method, API + path, json=body, params=params)
    return response.status_code, str(response.json().get("detail"))


def _ids(rows: list[dict], slot: str) -> list[int]:
    return [row["player"]["id"] for row in rows if row["slot"] == slot]


def _draft(client: TestClient, seen: dict) -> str:
    seen["seasons"] = get(client, "/seasons")
    seen["created"] = post(client, "/games", {"season": SEASON, "seed": SEED, "draft_slot": SLOT,
                                              "name": "  Route league ", **OLD_PLAYOFFS})
    game = f"/games/{seen['created']['game']['id']}"
    seen["board"] = get(client, game + "/draft", limit=5)
    seen["pick_taken"] = refused(client, game + "/draft/pick", {"player_id": seen["board"]["picks"][0]["player"]["id"]})
    seen["pick_unknown"] = refused(client, game + "/draft/pick", {"player_id": 999_999})
    seen["pick"] = post(client, game + "/draft/pick", {"player_id": seen["board"]["results"][0]["player"]["id"]})
    seen["auto_one"] = post(client, game + "/draft/auto")
    seen["drafted"] = post(client, game + "/draft/auto", whole_draft="true")
    seen["pick_late"] = refused(client, game + "/draft/pick", {"player_id": 999_999})
    seen["recap"] = get(client, game + "/draft")
    return game


def _week_one(client: TestClient, game: str, seen: dict) -> None:
    """Phase "open": a manual lineup, a free-agent swap, a lopsided trade proposal, then the games."""
    rows = seen["team"] = get(client, game + "/team")
    rows = rows["results"]
    spare = next(row for row in rows if not row["starting"] and row["player"]["position"] in ("RB", "WR"))
    starters = {slot: _ids(rows, slot) for slot in ("QB", "RB", "WR", "TE", "K", "DST")}
    seen["spare"], seen["benched"] = spare["player"]["id"], _ids(rows, "FLEX")[0]
    seen["lineup"] = post(client, game + "/lineup", {"starters": {**starters, "FLEX": [seen["spare"]]}})
    seen["lineup_bad"] = refused(client, game + "/lineup", {"starters": {"QB": _ids(rows, "K")}})
    seen["lineup_auto"] = post(client, game + "/lineup", {"starters": None})
    kicker = get(client, game + "/players", position="K", status="free_agent", sort="projection", limit=1)["results"][0]
    swap = {"add": kicker["player"]["id"], "drop": _ids(rows, "K")[0]}
    seen["claim_too_late"] = refused(client, game + "/claims", swap)
    seen["add_drop"], seen["swap"] = post(client, game + "/add-drop", swap), swap
    theirs = get(client, game + "/team", team_id=PARTNER)["results"]
    worst = min((r for r in rows if not r["starting"] and r["player"]["position"] in SKILL), key=lambda r: r["value"])
    best = max(theirs, key=lambda r: r["value"])
    seen["deal"] = {"to_team": PARTNER, "give": [worst["player"]["id"]], "get": [best["player"]["id"]]}
    seen["evaluate"] = post(client, game + "/trades/evaluate", seen["deal"])
    seen["propose"] = post(client, game + "/trades/propose", seen["deal"])
    seen["propose_again"] = post(client, game + "/trades/propose", seen["deal"])
    seen["trades"] = get(client, game + "/trades")
    seen["stale"] = refused(client, game + "/advance", {"step": "play", "week": 2})
    seen["wrong_step"] = refused(client, game + "/advance", {"step": "wednesday", "week": 1})
    seen["played"] = [post(client, game + "/advance", {"step": "play", "week": 1})]


def _week_two_waivers(client: TestClient, game: str, seen: dict) -> None:
    """Phase "waivers": three claims, reordered, one cancelled, then the waiver run."""
    wire = [row["player"]["id"] for row in get(client, game + "/players", limit=3)["results"]]
    rows = get(client, game + "/team")["results"]
    drop = min((r for r in rows if not r["starting"] and r["player"]["position"] in SKILL),
               key=lambda r: r["value"])["player"]["id"]
    for add in wire:
        post(client, game + "/claims", {"add": add, "drop": drop, "week": 2})
    seen["stale_actions"] = [refused(client, game + path, {**body, "week": 1}) for path, body in (
        ("/lineup", {"starters": None}), ("/claims", {"add": wire[0], "drop": drop}), ("/drop", {"player_id": drop}),
        ("/add-drop", {"add": wire[0], "drop": drop}), ("/ir/place", {"player_id": drop}),
        ("/ir/activate", {"player_id": drop}), ("/trades/propose", seen["deal"]))]
    first = get(client, game + "/claims")
    order = [row["id"] for row in first["results"]][::-1]
    seen["wire"], seen["claim_twice"] = wire, refused(client, game + "/claims", {"add": wire[0], "drop": drop})
    seen["reordered"] = post(client, game + "/claims/reorder", {"ids": order})
    seen["reorder_bad"] = refused(client, game + "/claims/reorder", {"ids": order[:1]})
    seen["cancelled"] = post(client, f"{game}/claims/{order[0]}/cancel")
    seen["cancel_again"] = refused(client, f"{game}/claims/{order[0]}/cancel")
    seen["claims"], seen["claim_order"] = get(client, game + "/claims"), order
    seen["add_too_early"] = refused(client, game + "/add-drop", {"add": wire[0], "drop": drop})
    seen["wednesday"] = post(client, game + "/advance", {"step": "wednesday", "week": 2})


def _snapshot(client: TestClient, game: str) -> dict[str, dict]:
    """The screens that show player points: every team, the whole pool, some player pages."""
    teams = {f"team {team_id}": get(client, game + "/team", team_id=team_id) for team_id in range(1, 13)}
    pool = [row for offset in (0, 200) for row in
            get(client, game + "/players", status="all", limit=200, offset=offset)["results"]]
    ids = [*_ids(teams["team 1"]["results"], "BN"), *_ids(teams["team 7"]["results"], "RB"), *fakes.STORY_IDS]
    lines = {f"players {stats} {sort}": get(client, game + "/players", status="all", stats=stats, sort=sort, dir="asc",
                                            limit=200, **({"stat_week": LEAK_WEEK - 1} if stats == "week" else {}))
             for stats in ("season", "last4", "week") for sort in ("line_points", "rush_yds", "line_projection")}
    boxes = {f"box {g.home_team}": get(client, game + "/box", week=LEAK_WEEK - 1, team=g.home_team)
             for g in DATA.schedule.games if g.week == LEAK_WEEK - 1}
    return {**teams, **lines, **boxes, "players": {"results": pool}, "matchup": get(client, game + "/matchup"),
            **{f"player {pid}": get(client, f"{game}/players/{pid}") for pid in sorted(ids)}}


@pytest.fixture(scope="module")
def story(client) -> dict:
    """One game from the draft to the champion; every answer worth checking is kept by name."""
    seen: dict = {"answers": []}
    game = seen["path"] = _draft(client, seen)
    _week_one(client, game, seen)
    _week_two_waivers(client, game, seen)
    while (summary := get(client, game))["phase"] != "complete":
        week = summary["week"]
        if summary["phase"] == "waivers":
            post(client, game + "/advance", {"step": "wednesday", "week": week})
        for offer in get(client, game + "/trades")["offers"]:
            if "counter" not in seen and offer["verdict"]:          # the first one is countered with the same deal
                deal = {"give": [p["id"] for p in offer["get"]], "get": [p["id"] for p in offer["give"]]}
                seen["counter"] = (offer, post(client, f"{game}/trades/offers/{offer['id']}/counter", deal))
                continue
            accept = len(seen["answers"]) % 2 == 0
            answer = post(client, f"{game}/trades/offers/{offer['id']}/respond", {"accept": accept})
            seen["answers"].append((accept, offer, answer))
        if week == LEAK_WEEK:
            seen["snapshot"] = _snapshot(client, game)
        seen["played"].append(post(client, game + "/advance", {"step": "play", "week": week}))
    seen["after_end"] = refused(client, game + "/advance", {"step": "play", "week": summary["week"]})
    seen["over"] = [refused(client, game + path, body) for path, body in (
        ("/lineup", {"starters": None}), ("/add-drop", {"add": 1}), ("/claims", {"add": 1}))]
    seen["final"] = {name: get(client, f"{game}/{name}") for name in (
        "team", "matchup", "scoreboard", "standings", "schedule", "activity", "chat", "players", "claims", "trades", "draft",
        "playoffs")}
    seen["final"]["summary"] = summary
    return seen


# ---------------- the whole game ----------------

def test_seasons_report_which_can_be_played(story):
    seasons = story["seasons"]

    assert [row["season"] for row in seasons["results"]] == list(replay_pool.SUPPORTED_SEASONS)
    assert [row["season"] for row in seasons["results"] if row["ready"]] == [SEASON]
    assert set(seasons["results"][0]) >= {"season", "ready", "has_stats", "adp_players", "projection_weeks",
                                          "last_week"}
    assert next(row for row in seasons["results"] if row["season"] == SEASON)["last_week"] == 17
    assert seasons["prepare"] is None


def test_create_starts_the_draft_at_the_users_first_pick(story):
    created = story["created"]

    game = created["game"]
    assert set(created) == {"game", "events"} and set(game) == SUMMARY
    assert (game["name"], game["season"], game["phase"], game["week"]) == ("Route league", SEASON, "draft", 0)
    assert game["user_team_id"] == game["on_the_clock"] == SLOT and game["next_action"]["step"] == "pick"
    assert (game["kind"], game["sim_from"], game["start_week"]) == ("replay", None, 1)     # a past season, as it was
    assert [event["kind"] for event in created["events"]] == ["draft"] * (SLOT - 1)
    assert story["board"]["is_user_turn"] and len(story["board"]["picks"]) == SLOT - 1


def test_draft_picks_are_checked_and_followed_by_the_cpus(story):
    pick = story["pick"]

    assert story["pick_taken"][0] == 422 and "already been drafted" in story["pick_taken"][1]
    assert story["pick_unknown"][0] == 422 and "no player 999999" in story["pick_unknown"][1]
    assert pick["events"][0]["team"]["is_user"] and pick["events"][0]["players"][0] == story["board"]["results"][0][
        "player"]
    assert len(pick["events"]) == 1 + 2 * (12 - SLOT) and not any(e["team"]["is_user"] for e in pick["events"][1:])
    assert pick["game"]["on_the_clock"] == SLOT and story["auto_one"]["events"][0]["team"]["is_user"]


def test_auto_draft_finishes_the_draft_and_starts_week_one(story):
    game, recap = story["drafted"]["game"], story["recap"]

    assert (game["phase"], game["week"], game["on_the_clock"]) == ("open", 1, None)
    assert game["next_action"] == {"step": "play", "label": "Play week 1"} and game["matchup"]["week"] == 1
    assert len(recap["picks"]) == 180 and recap["total"] == 0
    assert sum(len(players) for players in recap["roster"].values()) == 15
    assert story["pick_late"] == (422, "The draft is over.")


def test_lineup_can_be_set_by_hand_and_handed_back(story):
    manual, auto = story["lineup"]["team"], story["lineup_auto"]["team"]

    assert manual["manual_lineup"] is True and _ids(manual["results"], "FLEX") == [story["spare"]]
    assert story["benched"] in _ids(manual["results"], "BN")
    assert story["lineup_bad"][0] == 422 and "can't play in the QB slot" in story["lineup_bad"][1]
    assert auto["manual_lineup"] is False and _ids(auto["results"], "FLEX") == [story["benched"]]
    assert set(story["lineup"]) == {"game", "events", "team"} and story["lineup"]["events"] == []


def test_free_agents_are_added_once_waivers_have_run(story):
    events, swap = story["add_drop"]["events"], story["swap"]

    assert [event["kind"] for event in events] == ["add"]                  # one event for the whole move
    assert [player["id"] for player in events[0]["players"]] == [swap["add"], swap["drop"]]
    assert story["claim_too_late"][0] == 422 and "Waiver claims can only be made before" in story["claim_too_late"][1]
    assert story["add_too_early"][0] == 422 and "put in a waiver claim" in story["add_too_early"][1]


def test_claims_are_kept_in_order_and_settled_on_wednesday(story):
    claims, wire = story["claims"], story["wire"]

    assert [row["id"] for row in claims["results"]] == story["claim_order"][1:]
    assert [row["add"]["player"]["id"] for row in claims["results"]] == [wire[1], wire[0]]
    assert story["reordered"]["game"]["pending"]["claims"] == 3 and story["cancelled"]["game"]["pending"]["claims"] == 2
    assert story["claim_twice"][0] == 422 and "already have a claim" in story["claim_twice"][1]
    assert story["reorder_bad"][0] == 422 and story["cancel_again"][0] == 422
    settled = [event for event in story["wednesday"]["events"] if event["kind"] == "claim" and event["team"]["is_user"]]
    assert {event["players"][0]["id"] for event in settled} == {wire[0], wire[1]}
    assert story["wednesday"]["game"]["pending"]["claims"] == 0 and story["wednesday"]["game"]["phase"] == "open"
    assert (story["wednesday"]["step"], story["wednesday"]["week"]) == ("wednesday", 2)


def test_a_lopsided_trade_is_declined_with_reasons_and_changes_nothing(story):
    verdict, proposed = story["evaluate"]["verdict"], story["propose"]

    assert set(story["evaluate"]) == {"verdict"} and verdict["accepted"] is False
    assert any("Value is too far apart" in reason for reason in verdict["reasons"]) and verdict["suggestion"]
    assert verdict["fair_label"] == "big_win" and verdict["value_get"] > 5 * verdict["value_give"]
    assert [row["player"]["id"] for row in verdict["give"]] == story["deal"]["give"]
    assert set(proposed) == {"game", "events", "verdict", "executed", "counter", "reply"} and proposed["verdict"] == verdict
    countered = proposed["counter"]
    assert countered is None or (countered["counter_to"], countered["status"]) == (declined_id(story), "pending")
    assert (proposed["executed"], proposed["events"]) == (False, [])
    declined = [row for row in story["trades"]["results"] if row["status"] == "declined"]
    assert len(declined) == 1 and declined[0]["to_team"]["team_id"] == PARTNER      # asked twice, kept once
    assert [player["id"] for player in declined[0]["get"]] == story["deal"]["get"]


def test_cpu_offers_are_previewed_and_answered(story):
    answers = story["answers"]

    assert len(answers) >= 2                                     # this seed's CPUs make offers
    for accept, offer, answer in answers:
        assert set(answer) == {"game", "events", "accepted", "expired", "message", "verdict", "counter"}
        assert offer["status"] == "pending" and offer["to_team"]["is_user"] and (offer["verdict"] or offer["note"])
        if not accept:
            assert (answer["accepted"], answer["expired"], answer["events"]) == (False, False, [])
        elif answer["accepted"]:
            assert answer["events"][0]["kind"] == "trade" and offer["verdict"]["accepted"]
        else:
            assert answer["expired"] and answer["message"].startswith("That offer no longer stands")
    history = {row["id"]: row["status"] for row in story["final"]["trades"]["results"]}
    assert all(history[offer["id"]] in ("accepted", "declined", "expired") for _, offer, _ in answers)


def declined_id(story) -> int:
    return next(row["id"] for row in story["trades"]["results"] if row["status"] == "declined")


def test_a_cpu_offer_can_be_countered(story):
    offer, answer = story["counter"]
    history = {row["id"]: row for row in story["final"]["trades"]["results"]}

    assert set(answer) == {"game", "events", "verdict", "executed", "counter", "reply"}
    assert history[offer["id"]]["status"] == "countered"
    assert any(row["counter_to"] == offer["id"] for row in history.values())    # the user's own deal, kept


def test_time_only_moves_forward_one_step_at_a_time(story):
    assert story["stale"] == (409, "This league is in week 1, not week 2. Reload it.")
    assert story["wrong_step"][0] == 422 and "play week 1 instead" in story["wrong_step"][1]
    assert story["after_end"] == (422, "This season is over.")


def test_an_action_from_a_page_showing_an_old_week_is_refused(story):
    assert story["stale_actions"] == [(409, "This league is in week 2, not week 1. Reload it.")] * 7


def test_a_finished_season_refuses_every_move_and_shows_no_moods(story):
    assert story["over"] == [(422, "This season is over.")] * 3
    assert not _moods(story["final"]["playoffs"])


def _moods(value) -> list:
    """Every non-null "mood" anywhere in a response."""
    if isinstance(value, dict):
        return [m for key, item in value.items() for m in ([item] if key == "mood" and item else _moods(item))]
    return [m for item in value for m in _moods(item)] if isinstance(value, list) else []


def test_every_week_is_played_to_a_champion(story):
    final, played = story["final"], story["played"]

    assert len(played) == 16 and all(sum(e["kind"] == "result" for e in step["events"]) == 6 for step in played)
    assert [step["week"] for step in played] == list(range(1, 17)) and played[0]["step"] == "play"
    assert played[0]["game"]["phase"] == "waivers" and played[0]["game"]["next_action"]["step"] == "wednesday"
    assert played[-1]["events"][-1]["kind"] == "champion" and played[-1]["game"]["phase"] == "complete"
    summary, table = final["summary"], final["standings"]
    assert summary["next_action"] == {"step": "done", "label": "Season complete"}
    assert table["results"][0]["team_id"] == summary["champion"]["team_id"]
    assert all(row["played"] and row["result"] in "WLT" for row in final["schedule"]["results"])
    assert summary["record"]["place"] == next(r["place"] for r in table["results"] if r["is_user"])


def test_responses_have_the_documented_keys(story):
    final = story["final"]

    assert set(final["team"]["results"][0]) == PLAYER_ROW | {"slot", "starting"}
    assert set(final["matchup"]) == {"week", "played", "playoff", "label", "winner", "home", "away"}
    assert set(final["scoreboard"]["results"][0]["home"]) == SIDE
    assert set(final["standings"]) == {"results", "regular_season_weeks", "playoff_teams", "playoff_weeks", "byes",
                                       "seeds_final"}
    assert (final["standings"]["playoff_weeks"], final["standings"]["byes"]) == ([15, 16], 0)
    assert set(final["activity"]) == {"total", "limit", "offset", "results"} and final["activity"]["limit"] == 50
    chat = final["chat"]
    assert chat["total"] == final["summary"]["chat"] > 0 and chat["results"]
    assert set(chat["results"][0]) == {"id", "week", "team", "to_user", "kind", "text", "players"}
    assert [row["id"] for row in chat["results"]] == sorted((row["id"] for row in chat["results"]), reverse=True)
    assert all(not row["team"]["is_user"] and "{" not in row["text"] for row in chat["results"])
    assert any(row["to_user"] for row in chat["results"])
    assert set(final["players"]["results"][0]) == WIRE_ROW and final["players"]["week"] == 16
    assert set(final["claims"]) == {"phase", "can_claim", "priority", "waiver_order", "results", "faab"}
    assert final["claims"]["faab"] is None                     # a "wednesday" league
    assert set(final["trades"]) == {"open", "deadline_week", "max_players", "offers", "results", "shops"}
    assert set(final["draft"]["picks"][0]) == {"overall", "round", "team", "player"}


def test_games_are_listed_and_can_be_deleted(client, story):
    extra = post(client, "/games", {"season": SEASON, "seed": 2})["game"]["id"]

    listed = get(client, "/games")["results"]
    deleted = post(client, f"/games/{extra}/delete")

    finished = next(row for row in listed if f"/games/{row['id']}" == story["path"])
    assert set(finished) == {"id", "name", "season", "phase", "week", "teams", "scoring", "champion", "created_at",
                             "updated_at", "kind"}
    assert (finished["phase"], finished["champion"]) == ("complete", story["final"]["summary"]["champion"]["name"])
    assert listed[0]["id"] == extra and listed[0]["phase"] == "draft"          # most recently played first
    assert deleted == {"deleted": extra} and refused(client, f"/games/{extra}", method="GET")[0] == 404
    assert refused(client, f"/games/{extra}/delete")[0] == 404
    assert extra not in [row["id"] for row in get(client, "/games")["results"]]


# ---------------- creating games, and errors ----------------

def test_a_random_season_and_draft_slot_come_from_the_seed(client):
    first = post(client, "/games", {"season": "random", "seed": 3, "teams": 10, "scoring": "half"})["game"]
    again = post(client, "/games", {"season": "random", "seed": 3, "teams": 10})["game"]

    assert first["season"] == SEASON and first["name"] == f"{SEASON} Replay" and first["settings"]["teams"] == 10
    assert first["user_team_id"] == again["user_team_id"] and 1 <= first["user_team_id"] <= 10
    assert first["on_the_clock"] == first["user_team_id"]


def test_a_random_season_only_comes_from_seasons_long_enough_for_the_window(client, monkeypatch):
    ready = {SEASON + 1: 18, SEASON + 2: 18, SEASON: 17}         # season -> last week
    monkeypatch.setattr(replay_pool, "ready_last_weeks", lambda session: ready)
    picked = {seed: replay_routes._season(None, "random", seed, 18)[0] for seed in range(20)}

    assert set(picked.values()) == {SEASON + 1, SEASON + 2}      # never the 17-week season
    assert picked == {seed: replay_routes._season(None, "random", seed, 18)[0] for seed in range(20)}   # by seed
    ready = {SEASON: 17}
    assert refused(client, "/games", {"season": "random", "playoff_end_week": 18}) == (
        422, "No prepared season has a week 18: pick an earlier championship week.")
    assert post(client, "/games", {"season": "random", "seed": 3})["game"]["season"] == SEASON


def test_create_says_what_is_wrong_with_a_league(client, monkeypatch):
    def create(**body) -> tuple[int, str]:
        return refused(client, "/games", {"season": SEASON, **body})

    assert create(season=2010) == (422, "Replay seasons run from 2018 to 2025, plus 2026 simulated; not 2010.")
    assert create(teams=9)[0] == 422 and "A league has 4, 6, 8, 10, 12, 14 or 16 teams" in create(teams=9)[1]
    assert create(teams=8, draft_slot=9) == (422, "Draft slot must be between 1 and 8.")
    assert create(teams=99)[0] == create(scoring="superflex")[0] == create(season="soon")[0] == 422
    assert create(season=2019)[0] == 503 and "2019 is not ready" in create(season=2019)[1]
    assert create(scoring="ppr")[0] == 200                       # the same season in another scoring
    monkeypatch.setattr(replay_pool, "ready_last_weeks", lambda session: {})
    assert create(season="random") == (503, "No season is ready to play yet: prepare one first.")


@pytest.mark.parametrize("body, regular, playoff_teams, end", [
    ({}, 14, 6, 17),                                             # 12 teams: 6 make it, final in week 17
    ({"playoff_teams": 4}, 15, 4, 17), ({"playoff_teams": 6}, 14, 6, 17),
    ({"playoff_end_week": 16, "playoff_teams": 8}, 13, 8, 16), ({"teams": 4}, 15, 4, 17),
    ({"teams": 16, "playoff_end_week": 16}, 13, 8, 16), ({"teams": 8, "playoff_teams": 5}, 14, 5, 17),
])
def test_the_playoff_window_sets_the_regular_season(client, body, regular, playoff_teams, end):
    game = post(client, "/games", {"season": SEASON, "seed": 1, **body})["game"]

    shown = game["settings"]
    assert (shown["regular_season_weeks"], shown["playoff_teams"], game["end_week"]) == (regular, playoff_teams, end)


def test_a_playoff_window_the_season_cannot_hold_is_refused(client):
    def create(**body) -> tuple[int, str]:
        return refused(client, "/games", {"season": SEASON, **body})

    too_long = create(playoff_end_week=18)                       # the fake season ends in week 17
    assert too_long[0] == 422 and "has 17 weeks" in too_long[1] and "too long" in too_long[1]
    assert create(playoff_end_week=15)[0] == create(playoff_teams=3)[0] == create(playoff_teams=9)[0] == 422
    assert create(teams=6, playoff_teams=8) == (422, "Playoff teams must be between 4 and 6.")


def test_the_draft_board_sorts_by_rank_adp_or_projection(client):
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": 1, "teams": 10})["game"]["id"]

    def board(**params) -> list[dict]:
        return get(client, game + "/draft", limit=200, **params)["results"]

    ranked, by_adp, best, worst = board(), board(sort="adp"), board(sort="projection"), board(
        sort="projection", direction="asc")
    ranks = [row["pre_rank"] for row in ranked]
    known = [rank for rank in ranks if rank is not None]
    assert known == sorted(known) and ranks[:len(known)] == known
    assert all(row["round"] == -(-row["pre_rank"] // 10) for row in ranked if row["pre_rank"] and row["pre_rank"] <= 150)
    assert all(row["round"] is None for row in ranked if (row["pre_rank"] or 0) > 150)       # past the 15th round
    adps = [row["adp"] for row in by_adp if row["adp"] is not None]
    assert adps == sorted(adps) and len(adps) > 100
    assert [row["projection"] for row in best] == sorted((row["projection"] for row in best), reverse=True)
    assert [row["projection"] for row in worst] == sorted(row["projection"] for row in worst)
    late = [row["adp"] for row in board(sort="adp", direction="desc")]
    assert late == sorted(late, reverse=True) and late[0] > max(adps)          # the first page by ADP stops early
    assert refused(client, game + "/draft", method="GET", sort="value")[0] == 422
    assert refused(client, game + "/draft", method="GET", direction="up")[0] == 422


def test_unknown_games_players_and_teams_are_reported(client, story):
    game = story["path"]

    for path in ("", "/draft", "/team", "/matchup", "/scoreboard", "/standings", "/schedule", "/activity", "/players",
                 "/players/1", "/claims", "/trades"):
        assert refused(client, "/games/999999" + path, method="GET") == (404, "No replay league 999999.")
    for path, body in (("/draft/pick", {"player_id": 1}), ("/draft/auto", None), ("/lineup", {"starters": None}),
                       ("/claims", {"add": 1}), ("/claims/1/cancel", None), ("/claims/reorder", {"ids": []}),
                       ("/claims/1/bid", {"bid": 1}),
                       ("/add-drop", {"add": 1}), ("/drop", {"player_id": 1}), ("/ir/place", {"player_id": 1}),
                       ("/ir/activate", {"player_id": 1}),
                       ("/advance", {"step": "play", "week": 1}),
                       ("/trades/evaluate", {"to_team": 1, "give": [1], "get": [2]}),
                       ("/trades/propose", {"to_team": 1, "give": [1], "get": [2]}),
                       ("/trades/offers/1/respond", {"accept": True}),
                       ("/trades/offers/1/counter", {"give": [1], "get": [2]}), ("/delete", None)):
        assert refused(client, "/games/999999" + path, body)[0] == 404, path
    assert refused(client, game + "/players/5", method="GET")[0] == 404
    assert refused(client, game + "/team", method="GET", team_id=99) == (422, "No team 99 in this league.")
    assert refused(client, game + "/team", method="GET", week=17)[0] == 422
    for query in ({"limit": 0}, {"limit": 500}, {"offset": -1}, {"sort": "luck"}, {"status": "hurt"},
                  {"position": "LB"}):
        assert refused(client, game + "/players", method="GET", **query)[0] == 422, query
    assert refused(client, game + "/activity", method="GET", kind="gossip")[0] == 422
    assert refused(client, game + "/drop", {"player_id": 0})[0] == 422


def test_a_played_games_box_score_has_the_real_final_score(client, story):
    game, played = story["path"], next(g for g in DATA.schedule.games if g.week == 2)

    shown = get(client, game + "/box", week=2, team=played.away_team)

    assert [(side["team"], side["score"]) for side in shown["teams"]] == [
        (played.away_team, DATA.scores[(played.away_team, 2)]), (played.home_team, DATA.scores[(played.home_team, 2)])]
    assert all(side["players"] for side in shown["teams"])
    for query in ({"week": 2, "team": "XYZ"}, {"week": 21, "team": played.home_team}, {"week": 0, "team": "KC"},
                  {"week": 2}, {"team": played.home_team}):
        assert refused(client, game + "/box", method="GET", **query)[0] == 422, query
    assert refused(client, "/games/999999/box", method="GET", week=2, team="KC")[0] == 404


def test_the_wire_sorts_any_column_either_way_and_shows_the_chosen_stats(client, story):
    game = story["path"]

    def listed(**query) -> dict:
        return get(client, game + "/players", status="all", limit=200, **query)

    week = listed(stats="week", stat_week=2, sort="line_points", dir="desc")
    asc = listed(sort="name", dir="asc")["results"]

    assert (week["stats"], week["stat_week"]) == ("week", 2) and week["results"][0]["line"]["week"] == 2
    points = [row["line"]["points"] for row in week["results"]]
    known = [p for p in points if p is not None]
    assert known == sorted(known, reverse=True) and points == known + [None] * (len(points) - len(known))
    assert [row["player"]["name"].lower() for row in asc] == sorted(row["player"]["name"].lower() for row in asc)
    assert listed(sort="rec", dir="asc")["total"] == listed()["total"]
    assert listed(stats="last4")["results"][0]["line"]["span"] == "last4"
    for query in ({"dir": "up"}, {"stats": "career"}, {"stats": "week", "stat_week": 0},
                  {"stats": "week", "stat_week": 23}, {"sort": "luck"}):
        assert refused(client, game + "/players", method="GET", **query)[0] == 422, query
    detail = get(client, game + "/players/%d" % asc[0]["player"]["id"])
    assert {"upcoming", "defense_basis"} <= set(detail) and set(detail["defense_basis"]) == {"weeks", "prior"}


def test_a_game_id_beyond_sqlite_integers_is_a_422_not_a_500(client):
    huge = "/games/99999999999999999999"

    assert refused(client, huge, method="GET")[0] == 422
    assert refused(client, huge + "/draft/auto")[0] == 422
    assert refused(client, "/games/0", method="GET")[0] == 422
    assert refused(client, f"/games/{2 ** 63 - 1}", method="GET")[0] == 404


def test_a_repeated_draft_request_with_the_pick_number_is_refused(client):
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": SEED, "draft_slot": SLOT})["game"]["id"]
    overall = SLOT                                              # the user is on the clock at their first pick

    first = post(client, game + "/draft/auto", overall=overall)
    again = refused(client, game + "/draft/auto", overall=overall)
    stale_pick = refused(client, game + "/draft/pick", {"player_id": 1, "overall": overall})

    mine = [p for p in get(client, game + "/draft")["picks"] if p["team"]["team_id"] == SLOT]
    assert first["events"][0]["team"]["is_user"] and len(mine) == 1
    next_pick = 2 * 12 - SLOT + 1
    assert again == (409, f"The draft is at pick {next_pick}, not pick {overall}. Reload it.")
    assert stale_pick[0] == 409
    post(client, game + "/draft/auto")                          # without `overall`: no check, as before
    post(client, game + "/draft/auto")
    assert len([p for p in get(client, game + "/draft")["picks"] if p["team"]["team_id"] == SLOT]) == 3


def test_the_replay_lock_survives_build_lock_eviction():
    from api.replay_routes import game_lock
    from services import build_locks

    lock = game_lock(7)
    held = [build_locks.key_lock(("other", n)) for n in range(build_locks.MAX_LOCKS + 1)]

    assert held and game_lock(7) is lock


def test_a_drop_and_a_season_without_waivers(client):
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": 4, "waiver_mode": "none"})["game"]["id"]
    post(client, game + "/draft/auto", whole_draft="true")
    bench = _ids(get(client, game + "/team")["results"], "BN")

    dropped = post(client, game + "/drop", {"player_id": bench[0]})
    status = get(client, f"{game}/players/{bench[0]}")["player"]["status"]
    played = post(client, game + "/advance", {"step": "play", "week": 1})

    assert [event["kind"] for event in dropped["events"]] == ["drop"] and status == "waivers"
    assert (played["game"]["phase"], played["game"]["week"]) == ("open", 2)        # no Wednesday step
    assert refused(client, game + "/advance", {"step": "wednesday", "week": 2})[0] == 422
    assert refused(client, game + "/drop", {"player_id": bench[0]})[0] == 422


def test_a_faab_league_takes_bids_on_claims(client):
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": 6, "waiver_mode": "faab"})["game"]["id"]
    post(client, game + "/draft/auto", whole_draft="true")
    post(client, game + "/advance", {"step": "play", "week": 1})
    add = get(client, game + "/players", limit=1)["results"][0]["player"]["id"]
    cut = _ids(get(client, game + "/team")["results"], "BN")[0]

    bad = [refused(client, game + "/claims", {"add": add, "drop": cut, "bid": bid})[0] for bid in (-1, 101)]
    missing = refused(client, game + "/claims", {"add": add, "drop": cut})
    claimed = post(client, game + "/claims", {"add": add, "drop": cut, "bid": 10})
    claim_id = get(client, game + "/claims")["results"][0]["id"]
    rebid = post(client, f"{game}/claims/{claim_id}/bid", {"bid": 100})       # all in: no CPU outbids it
    bad_rebid = [refused(client, f"{game}/claims/{claim_id}/bid", {"bid": bid})[0] for bid in (-1, 101)]
    after = post(client, game + "/advance", {"step": "wednesday", "week": 2})
    too_late = refused(client, f"{game}/claims/{claim_id}/bid", {"bid": 5})

    assert bad == bad_rebid == [422, 422]
    assert missing == (422, "Enter a bid: $0 to $100 (your budget left).")
    assert set(claimed) >= {"game", "events"} and set(rebid) >= {"game", "events"}
    mine = [e["text"] for e in after["events"] if e["kind"] == "claim" and e["team"]["is_user"]]
    assert len(mine) == 1 and " for $100, dropping " in mine[0]                    # the rebid is what was paid
    assert too_late == (422, "Bids can only be changed before the weekly waiver run.")
    wednesday = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": 6})["game"]["id"]
    post(client, wednesday + "/draft/auto", whole_draft="true")
    free = get(client, wednesday + "/players", status="free_agent", limit=1)["results"][0]["player"]["id"]
    mine = _ids(get(client, wednesday + "/team")["results"], "BN")[0]
    assert refused(client, wednesday + "/add-drop", {"add": free, "drop": mine, "bid": 50})[0] == 422   # no bids on adds
    assert post(client, wednesday + "/add-drop", {"add": free, "drop": mine})["events"]


def test_two_requests_cannot_play_the_same_week_twice(tmp_path_factory):
    url = f"sqlite:///{(tmp_path_factory.mktemp('replay') / 'lock.db').as_posix()}"
    barrier, answers = threading.Barrier(2), []

    for locked in _client(DATA, url):
        game = _idle_to(locked, 1)

        def play() -> None:
            barrier.wait(10)
            answers.append(locked.post(API + game + "/advance", json={"step": "play", "week": 1}).status_code)

        threads = [threading.Thread(target=play) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        after = get(locked, game)

    assert sorted(answers) == [200, 409] and (after["week"], after["record"]["wins"] + after["record"]["losses"]
                                             + after["record"]["ties"]) == (2, 1)


def test_players_go_on_ir_and_come_back_through_the_api(client, tmp_path_factory):
    """The same league on a season where two of the user's bench players are tagged Out in week 1."""
    rows = get(client, _idle_to(client, 1) + "/team")["results"]
    first, second = [row["player"] for row in rows if row["slot"] == "BN" and row["player"]["position"] in SKILL][:2]
    kicker = next(row["player"] for row in rows if row["slot"] == "K")
    hurt = fakes.tagged(DATA, {(p["id"], 1): fakes.Injury("O", "DNP", "Knee") for p in (first, second)})
    url = f"sqlite:///{(tmp_path_factory.mktemp('replay') / 'ir.db').as_posix()}"

    for tagged in _client(hurt, url):
        game = _idle_to(tagged, 1)
        size = get(tagged, game)["settings"]["roster_size"]
        placed = post(tagged, game + "/ir/place", {"player_id": first["id"]})
        on_ir = get(tagged, game + "/team")
        taken = refused(tagged, game + "/ir/place", {"player_id": second["id"]})
        healthy = refused(tagged, game + "/ir/place", {"player_id": kicker["id"]})
        swapped = post(tagged, game + "/ir/activate", {"player_id": first["id"], "swap": second["id"]})
        free = get(tagged, game + "/players", position="K", status="free_agent", limit=1)["results"][0]["player"]
        post(tagged, game + "/add-drop", {"add": free["id"]})
        full = refused(tagged, game + "/ir/activate", {"player_id": second["id"]})
        activated = post(tagged, game + "/ir/activate", {"player_id": second["id"], "drop": free["id"]})
        moves = get(tagged, game + "/activity", kind="ir")
        bad = [refused(tagged, game + path, body)[0] for path, body in (
            ("/ir/place", {}), ("/ir/place", {"player_id": 0}), ("/ir/activate", {"player_id": 1, "drop": 0}),
            ("/ir/activate", {"player_id": 1, "swap": 0}))]
        after = get(tagged, game + "/team")

    tag = {"tag": "O", "practice": "DNP", "injury": "Knee"}
    assert set(placed) == {"game", "events"} and [e["kind"] for e in placed["events"]] == ["ir"]
    assert placed["events"][0]["text"] == f"Your Team moved {first['name']} to IR"
    assert placed["events"][0]["players"] == [{**first, "injury": tag}] and placed["game"]["ir_blocked"] == []
    assert [row["player"]["id"] for row in on_ir["ir"]] == [first["id"]] and len(on_ir["results"]) == size - 1
    assert taken == (422, f"Your Team's IR slot is taken by {first['name']}: activate or drop him first.")
    assert healthy == (422, f"{kicker['name']} can't go on IR: only a player tagged O, IR or PUP this week can.")
    assert [e["kind"] for e in swapped["events"]] == ["ir"]
    assert [p["id"] for p in swapped["events"][0]["players"]] == [first["id"], second["id"]]
    assert swapped["events"][0]["text"] == f"Your Team activated {first['name']} from IR and moved {second['name']} to IR"
    assert full == (422, f"The roster is full ({size} players): pick a player to drop to activate {second['name']}.")
    assert [e["kind"] for e in activated["events"]] == ["ir"]          # one event for the whole move
    assert [p["id"] for p in activated["events"][0]["players"]] == [second["id"], free["id"]]
    assert moves["total"] == 3 and {e["kind"] for e in moves["results"]} == {"ir"}
    assert bad == [422] * 4 and after["ir"] == [] and len(after["results"]) == size


# ---------------- no future information ----------------

def _idle_to(client: TestClient, week: int) -> str:
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": SEED, "draft_slot": SLOT})["game"]["id"]
    post(client, game + "/draft/auto", whole_draft="true")
    for now in range(1, week):
        post(client, game + "/advance", {"step": "play", "week": now})
        post(client, game + "/advance", {"step": "wednesday", "week": now + 1})
    return game


def test_an_offer_that_no_longer_stands_is_saved_as_expired(client):
    game = _idle_to(client, 2)
    for week in range(2, 11):                                   # on to the first week a CPU makes an offer
        if get(client, game + "/trades")["offers"]:
            break
        post(client, game + "/advance", {"step": "play", "week": week})
        post(client, game + "/advance", {"step": "wednesday", "week": week + 1})
    offer = get(client, game + "/trades")["offers"][0]
    post(client, game + "/drop", {"player_id": offer["get"][0]["id"]})     # the player the CPU asked for: the
                                                                           # drop expires the offer at once
    trades = get(client, game + "/trades")

    assert trades["offers"] == [] and trades["results"][0]["status"] == "expired"
    assert refused(client, f"{game}/trades/offers/{offer['id']}/respond", {"accept": True}) == (
        422, "That offer is already expired.")


def test_responses_before_a_week_show_none_of_its_real_points(story):
    snapshot = story["snapshot"]

    rows = [row for name, screen in snapshot.items() if name.startswith("team") for row in screen["results"]]
    rows += snapshot["players"]["results"] + snapshot["matchup"]["home"]["results"]
    assert len(rows) > 500 and all(row["points"] is None for row in rows)
    assert all(row["player"]["injury"] == _tag(row["player"]["id"], LEAK_WEEK) for row in rows)   # this week's tag
    for row in snapshot["players"]["results"]:
        pid = row["player"]["id"]
        so_far = [DATA.actual[(pid, week)] for week in range(1, LEAK_WEEK) if (pid, week) in DATA.actual]
        assert row["season_points"] == pytest.approx(sum(so_far), abs=0.01)
        assert row["last_points"] == DATA.actual.get((pid, LEAK_WEEK - 1)) and row["games"] == len(so_far)
    pages = [screen for name, screen in snapshot.items() if name.startswith("player ")]
    assert len(pages) >= 10
    for detail in pages:
        assert [week["week"] for week in detail["weeks"]] == list(range(1, LEAK_WEEK + 1))
        assert detail["weeks"][-1]["played"] is False and detail["weeks"][-1]["points"] is None
        assert [week["injury"] for week in detail["weeks"]] == [_tag(detail["player"]["player"]["id"], week)
                                                                for week in range(1, LEAK_WEEK + 1)]
    traded, breakout = (snapshot[f"player {pid}"] for pid in (fakes.TRADED_ID, fakes.BREAKOUT_ID))
    assert (traded["player"]["player"]["injury"]["tag"], breakout["weeks"][2]["injury"]["tag"]) == ("D", "Q")


def _tag(player_id: int, week: int) -> dict | None:
    """The fake season's tag as the API shows it."""
    tag = DATA.injuries.get((player_id, week))
    return tag and {"tag": tag.tag, "practice": tag.practice, "injury": tag.injury}


def test_responses_are_identical_when_the_unplayed_weeks_are_different(client, story, tmp_path_factory):
    """The same league on a season whose real points from LEAK_WEEK on, and projections after it, differ."""
    shaken = fakes.future_changed(DATA, after_week=LEAK_WEEK)
    hidden = fakes.patched(shaken, actual={key: points + 7 for key, points in DATA.actual.items()
                                           if key[1] == LEAK_WEEK})
    url = f"sqlite:///{(tmp_path_factory.mktemp('replay') / 'twin.db').as_posix()}"
    ours = _idle_to(client, LEAK_WEEK)

    for twin in _client(hidden, url):
        theirs = _idle_to(twin, LEAK_WEEK)
        mine, other = _snapshot(client, ours), _snapshot(twin, theirs)
        before = [name for name in mine if json.dumps(mine[name]) != json.dumps(other[name])]
        boards = [post(c, game + "/advance", {"step": "play", "week": LEAK_WEEK}) and get(c, game + "/scoreboard",
                                                                                           week=LEAK_WEEK)
                  for c, game in ((client, ours), (twin, theirs))]

    assert before == [] and len(mine) > 20
    assert boards[0]["played"] and boards[0] != boards[1]        # the probe is live once the week is played


# ---------------- preparing a season ----------------

def test_prepare_runs_in_the_background_and_reports_its_status(client, story, monkeypatch):
    calls = []

    def fake_prepare(session, season, force=False, on_step=None, **_) -> dict:
        calls.append((season, force))
        return {"season": season, "errors": {}}

    monkeypatch.setattr(replay_data, "prepare_season", fake_prepare)

    started = post(client, f"/seasons/{SEASON}/prepare", force="true")
    for thread in threading.enumerate():
        if thread.name == "replay-prepare":
            thread.join(5)
    status = get(client, "/seasons")["prepare"]

    assert (started["season"], started["status"]) == (SEASON, "running") and calls == [(SEASON, True)]
    assert (status["season"], status["status"], status["error"]) == (SEASON, "done", None)
    assert refused(client, "/seasons/2010/prepare")[0] == refused(client, "/seasons/2030/prepare")[0] == 422


def test_a_prepared_season_is_read_again(monkeypatch):
    cleared = []
    for step in ("load_game_results", "load_snaps", "load_adp", "load_expert_ranks", "load_projections",
                 "load_injuries"):
        monkeypatch.setattr(replay_data, step, lambda *args, **kwargs: {})
    monkeypatch.setattr(replay_data, "ensure_defenses", lambda session: 32)
    monkeypatch.setattr(replay_pool, "clear_cache", lambda: cleared.append(True))

    summary = replay_data.prepare_season(None, SEASON)

    assert cleared == [True] and summary["errors"] == {}


def test_a_lineup_body_bounds_each_slot_list():
    from pydantic import ValidationError

    from api.replay_action_routes import MAX_LISTED, LineupBody

    with pytest.raises(ValidationError):
        LineupBody(starters={"QB": list(range(1, MAX_LISTED + 2))})


def test_an_add_drop_body_refuses_a_bid():
    from pydantic import ValidationError

    from api.replay_action_routes import AddBody

    with pytest.raises(ValidationError):
        AddBody(add=1, bid=5)


def test_a_proposal_for_a_player_in_a_cpus_ir_slot_is_answered_like_any_other(client, monkeypatch):
    """The counter search once looked for his owner on active rosters only and crashed (a 500)."""
    from services import replay_counter
    monkeypatch.setattr(replay_counter.replay_personas, "counter_rate", lambda persona: 1.0)
    game = post(client, "/games", {"season": SEASON, "seed": SEED, "teams": 12, "draft_slot": 12})["game"]["id"]
    post(client, f"/games/{game}/draft/auto", whole_draft="true")
    held = None
    while held is None:
        summary = get(client, f"/games/{game}")
        assert summary["week"] <= fakes.INJURED_WEEKS[-1], "no CPU put anyone on IR"
        for team in (t for t in summary["teams"] if not t["is_user"]):
            ir = get(client, f"/games/{game}/team", team_id=team["team_id"])["ir"]
            held = held or (team["team_id"], ir[0]["player"]["id"]) if ir else held
        if held is None:
            post(client, f"/games/{game}/advance", {"step": summary["next_action"]["step"], "week": summary["week"]})
    mine = get(client, f"/games/{game}/team")["results"]
    scrub = min((row for row in mine if row["player"]["position"] in SKILL), key=lambda row: row["value"])["player"]["id"]

    answer = post(client, f"/games/{game}/trades/propose",
                  {"to_team": held[0], "give": [scrub], "get": [held[1]], "week": get(client, f"/games/{game}")["week"]})
    assert answer["executed"] is False and answer["verdict"]["reasons"]


@pytest.mark.parametrize("raw", ['{"season": 2024, "teams": NaN}', '{"season": Infinity}', '{"season": 2024, "seed": 1e400}',
                                 '{"season": 2024, "scoring_rules": {"reception": "NaN", "te_premium": 0, '
                                 '"pass_td": 4, "turnover": -2}}'])
def test_a_nan_or_infinity_in_a_body_is_a_422_that_says_so(client, raw):
    """Breaker: the default validation handler echoed the value back and could not encode it (a 500)."""
    response = client.post(API + "/games", content=raw, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert any(error["msg"] == "Input should be a finite number" for error in response.json()["detail"])


def test_a_league_name_loses_control_bidi_and_zero_width_characters(client):
    name = "Bad\x00 Bell\x07 ‮Kings​"

    created = post(client, "/games", {"season": SEASON, "seed": 3, "name": name})

    assert created["game"]["name"] == "Bad Bell Kings"


def test_roster_moves_and_weeks_out_of_range_say_what_is_wrong(client):
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": 3})["game"]["id"]
    one = get(client, game + "/players", limit=1)["results"][0]["player"]["id"]

    claim = refused(client, game + "/claims", {"add": one})
    add = refused(client, game + "/add-drop", {"add": one})

    assert claim == add == (422, "Waiver claims and free-agent adds open once the draft is over.")


def test_a_matchup_for_week_zero_names_the_weeks_that_can_be_shown(client):
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": 3})["game"]["id"]
    post(client, game + "/draft/auto", whole_draft="true")
    post(client, game + "/advance", {"step": "play", "week": 1})

    assert refused(client, game + "/matchup", method="GET", week=0) == (422, "Week 0 can't be shown: pick a week from 1 to 2.")


def test_inf_sent_to_a_choice_field_lists_the_choices_not_a_number_rule(client):
    response = client.post(API + "/games", json={"season": 2024, "scoring": "inf"})

    assert response.status_code == 422
    assert all(error["msg"] != "Input should be a finite number" for error in response.json()["detail"])


@pytest.mark.parametrize("raw", ['{"season": ' + "[" * 600 + "]" * 600 + "}",
                                 '{"season": "' + chr(92) + 'ud800"}',          # JSON's escaped lone surrogate
                                 '{"season": "' + "x" * 200_000 + '"}'])
def test_a_deep_odd_or_huge_rejected_input_is_a_small_422(client, raw):
    """Security review: 600 levels deep raised RecursionError (500), a lone surrogate could not be encoded (500),
    and a huge string came back whole."""
    response = client.post(API + "/games", content=raw, headers={"content-type": "application/json"})

    assert response.status_code == 422 and len(response.content) < 5_000


def test_a_league_name_loses_separators_and_blank_letters_but_keeps_joined_emoji():
    from api.replay_routes import clean_name
    family = "\U0001F468‍\U0001F469‍\U0001F467"

    assert clean_name("A B C") == "ABC"
    assert clean_name("ㅤ⠀ ㅤ") == ""                  # blank: the create route falls back to the default
    assert clean_name(f"Team {family}") == f"Team {family}"
    assert clean_name("a‍b") == "ab"


def test_a_league_name_that_is_blank_after_cleaning_gets_the_default(client):
    created = post(client, "/games", {"season": SEASON, "seed": 3, "name": "ㅤ⠀"})

    assert created["game"]["name"] == f"{SEASON} Replay"


def test_a_league_name_keeps_an_emoji_newer_than_the_runtimes_unicode_data():
    import unicodedata

    from api.replay_routes import clean_name
    newest = next(chr(cp) for cp in range(0x1FAE8, 0x1FAFF) if unicodedata.category(chr(cp)) == "Cn")

    assert clean_name(f"Shakers {newest}") == f"Shakers {newest}"


def test_a_nan_nested_in_a_list_is_echoed_as_text_in_a_422(client):
    from api.validation import _safe

    response = client.post(API + "/games", content='{"season": [1, [NaN, Infinity]]}',
                           headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert _safe([1, [float("nan"), float("inf")]]) == [1, ["nan", "inf"]]


@pytest.mark.parametrize("raw, cleaned", [
    ("‍‍‍", ""),                                               # joiners only: blank
    ("\U0001F468‍‍\U0001F469", "\U0001F468\U0001F469"),             # a double joiner breaks the pair
    ("️︎͏឴", ""),                                         # selectors and blank marks only
    ("Team \U0001F468‍\U0001F469‍\U0001F467", "Team \U0001F468‍\U0001F469‍\U0001F467"),
    ("\U0001F3F3️‍\U0001F308 Flags", "\U0001F3F3️‍\U0001F308 Flags"),   # selector then joiner
    ("一‍二", "一二"),                                   # no joiner between CJK
    ("Café", "Café"),                                             # a real combining accent stays
])
def test_a_league_name_keeps_joiners_and_selectors_only_inside_emoji(raw, cleaned):
    from api.replay_routes import clean_name

    assert clean_name(raw) == cleaned


def test_the_echo_limits_cut_text_lists_dicts_and_depth():
    from api.validation import MAX_DEPTH, _safe

    deep: list = []
    for _ in range(25):
        deep = [deep]
    flat = _safe(deep)
    for _ in range(MAX_DEPTH):
        flat = flat[0]

    assert len(_safe("x" * 300)) == 203 and _safe("x" * 300).endswith("...")
    assert _safe(list(range(9))) == [0, 1, 2, 3, 4, "..."]
    assert _safe({str(n): n for n in range(7)})["..."] == "2 more"
    assert flat == ["..."]
