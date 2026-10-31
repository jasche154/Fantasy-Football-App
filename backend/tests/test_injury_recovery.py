"""Injury discount on trade value: severity at the injury, recovery with evidence and time, return dip."""
from types import SimpleNamespace

import pytest

from database.models import InjuryReport, Player, PlayerStatus
from services import injury_recovery as ir
from services import valuation as valuation_module
from services.league import LeagueSettings
from services.lineup_gain import _after_injury
from services.projections import GameLine, PlayerSeason
from services.schedule_strength import Game, Schedule
from services.season_sim import _start_out
from services.valuation import build_valuation

REMAINING = 10


def out(group="hamstring", status="OUT", practice=None, games_out=0, games_back=None, ending=False):
    return ir.Recovery(status=status, group=group, practice=practice, games_out=games_out, games_back=games_back,
                       season_ending=ending)


def test_body_parts_and_practice_reports_map_to_groups():
    assert ir.body_group("Knee - ACL") == "acl_achilles" and ir.body_group("Hamstring") == "hamstring"
    assert ir.body_group("Concussion") == "concussion" and ir.body_group("Groin") == "soft_tissue"
    assert ir.body_group(None) == ir.body_group("Undisclosed") == "unknown"
    assert ir.body_group("Spleen") == "other"
    assert ir.body_group("Achilles Tendinitis") == "soft_tissue"            # Sleeper body part + note: not a tear
    assert [ir.practice_key(p) for p in (None, "DNP", "LP", "FP")] == ["none", "dnp", "practicing", "practicing"]


def test_the_discount_starts_from_how_bad_the_injury_is():
    assert out("concussion").expected_missed(REMAINING) < out("knee").expected_missed(REMAINING)
    assert out(status="QUESTIONABLE").expected_missed(REMAINING) == 0.0            # plays this week (weekly model)
    assert out(status="IR").expected_missed(REMAINING) >= ir.IR_MIN_GAMES
    torn = out("acl_achilles", status="IR", ending=True)
    assert torn.expected_missed(REMAINING) == REMAINING and torn.share(REMAINING) == 0.0     # out for the season: 0


def test_practice_reports_move_the_estimate():
    none, dnp, practicing = (out(practice=p).expected_missed(REMAINING) for p in ("none", "dnp", "practicing"))
    assert practicing < dnp < none


def test_value_rises_a_little_each_week_he_is_out_even_without_news():
    shares = [out("knee", games_out=k).share(REMAINING - k) for k in range(5)]
    discounts = [1 - s for s in shares]
    assert all(later < earlier for earlier, later in zip(discounts, discounts[1:]))
    ir_weeks = [out("knee", status="IR", games_out=k).expected_missed(REMAINING) for k in range(8)]
    assert ir_weeks == sorted(ir_weeks, reverse=True) and ir_weeks[-1] < ir_weeks[0]


def test_after_he_returns_a_short_dip_then_back_to_normal():
    back = [out(status=None, games_back=k).share(REMAINING) for k in range(4)]
    assert back[0] < back[1] < back[2] < back[3] == 1.0
    assert out(status=None, games_back=None).share(REMAINING) == 1.0
    assert out(status=None, games_out=1).rust_games(REMAINING) > 0                 # missed last week, not tagged now
    assert out(status=None, games_back=1).note(REMAINING) == "1 game back from injury"
    assert out(practice="practicing").note(REMAINING) == "Hamstring injury: likely out 2 more games, practicing"
    assert out(status=None, games_out=1, group="knee").note(REMAINING).startswith("Returning from a knee injury")
    assert out("non_injury").note(REMAINING).startswith("Not injury-related: likely out")
    assert out(ending=True, status="IR").note(REMAINING) == "Out for the season"


def _line(week, points, passing_yards=0):
    return GameLine(week=week, season_type="REG", opponent="OPP", fantasy_points=points, expected_points=None,
                    expected_tds=None, passing_yards=passing_yards, passing_tds=0, interceptions=0, carries=10, rushing_yards=50,
                    rushing_tds=0, targets=3, receptions=2, receiving_yards=20, receiving_tds=0,
                    target_share=None, snap_pct=None)


def _schedule(weeks=range(1, 7)) -> Schedule:
    games = tuple(Game(week=w, home_team="AAA", away_team="OPP", home_score=20, away_score=17,
                       spread_line=None, total_line=None) for w in weeks)
    opponents = {key: value for w in weeks for key, value in ((("AAA", w), "OPP"), (("OPP", w), "AAA"))}
    return Schedule(games=games, opponents=opponents)


def _season(pid, name, weeks_points, position="RB"):
    return PlayerSeason(pid, name, position, "AAA", 2026, tuple(_line(w, p) for w, p in weeks_points))


