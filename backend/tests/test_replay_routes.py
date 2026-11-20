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
SKILL = ("QB", "RB", "WR", "TE")


def _client(data, url: str = "sqlite://") -> Iterator[TestClient]:
    """The real app on its own database holding `data`. (Loaded seasons are cached per database URL, so two
    different seasons need two different URLs.)"""
    engine = create_engine(url, connect_args={"check_same_thread": False},
                           **({"poolclass": StaticPool} if url == "sqlite://" else {}))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fakes.seed_db(session, data)
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
                                              "name": "  Route league "})
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
        post(client, game + "/claims", {"add": add, "drop": drop})
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
    return {**teams, "players": {"results": pool}, "matchup": get(client, game + "/matchup"),
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
            accept = len(seen["answers"]) % 2 == 0
            answer = post(client, f"{game}/trades/offers/{offer['id']}/respond", {"accept": accept})
            seen["answers"].append((accept, offer, answer))
        if week == LEAK_WEEK:
            seen["snapshot"] = _snapshot(client, game)
        seen["played"].append(post(client, game + "/advance", {"step": "play", "week": week}))
    seen["after_end"] = refused(client, game + "/advance", {"step": "play", "week": summary["week"]})
    seen["final"] = {name: get(client, f"{game}/{name}") for name in (
        "team", "matchup", "scoreboard", "standings", "schedule", "activity", "players", "claims", "trades", "draft")}
    seen["final"]["summary"] = summary
    return seen


# ---------------- the whole game ----------------

def test_seasons_report_which_can_be_played(story):
    seasons = story["seasons"]

    assert [row["season"] for row in seasons["results"]] == list(replay_pool.SUPPORTED_SEASONS)
    assert [row["season"] for row in seasons["results"] if row["ready"]] == [SEASON]
    assert set(seasons["results"][0]) >= {"season", "ready", "has_stats", "adp_players", "projection_weeks"}
    assert seasons["prepare"] is None


def test_create_starts_the_draft_at_the_users_first_pick(story):
    created = story["created"]

    game = created["game"]
    assert set(created) == {"game", "events"} and set(game) == SUMMARY
    assert (game["name"], game["season"], game["phase"], game["week"]) == ("Route league", SEASON, "draft", 0)
    assert game["user_team_id"] == game["on_the_clock"] == SLOT and game["next_action"]["step"] == "pick"
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

    assert [event["kind"] for event in events] == ["drop", "add"]
    assert [player["id"] for player in events[1]["players"]] == [swap["add"], swap["drop"]]
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
    assert set(proposed) == {"game", "events", "verdict", "executed"} and proposed["verdict"] == verdict
    assert (proposed["executed"], proposed["events"]) == (False, [])
    declined = [row for row in story["trades"]["results"] if row["status"] == "declined"]
    assert len(declined) == 1 and declined[0]["to_team"]["team_id"] == PARTNER      # asked twice, kept once
    assert [player["id"] for player in declined[0]["get"]] == story["deal"]["get"]


def test_cpu_offers_are_previewed_and_answered(story):
    answers = story["answers"]

    assert len(answers) >= 2                                     # this seed's CPUs make offers
    for accept, offer, answer in answers:
        assert set(answer) == {"game", "events", "accepted", "expired", "message", "verdict"}
        assert offer["status"] == "pending" and offer["to_team"]["is_user"] and (offer["verdict"] or offer["note"])
        if not accept:
            assert (answer["accepted"], answer["expired"], answer["events"]) == (False, False, [])
        elif answer["accepted"]:
            assert answer["events"][0]["kind"] == "trade" and offer["verdict"]["accepted"]
        else:
            assert answer["expired"] and answer["message"].startswith("That offer no longer stands")
    history = {row["id"]: row["status"] for row in story["final"]["trades"]["results"]}
    assert all(history[offer["id"]] in ("accepted", "declined", "expired") for _, offer, _ in answers)


def test_time_only_moves_forward_one_step_at_a_time(story):
    assert story["stale"] == (409, "This league is in week 1, not week 2. Reload it.")
    assert story["wrong_step"][0] == 422 and "play week 1 instead" in story["wrong_step"][1]
    assert story["after_end"] == (422, "This season is over.")


def test_every_week_is_played_to_a_champion(story):
    final, played = story["final"], story["played"]

    assert len(played) == 16 and all(sum(e["kind"] == "result" for e in step["events"]) == 6 for step in played)
    assert [step["week"] for step in played] == list(range(1, 17)) and played[0]["step"] == "play"
    assert played[0]["game"]["phase"] == "waivers" and played[0]["game"]["next_action"]["step"] == "wednesday"
    assert played[-1]["events"][-1]["kind"] == "champion" and played[-1]["game"]["phase"] == "complete"
    summary, table = final["summary"], final["standings"]
    assert summary["next_action"] == {"step": "done", "label": "Season complete"}
    assert summary["champion"] == table["champion"] and table["results"][0]["team_id"] == summary["champion"]["team_id"]
    assert all(row["played"] and row["result"] in "WLT" for row in final["schedule"]["results"])
    assert summary["record"]["place"] == next(r["place"] for r in table["results"] if r["is_user"])


def test_responses_have_the_documented_keys(story):
    final = story["final"]

    assert set(final["team"]["results"][0]) == PLAYER_ROW | {"slot", "starting"}
    assert set(final["matchup"]) == {"week", "played", "playoff", "label", "winner", "home", "away"}
    assert set(final["scoreboard"]["results"][0]["home"]) == SIDE
    assert set(final["standings"]) == {"results", "regular_season_weeks", "playoff_teams", "seeds_final", "bracket",
                                       "champion"}
    assert set(final["activity"]) == {"total", "limit", "offset", "results"} and final["activity"]["limit"] == 50
    assert set(final["players"]["results"][0]) == WIRE_ROW and final["players"]["week"] == 16
    assert set(final["claims"]) == {"phase", "can_claim", "priority", "waiver_order", "results"}
    assert set(final["trades"]) == {"open", "deadline_week", "max_players", "offers", "results"}
    assert set(final["draft"]["picks"][0]) == {"overall", "round", "team", "player"}


def test_games_are_listed_and_can_be_deleted(client, story):
    extra = post(client, "/games", {"season": SEASON, "seed": 2})["game"]["id"]

    listed = get(client, "/games")["results"]
    deleted = post(client, f"/games/{extra}/delete")

    finished = next(row for row in listed if f"/games/{row['id']}" == story["path"])
    assert set(finished) == {"id", "name", "season", "phase", "week", "teams", "scoring", "champion", "created_at",
                             "updated_at"}
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


def test_create_says_what_is_wrong_with_a_league(client, monkeypatch):
    def create(**body) -> tuple[int, str]:
        return refused(client, "/games", {"season": SEASON, **body})

    assert create(season=2010) == (422, "Replay seasons run from 2018 to 2025, not 2010.")
    assert create(teams=9)[0] == 422 and "A league has 8, 10, 12 or 14 teams" in create(teams=9)[1]
    assert create(teams=8, draft_slot=9) == (422, "Draft slot must be between 1 and 8.")
    assert create(teams=99)[0] == create(scoring="superflex")[0] == create(season="soon")[0] == 422
    assert create(season=2019)[0] == 503 and "2019 is not ready" in create(season=2019)[1]
    assert create(scoring="ppr")[0] == 200                       # the same season in another scoring
    monkeypatch.setattr(replay_pool, "ready_seasons", lambda session: [])
    assert create(season="random") == (503, "No season is ready to play yet: prepare one first.")


def test_unknown_games_players_and_teams_are_reported(client, story):
    game = story["path"]

    for path in ("", "/draft", "/team", "/matchup", "/scoreboard", "/standings", "/schedule", "/activity", "/players",
                 "/players/1", "/claims", "/trades"):
        assert refused(client, "/games/999999" + path, method="GET") == (404, "No replay league 999999.")
    for path, body in (("/draft/pick", {"player_id": 1}), ("/draft/auto", None), ("/lineup", {"starters": None}),
                       ("/claims", {"add": 1}), ("/claims/1/cancel", None), ("/claims/reorder", {"ids": []}),
                       ("/add-drop", {"add": 1}), ("/drop", {"player_id": 1}),
                       ("/advance", {"step": "play", "week": 1}),
                       ("/trades/evaluate", {"to_team": 1, "give": [1], "get": [2]}),
                       ("/trades/propose", {"to_team": 1, "give": [1], "get": [2]}),
                       ("/trades/offers/1/respond", {"accept": True}), ("/delete", None)):
        assert refused(client, "/games/999999" + path, body)[0] == 404, path
    assert refused(client, game + "/players/5", method="GET")[0] == 404
    assert refused(client, game + "/team", method="GET", team_id=99) == (422, "No team 99 in this league.")
    assert refused(client, game + "/team", method="GET", week=17)[0] == 422
    for query in ({"limit": 0}, {"limit": 500}, {"offset": -1}, {"sort": "luck"}, {"status": "hurt"},
                  {"position": "LB"}):
        assert refused(client, game + "/players", method="GET", **query)[0] == 422, query
    assert refused(client, game + "/activity", method="GET", kind="gossip")[0] == 422
    assert refused(client, game + "/drop", {"player_id": 0})[0] == 422


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


# ---------------- no future information ----------------

def _idle_to(client: TestClient, week: int) -> str:
    game = "/games/%d" % post(client, "/games", {"season": SEASON, "seed": SEED, "draft_slot": SLOT})["game"]["id"]
    post(client, game + "/draft/auto", whole_draft="true")
    for now in range(1, week):
        post(client, game + "/advance", {"step": "play", "week": now})
        post(client, game + "/advance", {"step": "wednesday", "week": now + 1})
    return game


def test_an_offer_that_no_longer_stands_is_saved_as_expired(client):
    game = _idle_to(client, 2)                                   # this seed: an offer arrives in week 2
    offer = get(client, game + "/trades")["offers"][0]
    post(client, game + "/drop", {"player_id": offer["get"][0]["id"]})     # the player the CPU asked for

    answer = post(client, f"{game}/trades/offers/{offer['id']}/respond", {"accept": True})

    assert (answer["accepted"], answer["expired"]) == (False, True) and answer["game"]["pending"]["offers"] == 0
    assert answer["message"].startswith("That offer no longer stands. Not on Your Team's roster")
    assert get(client, game + "/trades")["results"][0]["status"] == "expired"
    assert refused(client, f"{game}/trades/offers/{offer['id']}/respond", {"accept": True}) == (
        422, "That offer is already expired.")


def test_responses_before_a_week_show_none_of_its_real_points(story):
    snapshot = story["snapshot"]

    rows = [row for name, screen in snapshot.items() if name.startswith("team") for row in screen["results"]]
    rows += snapshot["players"]["results"] + snapshot["matchup"]["home"]["results"]
    assert len(rows) > 500 and all(row["points"] is None for row in rows)
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
    for step in ("load_game_results", "load_adp", "load_projections"):
        monkeypatch.setattr(replay_data, step, lambda *args, **kwargs: {})
    monkeypatch.setattr(replay_data, "ensure_defenses", lambda session: 32)
    monkeypatch.setattr(replay_pool, "clear_cache", lambda: cleared.append(True))

    summary = replay_data.prepare_season(None, SEASON)

    assert cleared == [True] and summary["errors"] == {}
