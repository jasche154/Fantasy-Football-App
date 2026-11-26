"""Replay League: CPU feelings and shops (services/replay_mood.py), and the shop discount in trades."""
import json
import re
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_mood as rm, replay_personas, replay_state as rs, replay_trades as rt, replay_values as rv
from services.replay_pool import SKILL
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings(waiver_mode="none")
WEEK = 6
PLAYED = WEEK - 1


def knowledge(week: int = WEEK, data=None) -> rv.Knowledge:
    return fakes.fake_knowledge(week, SETTINGS, data)


def season_now(week: int = WEEK, data=None):
    """The season as known at the start of `week` (what refresh_shops reads last week's snap counts from)."""
    return (data or fakes.fake_season()).as_of(week)


def league(personas: dict[int, str] | None = None, week: int = WEEK) -> rs.State:
    """The drafted league in `week`, every CPU balanced unless `personas` says otherwise."""
    state = fakes.drafted_state(SETTINGS)
    chosen = personas or {}
    teams = tuple(replace(t, persona=chosen.get(t.id, "balanced"),
                          hype=replay_personas.hype_for(chosen.get(t.id, "balanced"))) for t in state.teams)
    return replace(state, teams=teams, week=week)


def best_skill(know: rv.Knowledge, team: rs.Team) -> rv.ReplayValue:
    return max((know.by_id[pid] for pid in team.roster if know.by_id[pid].position in SKILL),
               key=lambda v: (v.chart, -v.player_id))


def with_values(know: rv.Knowledge, *values: rv.ReplayValue) -> rv.Knowledge:
    by_id = {**know.by_id, **{v.player_id: v for v in values}}
    return replace(know, by_id=MappingProxyType(by_id),
                   values=tuple(by_id[v.player_id] for v in know.values))


def slumping(value: rv.ReplayValue, share: float = 0.3) -> rv.ReplayValue:
    """The same player after bad games: form well below his chart and a last game under his rate."""
    return replace(value, form_chart=-share * value.chart, shock_chart=0.0, recent_points=(1.0, 3.0),
                   ros_ppg=max(value.ros_ppg, 10.0), out=False, injury=None)


def test_a_slump_worries_a_panicky_manager_most_and_a_steady_one_least():
    value = slumping(best_skill(knowledge(), league().teams[1]), 0.08)
    felt = {persona: rm.strength(value, persona) for persona in replay_personas.PERSONAS}

    assert felt["panicky"] < felt["balanced"] == felt["shark"] < 0
    assert felt["steady"] > felt["balanced"]                # shrugs it off (and still believes in a star)
    assert rm.feeling(value, "panicky").kind == "worried"


def test_a_hot_streak_lifts_a_gambler_most_and_a_hurt_player_has_no_feeling():
    value = replace(best_skill(knowledge(), league().teams[1]), form_chart=40.0, shock_chart=0.0)
    hurt = replace(value, out=True)

    assert rm.strength(value, "gambler") > rm.strength(value, "balanced") > 0
    assert rm.feeling(hurt, "panicky") is None and rm.feeling(replace(value, injury=fakes.Injury("Q")), "gambler") is None


def test_mood_line_names_his_strongest_worry_and_high():
    state, know = league(), knowledge()
    cpu = state.teams[1]
    ids = [pid for pid in cpu.roster if know.by_id[pid].position in SKILL]
    worried = slumping(know.by_id[ids[0]])
    hot = replace(know.by_id[ids[1]], form_chart=0.4 * know.by_id[ids[1]].chart, shock_chart=0.0, out=False,
                  injury=None, healthy_chart=max(know.by_id[ids[1]].healthy_chart, rm.FEEL_MIN_CHART))
    calm = [replace(know.by_id[pid], form_chart=0.0, shock_chart=0.0) for pid in ids[2:]]
    mood = rm.mood_line(with_values(know, worried, hot, *calm), cpu)

    assert mood == f"Worried about {worried.name}. High on {hot.name}."
    assert rm.mood_line(with_values(know, *[replace(know.by_id[pid], form_chart=0.0, shock_chart=0.0)
                                           for pid in cpu.roster]), cpu) is None