def test_history_counts_games_out_and_games_back():
    schedule = _schedule()
    assert ir.history(_season(1, "a", [(1, 9), (2, 9), (3, 9)]), schedule, 7) == (3, None, (4, 5, 6))
    assert ir.history(_season(1, "a", [(1, 9), (2, 9), (5, 9), (6, 9)]), schedule, 7) == (0, 2, (3, 4))
    assert ir.history(_season(1, "a", [(w, 9) for w in range(1, 7)]), schedule, 7) == (0, None, ())
    assert ir.history(_season(1, "a", [(4, 9), (5, 9), (6, 9)]), schedule, 7) == (0, None, ())     # joined late


def test_a_returning_back_is_flagged_when_his_fill_in_played_well():
    star = _season(1, "Starter", [(1, 18), (2, 16), (3, 17)])
    strong = _season(2, "Fill-In", [(1, 3), (2, 2), (3, 4), (4, 12), (5, 14), (6, 13)])
    weak = _season(3, "Weak", [(4, 3), (5, 2), (6, 3)])
    states = {1: out(games_out=3)}
    cuts = ir.rb_role_cuts([star, strong, weak], states, {1: (4, 5, 6)})
    assert cuts == {1: ("Fill-In", ir.params()["rb_role_loss"])}
    assert ir.rb_role_cuts([star, weak], states, {1: (4, 5, 6)}) == {}
    assert ir.rb_role_cuts([star, strong], {1: out(status=None, games_back=5)}, {1: (4, 5)}) == {}   # long since back


def test_lineup_gain_and_the_season_sim_use_the_same_expected_absence():
    rust = ir.params()["rust"]
    assert _after_injury(0, 1.5) == 0.0 and _after_injury(1, 1.5) == pytest.approx(0.5)
    assert _after_injury(2, 1.5) == pytest.approx(rust[0]) and _after_injury(5, 1.5) == 1.0
    valuation = SimpleNamespace(values=[SimpleNamespace(player_id=1, expected_missed=3.4),
                                        SimpleNamespace(player_id=2, expected_missed=0.2)])
    assert _start_out(valuation) == {1: 3}


def _pid(session, name):
    return session.query(Player.id).filter(Player.full_name == name).scalar()


def test_valuation_discounts_by_injury_and_practice(session, seeded):
    resting, practicing = _pid(session, "RB Player 01"), _pid(session, "RB Player 02")
    for pid in (resting, practicing):
        session.add(PlayerStatus(player_id=pid, injury_status="Out", injury_body_part="Hamstring"))
    valuation_module._cache.clear()
    week = (build_valuation(session, LeagueSettings()).through_week or 0) + 1
    session.add(InjuryReport(season=2026, week=week, player_id=practicing, report_status="Out", practice_status="LP",
                             injury="Hamstring"))
    session.commit()
    valuation_module._cache.clear()
    by_id = build_valuation(session, LeagueSettings()).by_id
    assert by_id[practicing].expected_missed < by_id[resting].expected_missed
    assert by_id[practicing].availability > by_id[resting].availability
    assert by_id[practicing].injury_note.startswith("Hamstring injury: likely out") and "practicing" in by_id[practicing].injury_note
    assert _pid(session, "RB Player 03") not in {pid for pid, v in by_id.items() if v.injury_note}



def test_an_injured_starting_qb_listed_behind_his_fill_in_stays_the_starter(monkeypatch):
    from services import injuries, ros_consensus

    starter = PlayerSeason(1, "Starter", "QB", "AAA", 2026, tuple(_line(w, 20, passing_yards=250) for w in (1, 2, 3)))
    fill_in = PlayerSeason(2, "Fill-In", "QB", "AAA", 2026, (_line(4, 12, passing_yards=180),))
    assert ir.season_starters([starter, fill_in]) == {"AAA": 1}
    monkeypatch.setattr(injuries, "qb_depth", lambda session: {"AAA": [2, 1]})     # depth chart after the injury
    shares = {1: 0.7}
    assert ros_consensus.backup_qb_shares(None, shares) == {1: 0.03}                # old reading: worth nothing
    assert ros_consensus.backup_qb_shares(None, shares, {"AAA": 1}) == {2: 0.3}     # fill-in starts what he misses


def test_a_benched_qb_with_a_questionable_tag_stays_the_backup():
    starter = PlayerSeason(1, "Veteran", "QB", "AAA", 2026, tuple(_line(w, 20, passing_yards=250) for w in (1, 2, 3)))
    assert ir.season_starters([starter], {1: out(status="QUESTIONABLE", games_out=2)}) == {}
    assert ir.season_starters([starter], {1: out(status="IR", games_out=2)}) == {"AAA": 1}


def test_a_bye_inside_a_reported_timeline_is_not_a_missed_game():
    schedule = _schedule(weeks=(7, 9, 10))                                  # bye in week 8
    assert ir.news_games(3.0, "AAA", schedule, 7) == 2.0
    assert ir.news_games(3.0, None, schedule, 7) == 3.0
