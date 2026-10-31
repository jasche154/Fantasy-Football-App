"""Injury evidence from Sleeper news: season-ending headlines, timelines, and the refresh."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from database.models import InjuryNews, Player, PlayerStatus
from integrations import sleeper
from integrations.sleeper import SleeperError
from services import injury_news as news
from services import injury_recovery as ir

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def item(title, summary=None, days_ago=1):
    return {"title": title, "summary": summary, "published": (NOW - timedelta(days=days_ago)).isoformat()}


@pytest.mark.parametrize("title", [
    "Terry McLaurin Out for Season With Torn Patellar Tendon",
    "Jaylin Lane (ankle) likely done for 2026 season",
    "Josh Oliver to Miss the Rest of the Season With Torn Biceps",
    "Jaxson Dart (knee) underwent season-ending knee surgery",
    "Jalen Hurts Out for Remainder of 2026 Season",
])
def test_season_ending_headlines(title):
    assert news.signal(item(title))["season_ending"]


def test_negations_and_teammates_are_not_season_ending():
    assert news.signal(item("Smith avoids a season-ending injury")) is None
    assert news.signal(item("Scan shows the injury is not season-ending")) is None
    assert news.signal(item("Rams", "They will be without him for a significant stretch of the remainder of the 2026 season.")) is None
    assert news.signal(item("Receiver could miss the rest of the season")) is None                 # speculation
    teammate = item("49ers Put Demarcus Robinson on IR",
                    "The 49ers placed Demarcus Robinson (ankle) on IR. Robinson joins Ricky Pearsall (knee), who is out for the season.")
    assert news.signal(teammate, "robinson") is None
    assert news.signal(item("Hurts", "Jalen Hurts (blood clots), who is out for the year."), "hurts")["season_ending"]


def test_timelines_ranges_months_and_words():
    assert news.signal(item("Davante Adams Suffers Ruptured Hamstring, Out 6-10 Weeks"))["weeks"] == [6.0, 10.0]
    assert news.signal(item("Tyquan Thornton - Facing 3-to-4-month absence"))["weeks"] == [12.9, 17.2]
    assert news.signal(item("Etienne", "Travis Etienne Jr. (hamstring) will be out for at least four weeks."),
                       "etienne")["weeks"] == [4.0, 4.0]
    assert news.signal(item("Ruled out", "He will miss his fourth game in the past five weeks.")) is None
    assert news.signal(item("Ruled out for Week 7 against the Giants")) is None
    assert news.signal(item("Will Require Blood Thinners for 3-6 Months")) is None        # not about missing time
    assert news.last_name("Deebo Samuel Sr.") == "samuel" and news.last_name("Travis Etienne Jr.") == "etienne"


def test_evidence_ignores_old_news_and_uses_the_median_return():
    stored = news.signals([item("Out 12 weeks after back surgery", days_ago=47),
                           item("Out for 12 weeks after surgery", days_ago=47),
                           item("Has a chance of returning", "He was expected to be out 12-14 weeks.", days_ago=1)])
    found = news.evidence(stored, since=NOW - timedelta(days=60), now=NOW)
    assert not found.season_ending and found.weeks_left == pytest.approx(12 - 47 / 7, abs=0.2)   # not restarted
    ended = news.signals([item("Out for the season"), item("Out 6 weeks", days_ago=3)])
    assert news.evidence(ended, None, NOW).season_ending
    assert news.evidence(ended, since=NOW, now=NOW) is None                                    # before his last game


def test_the_news_moves_the_value():
    timeline = ir.Recovery("IR", "hamstring", None, 1, None, news_weeks=7.0)
    plain = ir.Recovery("IR", "hamstring", None, 1, None)
    assert timeline.expected_missed(10) > plain.expected_missed(10)
    assert "(reported timeline)" in timeline.note(10)
    surgery = ir.Recovery("IR", "hamstring", None, 0, None, surgery=True)
    assert surgery.expected_missed(10) >= ir.SURGERY_GAMES and "(surgery)" in surgery.note(10)
    ended = ir.Recovery("IR", "knee", None, 0, None, season_ending=True, from_news=True)
    assert ended.share(10) == 0.0 and ended.note(10) == "Out for the season (per news)"


def test_status_rows_keep_sleeper_notes_and_news_timestamp():
    rows = sleeper.status_rows({"9": {"injury_status": "IR", "injury_body_part": "Knee", "injury_notes": "Surgery",
                                      "news_updated": 1761900000000}}, {"9": 5})
    assert rows[0]["injury_notes"] == "Surgery" and rows[0]["news_updated"] == 1761900000000


def test_refresh_reads_news_only_for_injured_players_whose_news_changed(session, seeded, monkeypatch):
    hurt, healthy = session.query(Player).filter(Player.position == "WR").limit(2).all()
    for player, sid in ((hurt, "101"), (healthy, "102")):
        player.sleeper_player_id = sid
    session.add_all([PlayerStatus(player_id=hurt.id, injury_status="IR", news_updated=5),
                     PlayerStatus(player_id=healthy.id, injury_status="Questionable", news_updated=5)])
    session.commit()
    calls = []

    def fake_fetch(sleeper_id, client, fresh=False):
        assert fresh                                                                    # never a stale cached copy
        calls.append(sleeper_id)
        return [item(f"{hurt.full_name} out for the season")]

    monkeypatch.setattr(news, "fetch_news", fake_fetch)
    assert news.refresh(session, None) == {"due": 1, "fetched": 1, "errors": 0}
    assert json.loads(session.get(InjuryNews, hurt.id).signals)[0]["season_ending"]
    assert news.refresh(session, None)["due"] == 0 and calls == ["101"]                 # unchanged timestamp
    session.get(PlayerStatus, hurt.id).news_updated = 6
    session.commit()
    monkeypatch.setattr(news, "fetch_news", lambda sid, client, fresh: (_ for _ in ()).throw(SleeperError("down")))
    assert news.refresh(session, None) == {"due": 1, "fetched": 0, "errors": 1}


@pytest.mark.parametrize("title,summary", [
    ("Returns to practice", "Smith (ankle) returned to practice Wednesday after a three-week absence."),
    ("Expected back", "After missing three weeks with a hamstring injury, Smith is expected to return Sunday."),
    ("Out Sunday", "Smith (ankle), who was injured two weeks ago, is out Sunday."),
    ("Out Sunday", "Smith (ankle) is out Sunday, his fourth missed game in five weeks."),
    ("Out for the 2026 week 1 opener", None),
    ("Out again", "He will be reevaluated often weeks later."),
])
def test_past_absences_and_numbers_inside_words_are_not_timelines(title, summary):
    assert news.signal(item(title, summary), "smith") is None


def test_season_ending_edge_cases():
    assert news.signal(item("Smith (ankle) is out for the season opener."), "smith") is None
    backup = item("Update", "Smith (ankle) is out, and backup Jones, who tore his ACL, is done for the season.")
    assert news.signal(backup, "smith") is None
    for text in ("Smith (knee) will miss the rest of the regular season.",
                 "Smith (knee) has not practiced since and will miss the rest of the season.",
                 "Smith (knee) was placed on IR, ending his season."):
        assert news.signal(item(text), "smith")["season_ending"], text
    assert news.signal(item("Smith (hip) out fourteen weeks after surgery"), "smith")["weeks"] == [14.0, 14.0]


def test_evidence_median_of_two_and_merging_keeps_the_first_diagnosis():
    early, late = item("Out 4 weeks", days_ago=14), item("Out 8 weeks", days_ago=7)
    found = news.evidence(news.signals([early, late]), None, NOW)
    assert found.weeks_left == pytest.approx(4 - 2, abs=0.1)                  # the earlier return on a tie
    first = news.signals([item("Out 12-to-16 weeks", days_ago=30)])
    merged = news.merge(first, news.signals([item("Out 12-to-16 weeks", days_ago=30), item("Out 6 weeks", days_ago=1)]), NOW)
    assert len(merged) == 2 and merged[-1]["weeks"] == [12.0, 16.0]
    assert news.merge(news.signals([item("Out 6 weeks", days_ago=400)]), [], NOW) == []          # pruned


def test_a_news_refresh_changes_the_valuation_data_version(session, seeded):
    from services import ros_consensus

    before = ros_consensus.data_version(session, 2026)
    session.add(InjuryNews(player_id=session.query(Player.id).first()[0], signals="[]"))
    session.commit()
    assert ros_consensus.data_version(session, 2026) != before


def test_a_news_failure_never_fails_the_status_refresh(session, seeded, monkeypatch):
    from services import weekly_refresh

    monkeypatch.setattr(weekly_refresh.sleeper, "refresh_status", lambda session, http: 12)
    monkeypatch.setattr(news, "refresh", lambda session, http: (_ for _ in ()).throw(RuntimeError("boom")))
    result = weekly_refresh._refresh_status(session, None, force=True)
    assert result["state"] == "updated" and result["rows"] == 12