def shop_league(persona: str = "panicky") -> tuple[rs.State, rv.Knowledge, rs.Team, rv.ReplayValue]:
    """Team 2 (a `persona`) whose most valuable skill player is in a deep slump; every other CPU is steady
    (never shops)."""
    state = league({team_id: persona if team_id == 2 else "steady" for team_id in range(2, SETTINGS.teams + 1)})
    know = knowledge()
    cpu = rs.team(state, 2)
    star = slumping(best_skill(know, cpu), 0.1)            # worry 0.18 for a panicky manager: shops, not capped
    return state, with_values(know, star), cpu, star


def open_shops(state: rs.State, know: rv.Knowledge, seeds=range(40), data=None):
    return [(seed, *rm.refresh_shops(state, SETTINGS, know, seed, season_now(state.week, data)))
            for seed in seeds]


def test_a_panicky_manager_shops_his_slumping_star_with_a_bounded_discount():
    state, know, cpu, star = shop_league()
    runs = open_shops(state, know)
    opened = [shops[0] for _, _, shops in runs if shops]

    assert len(opened) > len(runs) / 2                      # SHOP_RATE 0.75
    assert {(s.team_id, s.player_id, s.week) for s in opened} == {(cpu.id, star.player_id, WEEK)}
    assert all(rm.DISCOUNT_RANGE[0] <= s.discount <= rm.DISCOUNT_RANGE[1] for s in opened)
    assert len({s.discount for s in opened}) > 1            # seeded jitter
    assert open_shops(state, know)[:5] == runs[:5]          # a reload never re-rolls


@pytest.mark.parametrize("persona", ["steady", "shark"])
def test_steady_and_shark_managers_never_shop(persona):
    state, know, _, _ = shop_league(persona)
    assert not any(shops for _, _, shops in open_shops(state, know))


def test_no_shops_before_mood_weeks_of_results_or_after_the_deadline():
    state, know, _, _ = shop_league()

    early = replace(state, week=rm.MOOD_WEEKS)                           # only MOOD_WEEKS - 1 weeks are known
    assert not any(shops for _, _, shops in open_shops(early, know))
    assert any(shops for _, _, shops in open_shops(replace(state, week=rm.MOOD_WEEKS + 1), know))
    late = replace(state, week=SETTINGS.trade_deadline_week + 1)
    assert not any(after.shops for _, after, _ in open_shops(late, know))


def test_at_most_max_shops_in_the_league_and_one_per_manager():
    state = league(dict.fromkeys(range(2, 13), "panicky"))
    know = knowledge()
    stars = [slumping(best_skill(know, t)) for t in state.teams if not t.is_user]
    know = with_values(know, *stars)
    for _, after, _ in open_shops(state, know, range(10)):
        assert len(after.shops) <= rm.MAX_SHOPS
        assert len({s.team_id for s in after.shops}) == len(after.shops)


def test_a_shop_closes_after_a_good_game_or_when_he_leaves_and_renews_otherwise():
    state, know, cpu, star = shop_league()
    seed, after, opened = next(run for run in open_shops(state, know) if run[2])
    nxt = replace(after, week=WEEK + 1)

    renewed, _ = rm.refresh_shops(nxt, SETTINGS, know, seed, season_now(WEEK + 1))
    good = with_values(know, replace(star, recent_points=(star.ros_ppg + 10.0, 1.0)))
    closed, _ = rm.refresh_shops(nxt, SETTINGS, good, seed, season_now(WEEK + 1))
    gone = rs.with_team(nxt, replace(cpu, roster=tuple(p for p in cpu.roster if p != star.player_id)))

    assert [(s.player_id, s.week, s.discount, s.opened) for s in renewed.shops] == [
        (star.player_id, WEEK + 1, opened[0].discount, WEEK)]                 # renewed: still the shop opened in WEEK
    assert opened[0].opened == WEEK
    assert all(s.player_id != star.player_id for s in closed.shops)
    assert rs.markdowns(gone, cpu.id) == {}


# ---------------- the discount in trades ----------------

def _deal(state: rs.State, know: rv.Knowledge, star: rv.ReplayValue) -> tuple[int, ...]:
    """The user's skill players closest in total chart to the star (what the user offers for him)."""
    mine = sorted((know.by_id[pid] for pid in rs.user_team(state).roster if know.by_id[pid].position in SKILL),
                  key=lambda v: abs(v.chart - star.chart))
    return (mine[0].player_id,)


def test_the_discount_moves_his_price_for_the_user_by_exactly_the_discount():
    state, know, cpu, star = shop_league()
    give = _deal(state, know, star)
    shop = rs.Shop(cpu.id, star.player_id, WEEK, 0.10)
    user = rs.user_team(state).id

    plain = rt.evaluate(state, SETTINGS, know, user, cpu.id, give, (star.player_id,))
    shopped = rt.evaluate(replace(state, shops=(shop,)), SETTINGS, know, user, cpu.id, give, (star.player_id,))
    price = replay_personas.private_value(star, cpu.persona, cpu.hype)
    bar = rs.USER_TRADE_MULTIPLE[SETTINGS.difficulty] * rt.tv.OUT_MARGIN

    assert shopped.score - plain.score == pytest.approx(0.10 * price * bar, abs=0.05)
    assert shopped.value_give == plain.value_give and shopped.value_get == plain.value_get   # the chart never moves


def test_the_discount_is_for_the_user_only_and_only_while_he_holds_the_player():
    state, know, cpu, star = shop_league()
    shop = rs.Shop(cpu.id, star.player_id, WEEK, 0.15)
    other = next(t for t in state.teams if not t.is_user and t.id != cpu.id)
    pid = best_skill(know, other).player_id
    plain = rt.evaluate(state, SETTINGS, know, other.id, cpu.id, (pid,), (star.player_id,))
    shopped = rt.evaluate(replace(state, shops=(shop,)), SETTINGS, know, other.id, cpu.id, (pid,), (star.player_id,))
    stale = replace(state, shops=(replace(shop, week=WEEK - 1),))

    assert shopped.score == plain.score
    assert rs.markdowns(stale, cpu.id) == {}


def test_buying_the_shopped_player_closes_the_shop_and_locks_him_from_a_flip():
    state, know, cpu, star = shop_league()
    state = replace(state, shops=(rs.Shop(cpu.id, star.player_id, WEEK, 0.15),))
    user = rs.user_team(state)
    mine = sorted((know.by_id[pid] for pid in user.roster if know.by_id[pid].position in SKILL),
                  key=lambda v: -v.chart)
    give = next(((v.player_id,) for v in reversed(mine)
                 if rt.evaluate(state, SETTINGS, know, user.id, cpu.id, (v.player_id,), (star.player_id,)).accepted),
                (mine[0].player_id,))
    after, verdict = rt.propose(state, SETTINGS, know, cpu.id, give, (star.player_id,))
    assert verdict.accepted

    other = next(t for t in after.teams if not t.is_user and t.id != cpu.id)
    flip = rt.evaluate(after, SETTINGS, know, user.id, other.id, (star.player_id,), (best_skill(know, other).player_id,))
    assert rs.markdowns(after, cpu.id) == {}
    assert not flip.accepted and any("Traded in the last" in reason for reason in flip.reasons)


# ---------------- no hindsight ----------------

def test_feelings_and_shops_never_see_the_future():
    state = league({2: "panicky", 3: "gambler", 4: "balanced", 5: "steady"})
    changed = fakes.future_changed(fakes.fake_season(), after_week=WEEK)
    now, later = knowledge(), knowledge(data=changed)

    for team in state.teams[1:]:
        assert rm.feelings(now, team) == rm.feelings(later, team)
        assert rm.mood_line(now, team) == rm.mood_line(later, team)
    for seed in range(5):
        assert rs.to_json(rm.refresh_shops(state, SETTINGS, now, seed, season_now())[0]) == rs.to_json(
            rm.refresh_shops(state, SETTINGS, later, seed, season_now(data=changed))[0])


# ---------------- what he says agrees with what he pays ----------------

def test_a_feeling_never_contradicts_his_price_anywhere_in_the_season():
    """High on a player: he values him at or above the chart; worried: at or below. Every week, persona, player."""
    wrong = []
    for week in range(1, SETTINGS.end_week + 1):
        know = knowledge(week)
        for value in know.values:
            for persona in replay_personas.PERSONAS:
                hype = replay_personas.hype_for(persona)
                felt = rm.feeling(value, persona, hype)
                lean = replay_personas.private_value(value, persona, hype) - value.chart
                if felt is not None and (lean < -1e-9 if felt.kind == "high" else lean > 1e-9):
                    wrong.append((week, persona, value.player_id, felt.kind, round(lean, 1)))
    assert wrong == []


def test_a_steady_manager_is_never_high_on_a_player_he_marks_below_the_chart():
    base = best_skill(knowledge(), league().teams[1])
    value = replace(base, form_chart=0.2 * base.chart, shock_chart=0.0, star=1.0,
                    out=False, injury=None)
    risen = replace(value, pre_chart=value.chart - 200.0)        # draft day thought less of him than today
    felt = rm.feeling(risen, "steady")

    assert replay_personas.private_value(risen, "steady") < risen.chart
    assert felt is None or felt.kind == "worried"


def test_no_shop_for_a_trade_locked_player_or_one_on_ir():
    state, know, cpu, star = shop_league()
    locked = replace(state, traded={star.player_id: WEEK - 1})
    on_ir = rs.with_team(state, replace(cpu, roster=tuple(p for p in cpu.roster if p != star.player_id),
                                        ir=(*cpu.ir, star.player_id)))

    assert any(shops for _, _, shops in open_shops(state, know))
    assert not any(shops for _, _, shops in open_shops(locked, know))
    assert not any(shops for _, _, shops in open_shops(on_ir, know))


def test_a_shop_leaves_the_state_when_his_player_leaves_the_active_roster():
    state, know, cpu, star = shop_league()
    state = replace(state, shops=(rs.Shop(cpu.id, star.player_id, WEEK, 0.1),))
    gone = rs.with_team(state, replace(cpu, roster=tuple(p for p in cpu.roster if p != star.player_id),
                                       ir=(*cpu.ir, star.player_id)))

    assert gone.shops == () and rs.markdowns(gone, cpu.id) == {}
    assert rs.with_team(state, cpu).shops == state.shops


def test_the_trade_eval_tally_counts_a_shop_discount_like_replay_trades():
    from services import replay_trade_eval as te
    state, know, cpu, star = shop_league()
    give, get = _deal(state, know, star), (star.player_id,)
    user = rs.user_team(state).id
    shopped = replace(state, shops=(rs.Shop(cpu.id, star.player_id, WEEK, 0.10),))

    def tally(on: rs.State) -> float:
        verdict = rt.evaluate(on, SETTINGS, know, user, cpu.id, give, get)
        return te._tally(on, SETTINGS, know, cpu, get, give, verdict).score

    price = replay_personas.private_value(star, cpu.persona, cpu.hype)
    bar = rs.USER_TRADE_MULTIPLE[SETTINGS.difficulty] * rt.tv.OUT_MARGIN
    assert tally(shopped) - tally(state) == pytest.approx(0.10 * price * bar, abs=0.05)


# ---------------- never about a player who may be hurt ----------------

def left_early(pid: int, week: int = PLAYED, data=None):
    """The season with his `week` game cut short: a snap share well under LEFT_EARLY x his median, no tag."""
    data = data or fakes.fake_season()
    earlier = sorted(u.snap_pct for w in range(1, week) if (u := data.usage.get((pid, w))) and u.snap_pct)
    short = replace(data.usage[(pid, week)], snap_pct=round(0.3 * earlier[len(earlier) // 2], 1))
    return replace(data, usage=MappingProxyType({**data.usage, (pid, week): short}))


def test_a_slumping_player_who_left_his_game_early_is_never_shopped_and_his_open_shop_closes():
    state, know, cpu, star = shop_league()
    hurt = left_early(star.player_id)
    assert star.injury is None and not star.out                          # untagged: only the snap count tells
    assert rm.might_be_hurt(know, hurt.as_of(WEEK), star.player_id, PLAYED)
    open_now = replace(state, shops=(rs.Shop(cpu.id, star.player_id, WEEK - 1, 0.1),))

    assert any(shops for _, _, shops in open_shops(state, know))         # healthy: he is shopped
    assert not any(s.player_id == star.player_id for _, after, _ in open_shops(state, know, data=hurt)
                   for s in after.shops)
    assert [s.player_id for s in rm.refresh_shops(open_now, SETTINGS, know, 0, season_now())[0].shops] == [
        star.player_id]
    assert all(s.player_id != star.player_id
               for s in rm.refresh_shops(open_now, SETTINGS, know, 0, hurt.as_of(WEEK))[0].shops)


def test_an_offer_never_says_he_is_worried_about_a_player_who_may_be_hurt():
    from services import replay_chat
    state, know, cpu, star = shop_league()
    user = rs.user_team(state)
    offer = rs.Offer(70, WEEK, cpu.id, user.id, (star.player_id,), (user.roster[1],), "pending")
    state = replace(state, offers=(offer,))

    def kind(data) -> str:
        return replay_chat.label_offers(state, know, 4, data.as_of(WEEK), SETTINGS).chat[-1].kind

    assert kind(fakes.fake_season()) == "offer_worried"
    assert kind(left_early(star.player_id)) != "offer_worried"


def test_a_left_early_shop_check_never_sees_the_future():
    state, know, cpu, star = shop_league()
    hurt = left_early(star.player_id)
    changed = fakes.future_changed(hurt, after_week=WEEK)
    for seed in range(5):
        assert rs.to_json(rm.refresh_shops(state, SETTINGS, know, seed, hurt.as_of(WEEK))[0]) == rs.to_json(
            rm.refresh_shops(state, SETTINGS, know, seed, changed.as_of(WEEK))[0])


# ---------------- an open shop closes for each reason ----------------

def _reopened(state: rs.State, know: rv.Knowledge, data=None) -> list[int]:
    """The players shopped once an already-open shop is refreshed (any seed: a kept shop never rolls)."""
    now = season_now(state.week, data)
    return [s.player_id for s in rm.refresh_shops(state, SETTINGS, know, 0, now)[0].shops]


@pytest.mark.parametrize("reason", ["tag", "worry_faded", "retrade_lock", "deadline", "mood_weeks"])
def test_an_open_shop_closes_for_each_reason(reason):
    state, know, cpu, star = shop_league()
    state = replace(state, shops=(rs.Shop(cpu.id, star.player_id, WEEK - 1, 0.1),))
    assert _reopened(state, know) == [star.player_id]                    # nothing changed: it renews

    data = None
    if reason == "tag":             # tagged in last week's game: only might_be_hurt sees it (star.injury stays None)
        data = fakes.tagged(fakes.fake_season(), {(star.player_id, PLAYED): fakes.Injury("Q")})
    elif reason == "worry_faded":
        know = with_values(know, replace(star, form_chart=-0.01 * star.chart))      # still slumping, barely worried
    elif reason == "retrade_lock":
        state = replace(state, traded={star.player_id: WEEK - 1})
    elif reason == "deadline":
        state = replace(state, week=SETTINGS.trade_deadline_week + 1)
    elif reason == "mood_weeks":
        state = replace(state, week=rm.MOOD_WEEKS)

    assert star.player_id not in _reopened(state, know, data)


def _off_a_bye() -> tuple[rs.State, rv.Knowledge, rs.Team, rv.ReplayValue, int]:
    """Team 2 (panicky) the week after its best slumping skill player's team had a bye: (state, know, cpu,
    star, bye week)."""
    data = fakes.fake_season()
    state = league({team_id: "panicky" if team_id == 2 else "steady" for team_id in range(2, SETTINGS.teams + 1)})
    cpu = rs.team(state, 2)
    for bye in range(rm.MOOD_WEEKS + 1, SETTINGS.trade_deadline_week):
        know = knowledge(bye + 1)
        star = slumping(best_skill(know, cpu), 0.1)
        if data.on_bye(star.player_id, bye):
            calm = [replace(know.by_id[pid], form_chart=0.0, shock_chart=0.0) for pid in cpu.roster
                    if pid != star.player_id]                                 # he is the one worry
            return replace(state, week=bye + 1), with_values(know, star, *calm), cpu, star, bye
    raise AssertionError("no star with a bye before the deadline in the fake season")


def test_a_bye_last_week_is_not_a_maybe_injury():
    state, know, cpu, star, bye = _off_a_bye()
    played = fakes.fake_season().as_of(bye + 1)

    assert (star.player_id, bye) not in played.usage                     # no snap count: he had no game
    assert not rm.might_be_hurt(know, played, star.player_id, bye)
    assert rm.might_be_hurt(know, fakes.tagged(fakes.fake_season(), {(star.player_id, bye): fakes.Injury("Q")})
                            .as_of(bye + 1), star.player_id, bye)                # a tag still counts, bye or not


def test_a_bye_does_not_hide_that_his_last_game_was_cut_short():
    state, know, cpu, star, bye = _off_a_bye()
    healthy = fakes.fake_season()
    assert not healthy.on_bye(star.player_id, bye - 1)                   # he played the week before the bye
    hurt = left_early(star.player_id, week=bye - 1, data=healthy)         # untagged: only the snap count tells

    assert rm.might_be_hurt(know, hurt.as_of(bye), star.player_id, bye - 1)          # judged right after it
    assert rm.might_be_hurt(know, hurt.as_of(bye + 1), star.player_id, bye)          # and still after the bye
    assert not rm.might_be_hurt(know, healthy.as_of(bye + 1), star.player_id, bye)   # a full game before it: fine
    assert not any(s.player_id == star.player_id for _, after, _ in open_shops(state, know, data=hurt)
                   for s in after.shops)


def test_a_slumping_player_coming_off_a_bye_can_be_shopped_and_an_open_shop_on_him_stays():
    state, know, cpu, star, bye = _off_a_bye()

    opened = [shops for _, _, shops in open_shops(state, know)]
    assert any(s.player_id == star.player_id for shops in opened for s in shops)
    kept = replace(state, shops=(rs.Shop(cpu.id, star.player_id, bye, 0.1),))
    assert _reopened(kept, know) == [star.player_id]


# ---------------- the discount's limits ----------------

def test_the_discount_always_stays_inside_its_range():
    for persona in replay_personas.PERSONAS:
        team = replace(league().teams[1], persona=persona)
        for seed in range(30):
            for worry in (rm.SHOP_WORRY, 0.3, 0.6, 1.0, 3.0):
                assert rm.DISCOUNT_RANGE[0] <= rm.discount(seed, WEEK, team, worry) <= rm.DISCOUNT_RANGE[1]


def test_the_discount_is_capped_at_its_top_for_a_deep_worry():
    for persona in ("balanced", "panicky", "gambler"):
        team = replace(league().teams[1], persona=persona)
        assert {rm.discount(seed, WEEK, team, 1.0) for seed in range(20)} == {rm.DISCOUNT_RANGE[1]}


def test_a_balanced_manager_barely_worried_is_floored_at_its_bottom():
    team = replace(league().teams[1], persona="balanced")
    found = [rm.discount(seed, WEEK, team, rm.SHOP_WORRY) for seed in range(60)]
    assert min(found) == rm.DISCOUNT_RANGE[0]                         # some seeds' jitter takes it below: floored
    assert max(found) > rm.DISCOUNT_RANGE[0]


def test_an_old_save_shop_without_an_opened_week_loads():
    state, _, cpu, star = shop_league()
    saved = json.loads(rs.to_json(replace(state, shops=(rs.Shop(cpu.id, star.player_id, WEEK, 0.1, opened=WEEK),))))
    saved["shops"] = [{key: value for key, value in shop.items() if key != "opened"} for shop in saved["shops"]]

    assert rs.state_from_json(json.dumps(saved)).shops == (rs.Shop(cpu.id, star.player_id, WEEK, 0.1),)


def test_a_fan_never_shops_or_sells_his_own_teams_player_with_a_worried_line():
    """A fan prices his team's players 10% higher: worried or not, he never sends a sell line about one."""
    from services import replay_chat
    state, know, cpu, star = shop_league()          # panicky feels the slump enough to shop; the rule keys on fan_of
    plain = rs.with_team(state, replace(cpu, fan_of=None))
    fan = rs.with_team(state, replace(cpu, fan_of=star.team))
    user = rs.user_team(state)

    assert any(shops for _, _, shops in open_shops(plain, know))                 # worried enough to shop him...
    assert not any(shop.player_id == star.player_id for _, after, _ in open_shops(fan, know) for shop in after.shops)
    offer = rs.Offer(71, WEEK, cpu.id, user.id, (star.player_id,), (user.roster[1],), "pending")
    kinds = [replay_chat.label_offers(replace(s, offers=(offer,)), know, 4, season_now(), SETTINGS).chat[-1].kind
             for s in (plain, fan)]
    assert kinds[0] == "offer_worried" and kinds[1] != "offer_worried"           # ...but not his own team's


def test_a_stashers_worried_line_is_about_a_slump_never_about_selling_before_he_fades():
    from services import replay_chat
    lines = replay_chat.bank()["offer_worried"]["stasher"]
    assert not [line for line in lines if re.search(r"still has value|before|fading|while he", line, re.IGNORECASE)]
