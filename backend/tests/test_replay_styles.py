"""Replay League: the newer CPU trade styles (fan, stasher, haggler, contender, dealer, tilted), the "high on"
mark-up, the need bonus and why a CPU says no (services/replay_personas.py, replay_styles.py, replay_fans.py,
replay_trade_value.py, replay_trades.py).

Leagues come from test_replay_trades (fakes.drafted_state() on its plain chart; the user is team 1).
"""
from collections import Counter
from dataclasses import replace

import pytest

from services import (
    replay_counter, replay_fans as fans, replay_mood, replay_personas as rp, replay_state as rs,
    replay_styles as st, replay_trade_ai as ai, replay_trade_value as tv, replay_trades as rt,
)
from services.replay_news import Report
from services.replay_pool import SKILL
from tests.test_replay_trades import (
    FAIR, NEEDS_RB, SETTINGS, USER, league, lock_free_agents, star, values,
)

CPU = NEEDS_RB                  # CPU 9: short at RB (replay_styles.position_needs agrees)


def with_team(state: rs.State, team_id: int, **fields) -> rs.State:
    return rs.with_team(state, replace(rs.team(state, team_id), **fields))


def results(state: rs.State, team_id: int, won: list[bool]) -> rs.State:
    """`state` with one fake regular-season result per entry: team_id wins or loses; everyone else splits."""
    ids = [t.id for t in state.teams]
    others = [t for t in ids if t != team_id]
    played = []
    for week, win in enumerate(won, start=1):
        pairs = ((team_id, others[0]), *zip(others[1::2], others[2::2]))
        scores = {t: 100.0 for t in ids} | {team_id: 120.0 if win else 80.0}
        played.append(rs.WeekResult(week, pairs, scores, {}, {}))
    return replace(state, results=tuple(played))


def chart(know, pid: int) -> float:
    return know.by_id[pid].chart


# ---------------- assignment ----------------

@pytest.mark.parametrize("cpus", [1, 2, 3, 7, 11, 15])
def test_assignment_keeps_a_shark_and_a_panicky_and_no_style_on_more_than_two_cpus(cpus):
    ids = list(range(2, 2 + cpus))
    seen = Counter()
    for seed in range(40):
        picked = rp.assign(seed, ids)
        counts = Counter(picked.values())
        assert set(picked) == set(ids) and max(counts.values()) <= rp.MAX_PER_STYLE
        if cpus >= 2:
            assert counts["shark"] >= 1 and counts["panicky"] >= 1
        seen.update(counts)
    assert rp.assign(3, ids) == rp.assign(3, ids)
    if cpus >= 7:
        assert set(seen) == set(rp.PERSONAS)          # every style turns up


def test_old_saves_keep_their_styles_and_load_without_the_new_fields():
    state = league()[0]
    text = rs.to_json(state).replace(',"fan_of":null', "")
    offer = rs.Offer(1, 1, USER, CPU, (1,), (2,), "declined")
    old = rs.state_from_json(rs.to_json(replace(state, offers=(offer,))).replace(',"reply":null', ""))

    assert '"fan_of"' not in text and rs.state_from_json(text).teams == state.teams
    assert old.offers[0].reply is None and [t.persona for t in old.teams] == [t.persona for t in state.teams]


# ---------------- fan ----------------

def test_the_rival_table_covers_every_team_and_oak_is_lv():
    assert len(fans.RIVALS) == 32 and set(fans.RIVALS.values()) <= set(fans.RIVALS)
    assert all(fans.nickname(code) != code for code in fans.RIVALS)
    kc = rs.Team(2, "x", False, (), 0.0, {}, persona="fan", fan_of="KC")
    assert fans.banned(kc, "OAK") and fans.banned(kc, "LV") and not fans.banned(kc, "KC")
    assert not fans.banned(replace(kc, fan_of=None), "LV")


def test_a_fans_tendency_names_his_team_and_its_rival():
    assert rp.tendency("fan", rp.TENDENCY_WEEKS, "CHI") == "Bears fan. Wants no Packers."
    assert rp.tendency("fan", rp.TENDENCY_WEEKS, "LA") == "Rams fan. Wants no 49ers."
    assert rp.tendency("fan", rp.TENDENCY_WEEKS - 1, "CHI") is None


def test_a_new_fan_gets_a_seeded_favourite_and_nobody_else_does():
    from services import replay_draft
    seed = next(s for s in range(200) if "fan" in rp.assign(s, [t for t in range(1, 13) if t != 1]).values())
    teams = replay_draft.new_game(SETTINGS, seed).teams

    assert all((t.fan_of in fans.RIVALS) == (t.persona == "fan") for t in teams)
    assert replay_draft.new_game(SETTINGS, seed).teams == teams


def test_a_fan_values_his_teams_players_ten_percent_more_his_own_and_the_ones_he_buys():
    know = values(p1=star(400.0, team="CHI"), p2=star(400.0, team="DET"))
    mind = replace(st.plain("fan"), fan_of="CHI")

    assert st.price(know, mind, 1) == pytest.approx(440.0) and st.price(know, mind, 1, own=True) == pytest.approx(440.0)
    assert st.price(know, mind, 2) == pytest.approx(400.0)
    assert tv.tally(know, mind, [2], [1], [], 0.0, 1.0, True).score > tv.tally(know, "balanced", [2], [1], [], 0.0, 1.0,
                                                                               True).score


@pytest.fixture
def fan_of_rival(monkeypatch):
    """fan_of_rival(team code) -> a favourite whose rival is that team (one added to the table when no real
    team hates it)."""
    def pick(team_code: str) -> str:
        found = next((fav for fav, hated in fans.RIVALS.items() if hated == fans.club(team_code)), None)
        if found is None:
            monkeypatch.setattr(fans, "RIVALS", {**fans.RIVALS, "TST": fans.club(team_code)})
            found = "TST"
        return found
    return pick


def test_a_fan_turns_down_any_trade_that_brings_him_a_rivals_player(fan_of_rival):
    state, know = league()
    to_team, give, get = FAIR
    favourite = fan_of_rival(know.by_id[give[0]].team)
    fan = with_team(state, to_team, persona="fan", fan_of=favourite)

    assert rt.evaluate(state, SETTINGS, know, USER, to_team, give, get).accepted
    verdict = rt.evaluate(fan, SETTINGS, know, USER, to_team, give, get)
    assert not verdict.accepted and (verdict.decline, verdict.decline_player) == ("decline_rival", give[0])
    assert any(fans.nickname(fans.RIVALS[favourite]) in reason for reason in verdict.reasons)


def test_a_fan_never_proposes_or_is_offered_a_rivals_player(fan_of_rival):
    state, know = league()
    user = rs.user_team(state)
    hated = fans.club(know.by_id[user.roster[0]].team)
    fan = rs.team(with_team(state, CPU, persona="fan", fan_of=fan_of_rival(hated)), CPU)
    state = rs.with_team(state, fan)
    other = rs.team(state, 5)

    for sender, receiver, side in ((user, fan, 0), (other, fan, 0), (fan, other, 1)):
        for deal in ai._candidates(state, SETTINGS, know, sender, receiver):
            assert not any(fans.club(know.by_id[pid].team) == hated for pid in deal[side])


def test_a_fan_never_drafts_a_rivals_player_while_anyone_else_fits(fan_of_rival):
    from services import replay_draft, replay_game as rg
    from tests import replay_fakes as fakes
    data, seed = fakes.fake_season(), 3
    base = replay_draft.new_game(SETTINGS, seed)
    plain = rg.auto_draft(with_team(base, CPU, persona="fan"), SETTINGS, data, seed)
    known = data.as_of(0)
    drafted = Counter(fans.club(known.team_at(pid, 0)) for pid in rs.team(plain, CPU).roster)
    hated = next(code for code, _ in drafted.most_common() if code in fans.RIVALS.values())

    banned = rg.auto_draft(with_team(base, CPU, persona="fan", fan_of=fan_of_rival(hated)), SETTINGS, data, seed)

    assert drafted[hated] >= 1
    assert all(fans.club(known.team_at(pid, 0)) != hated for pid in rs.team(banned, CPU).roster)


def test_a_fan_covers_a_slot_with_a_rivals_player_only_when_nobody_else_can_play_it(fan_of_rival):
    from services import replay_cover
    state, know = league()
    team = rs.team(state, CPU)
    qbs = [pid for pid in team.roster if know.by_id[pid].position == "QB"]
    state = with_team(state, CPU, roster=tuple(pid for pid in team.roster if pid not in qbs))
    first = replay_cover._cover_move(state, SETTINGS, know, CPU)[0]
    hated = know.by_id[first].team
    state = with_team(state, CPU, persona="fan", fan_of=fan_of_rival(hated))

    second = replay_cover._cover_move(state, SETTINGS, know, CPU)[0]
    others = [v.player_id for v in know.values if v.position == "QB" and v.team != hated
              and v.player_id not in rs.rostered(state)]
    forced = replay_cover._cover_move(replace(state, locked=tuple(others)), SETTINGS, know, CPU)[0]

    assert know.by_id[second].team != hated and second != first
    assert know.by_id[forced].team == hated


def test_a_fan_never_claims_or_adds_a_rivals_player(fan_of_rival):
    from services import replay_waiver_ai as wai
    from tests.test_replay_trades import chart_knowledge
    state, know = replace(league()[0], phase="waivers", week=6), chart_knowledge(6)
    claims = wai.cpu_claims(state, SETTINGS, know, 0)
    team_id, made = next(iter(claims.items()))
    hated = fans.club(know.by_id[made[0].add].team)
    state = with_team(state, team_id, persona="fan", fan_of=fan_of_rival(hated))

    assert all(fans.club(know.by_id[c.add].team) != hated for c in wai.cpu_claims(state, SETTINGS, know, 0).get(team_id, []))
    after = wai.cpu_free_agency(replace(state, phase="open"), SETTINGS, know, 0)
    added = set(rs.team(after, team_id).roster) - set(rs.team(state, team_id).roster)
    assert all(fans.club(know.by_id[pid].team) != hated for pid in added)


# ---------------- stasher ----------------

def test_a_stasher_takes_off_half_the_markets_injury_discount_for_a_player_with_a_return_date():
    back = Report(5, 9, "Out; expected back in week 9.")
    hurt = values(p1=star(300.0, healthy_chart=700.0, out=True, news=back))
    gone = values(p1=star(300.0, healthy_chart=700.0, out=True, news=replace(back, back=None)))

    assert rp.private_value(hurt.by_id[1], "stasher") == pytest.approx(300 + 0.5 * 400)
    assert rp.private_value(hurt.by_id[1], "balanced") == pytest.approx(300.0)
    assert rp.private_value(gone.by_id[1], "stasher") == pytest.approx(300.0)       # no return date: the chart


def test_a_stasher_pays_more_than_anyone_for_a_hurt_star_and_looks_at_them_first():
    back = Report(5, 9, "Out; expected back in week 9.")
    know = values(p1=star(300.0, healthy_chart=700.0, out=True, news=back), p2=star(360.0))
    scores = {p: tv.tally(know, p, [2], [1], [], 0.0, 1.0, True).score for p in rp.PERSONAS}

    assert max(scores, key=scores.get) == "stasher" and scores["stasher"] > 0 > scores["balanced"]
    assert ai._order(know.by_id[1], "stash") < ai._order(know.by_id[2], "stash")


# ---------------- haggler ----------------

def test_a_hagglers_opening_offer_gives_less_and_his_counter_his_normal_edge():
    assert rp.offer_edge("haggler") == (-0.15, -0.10)
    assert rp.offer_edge("haggler", counter=True) == rp.HAGGLE_COUNTER_EDGE == (0.05, 0.07)
    assert rp.offer_edge("balanced", counter=True) == rp.offer_edge("balanced")
    assert ai.generous(rp.offer_edge("haggler"), 100.0, 88.0) and not ai.generous(rp.offer_edge("haggler"), 100.0, 106.0)


def _haggler_league():
    """A league where the haggler (CPU 9) has an opening offer for one of the user's players, and that offer."""
    state, know = league()
    state = with_team(state, CPU, persona="haggler")
    user, haggler = rs.user_team(state), rs.team(state, CPU)
    deal = ai._best_deal(state, SETTINGS, know, user, haggler, want=True)
    assert deal is not None
    return rt.record_offer(state, CPU, USER, deal[1], deal[0], "pending"), know


def test_a_haggler_always_counters_a_declined_opening_offer_and_the_counter_stands_when_accepted():
    state, know = _haggler_league()
    opening = state.offers[-1]
    declined = rt.respond(state, SETTINGS, know, opening.id, accept=False)

    countered = replay_counter.haggle_counter(declined, SETTINGS, know,
                                              next(o for o in declined.offers if o.id == opening.id))
    counter = countered.offers[-1]
    verdict = rt.evaluate(countered, SETTINGS, know, USER, CPU, counter.get, counter.give, offered=True, counter=True)

    wanted = max(opening.get, key=lambda pid: chart(know, pid))
    assert counter.counter_to == opening.id and counter.status == "pending" and wanted in counter.get
    assert ai.generous(rp.HAGGLE_COUNTER_EDGE, verdict.value_give, verdict.value_get)
    assert rt.respond(countered, SETTINGS, know, counter.id, accept=True).offers[-1].status == "accepted"
    assert replay_counter.haggle_counter(countered, SETTINGS, know, countered.offers[-2]) is countered   # once


def test_nobody_but_a_haggler_counters_a_declined_offer_of_his_own():
    state, know = _haggler_league()
    opening = state.offers[-1]
    calm = with_team(rt.respond(state, SETTINGS, know, opening.id, accept=False), CPU, persona="balanced")

    assert replay_counter.haggle_counter(calm, SETTINGS, know, calm.offers[-1]) is calm


# ---------------- contender ----------------

def test_a_contender_contends_in_a_playoff_spot_from_league_week_four():
    state = league()[0]

    assert not st.contending(results(state, CPU, [True, True]), SETTINGS, CPU)
    assert st.contending(results(state, CPU, [True, True, True]), SETTINGS, CPU)
    assert not st.contending(results(state, CPU, [False, False, False]), SETTINGS, CPU)


def test_a_contending_contender_pays_more_for_starters_and_less_for_depth():
    state, know = league()
    state = with_team(results(state, CPU, [True] * 3), CPU, persona="contender")
    team = rs.team(state, CPU)
    starters = st._starters(know, SETTINGS, team.roster)
    starter = next(pid for pid in team.roster if pid in starters and know.by_id[pid].position in SKILL)
    bench = next(pid for pid in team.roster if pid not in starters and know.by_id[pid].position in SKILL)
    mind = st.mind_for(state, SETTINGS, know, team, sends=(starter, bench))
    calm_state = with_team(state, CPU, persona="balanced")
    calm = st.mind_for(calm_state, SETTINGS, know, rs.team(calm_state, CPU), sends=(starter, bench))

    assert mind.contending and not calm.contending
    assert st.price(know, mind, starter) == pytest.approx(st.price(know, calm, starter) * (1 + st.CONTEND_START))
    assert st.price(know, mind, bench) == pytest.approx(st.price(know, calm, bench) * (1 - st.CONTEND_BENCH))


# ---------------- dealer ----------------

def test_a_dealer_offers_most_weeks_counters_often_and_looks_at_two_for_two_first():
    state, know = league()
    state = with_team(state, CPU, persona="dealer")
    deals = ai._candidates(state, SETTINGS, know, rs.team(state, 5), rs.team(state, CPU))

    assert rp.offer_rate("dealer") == 0.5 and rp.counter_rate("dealer") >= 0.9
    assert deals and len(deals[0][0]) == len(deals[0][1]) == 2


def test_a_dealer_makes_extra_cpu_attempts_with_himself_in_every_pair(monkeypatch):
    state, know = league()
    state = with_team(state, CPU, persona="dealer")
    asked = []
    monkeypatch.setattr(ai, "_cpu_attempt", lambda s, settings, k, dice, with_team=None: asked.append(with_team) or s)
    monkeypatch.setattr(ai, "_user_offer", lambda s, *rest: s)
    for seed in range(20):
        ai.cpu_trades(state, SETTINGS, know, seed)

    assert asked.count(CPU) >= 5 and set(asked) == {None, CPU}


# ---------------- tilted ----------------

def test_a_tilted_manager_prices_like_panicky_offers_more_and_gives_more_after_two_straight_losses():
    state = with_team(league()[0], CPU, persona="tilted")
    team = rs.team(state, CPU)

    assert rp.now(results(state, CPU, [False]), team) == "tilted"
    assert rp.now(results(state, CPU, [True, False, False]), team) == "panicky"
    assert rp.offer_rate("tilted", 1) == rp.OFFER_RATE["tilted"] < rp.offer_rate("tilted", 2) == rp.TILT_OFFER_RATE
    assert [rp.offer_edge("tilted", n) for n in (0, 2, 3, 4, 9)] == [
        rp.OFFER_EDGE["tilted"], (0.08, 0.10), (0.10, 0.13), (0.12, 0.15), (0.12, 0.15)]
    assert rp.edge(results(state, CPU, [False] * 3), team) == (0.10, 0.13)
    assert rp.private_value(replace(league()[1].values[0], form_chart=-100.0), "tilted") == league()[1].values[0].chart


# ---------------- attachment ----------------

def test_high_on_a_player_asks_eight_percent_at_the_threshold_and_fifteen_for_a_strong_feeling():
    def mark(strength: float) -> float:
        know = values(p1=star(500.0, form_chart=strength * 500.0))
        return st.markup(know, st.plain("balanced"), 1)

    assert mark(0.05) == 0.0
    assert mark(replay_mood.HIGH) == pytest.approx(st.ATTACH_LOW)
    assert mark(0.16) == pytest.approx((st.ATTACH_LOW + st.ATTACH_HIGH) / 2)
    assert mark(0.5) == pytest.approx(st.ATTACH_HIGH)


def test_the_mark_up_raises_what_he_asks_from_any_buyer_and_the_feeling_and_price_agree():
    know = values(p1=star(500.0, form_chart=0.2 * 500.0), p2=star(500.0))
    for to_user in (True, False):
        loved = tv.tally(know, "balanced", [1], [2], [], 0.0, 1.0, to_user)
        assert loved.sent == pytest.approx(500.0 * (1 + st.markup(know, st.plain("balanced"), 1)))
        assert loved.score < loved.bare_score == tv.tally(know, "balanced", [2], [1], [], 0.0, 1.0, to_user).bare_score
    # a contender's bench price is lower, but he never asks under the chart for a player he is high on
    mind = replace(st.plain("contender"), contending=True)
    assert st.price(know, mind, 1, own=True) >= 500.0 * (1 + st.ATTACH_LOW)
    worried = values(p1=star(500.0, form_chart=-0.3 * 500.0, team="CHI"))
    assert st.price(worried, replace(st.plain("fan"), fan_of="CHI"), 1, own=True) <= 500.0


# ---------------- need bonus ----------------

def test_needs_and_deep_come_from_the_lineup_against_the_leagues_starters():
    state, know = league()
    needs = st.position_needs(state, SETTINGS, know)

    assert "RB" in needs[CPU][0]
    for team in state.teams:
        need, deep = needs[team.id]
        assert not need & deep
        for position in need:
            slots = SETTINGS.starters[position]
            mine = st._healthy(know, team.roster, position)
            assert len(mine) < slots or mine[slots - 1] < st.NEED_SHARE * max(
                st._healthy(know, t.roster, position)[slots - 1] for t in state.teams)


def test_the_need_bonus_pays_five_percent_ten_from_depth_and_with_the_lineup_term_never_more_than_ten():
    know = values(p1=star(400.0, position="RB"), p2=star(400.0, position="WR"), p3=star(400.0, position="TE"))
    mind = replace(st.plain("balanced"), needs=frozenset({"RB"}), deep=frozenset({"WR"}))

    def need(sends, kept, gain=0.0):
        return tv.tally(know, mind, sends, kept, [], gain, 1.0, True)

    assert need([3], [1]).need == pytest.approx(0.05 * 400)
    assert need([2], [1]).need == pytest.approx(0.10 * 400)
    assert need([1], [2]).need == 0.0
    gained = need([2], [1], gain=10.0)
    assert gained.lineup > 0 and tv.LINEUP_WEIGHT * gained.lineup + gained.need == pytest.approx(st.NEED_CAP * 400)


# ---------------- why a CPU says no ----------------

def test_a_cpu_with_no_lineup_room_says_so():
    from tests.test_replay_trades import DEEP_AT_WR, P
    only_te, back = P("TE", 25), P("RB", 10)
    state, know = league(changes={only_te: star(500.0), back: star(900.0, star=1.0)})
    locked = lock_free_agents(state, know, "TE")

    verdict = rt.evaluate(locked, SETTINGS, know, USER, DEEP_AT_WR, [back], [only_te])
    assert not verdict.accepted and verdict.decline == "decline_room"


def test_a_lowball_gets_a_value_reply_naming_the_player_asked_for():
    state, know = league()
    cpu, user = rs.team(state, CPU), rs.user_team(state)
    best = max(cpu.roster, key=lambda pid: chart(know, pid))
    worst = min((pid for pid in user.roster if know.by_id[pid].position in SKILL), key=lambda pid: chart(know, pid))

    verdict = rt.evaluate(state, SETTINGS, know, USER, CPU, [worst], [best])
    assert not verdict.accepted and (verdict.decline, verdict.decline_player) in (
        ("decline_value", best), ("decline_need", None))


def _close_deal(state, know):
    """A 1-for-1 the CPU accepts with less than 5% to spare: (user's player, its player)."""
    user, cpu = rs.user_team(state), rs.team(state, CPU)
    for mine in (pid for pid in user.roster if know.by_id[pid].position in SKILL):
        for theirs in (pid for pid in cpu.roster if know.by_id[pid].position in SKILL):
            verdict = rt.evaluate(state, SETTINGS, know, USER, CPU, [mine], [theirs])
            if verdict.accepted and 0 < verdict.score < 0.05 * chart(know, theirs):
                return mine, theirs
    raise AssertionError("no close deal in the fake league")


def test_a_cpu_high_on_the_player_asked_for_turns_down_what_it_would_take_without_the_mark_up():
    state, know = league()
    mine, theirs = _close_deal(state, know)
    loved = {theirs: {"form_chart": 0.3 * chart(know, theirs)}}
    state, know = league(changes=loved)

    verdict = rt.evaluate(state, SETTINGS, know, USER, CPU, [mine], [theirs])
    assert replay_mood.feeling(know.by_id[theirs], "balanced").kind == "high"
    assert not verdict.accepted and (verdict.decline, verdict.decline_player) == ("decline_attached", theirs)


def test_a_cpu_that_would_lose_a_starter_or_only_get_depth_says_it_does_not_need_that(monkeypatch):
    state, know = league()
    cpu, user = rs.team(state, CPU), rs.user_team(state)
    starters = st._starters(know, SETTINGS, cpu.roster)
    starter = max((pid for pid in cpu.roster if pid in starters and know.by_id[pid].position in SKILL),
                  key=lambda pid: chart(know, pid))
    scrub = min((pid for pid in user.roster if know.by_id[pid].position in SKILL), key=lambda pid: chart(know, pid))
    verdict = rt.evaluate(state, SETTINGS, know, USER, CPU, [scrub], [starter])
    assert (verdict.decline, verdict.decline_position) == ("decline_need", know.by_id[starter].position)

    position = know.by_id[scrub].position
    real = st.position_needs
    monkeypatch.setattr(st, "position_needs", lambda *a: {tid: (n, d | {position}) for tid, (n, d) in real(*a).items()})
    side = rt._side(state, SETTINGS, know, cpu, user, (starter,), (scrub,), (), rt.free_agents(state, know))
    assert rt._decline(SETTINGS, know, replace(side, tally=replace(side.tally, lineup=0.0, score=-1.0))) == (
        "decline_need", None, position)


def test_the_reply_is_stored_on_the_declined_offer_and_kept_out_of_the_feed(fan_of_rival):
    from services import replay_chat
    state, know = league()
    to_team, give, get = FAIR
    fan = with_team(state, to_team, persona="fan", fan_of=fan_of_rival(know.by_id[give[0]].team))
    after, verdict = rt.propose(fan, SETTINGS, know, to_team, give, get)
    replied = replay_chat.decline_reply(after, know, 1, after.offers[-1].id, verdict)

    assert replied.offers[-1].reply == replied.chat[-1].text
    assert replied.chat[-1].kind == "decline_rival" and not replay_chat.chat_for(replied)
    assert replay_chat.decline_reply(replied, know, 1, replied.offers[-1].id, verdict) == replied     # once


# ---------------- the round trip ----------------

def _best_single(state, know, mine: int) -> int | None:
    cpu = rs.team(state, CPU)
    for theirs in sorted((p for p in cpu.roster if know.by_id[p].position in SKILL), key=lambda p: -chart(know, p)):
        if rt.evaluate(state, SETTINGS, know, USER, CPU, (mine,), (theirs,)).accepted:
            return theirs
    return None


def _setup(persona: str, mine: int):
    """CPU 9 in `persona` at its most generous: a fan of the user's player's team, a contender in a playoff spot,
    tilted after three losses, a stasher buying the user's player hurt with a return date."""
    changes = {}
    if persona == "stasher":
        base = league()[1].by_id[mine]
        changes = {mine: {"out": True, "availability": 0.5, "news": Report(1, 4, "Out; back in week 4."),
                          "healthy_chart": base.healthy_chart}}
    state, know = league(changes=changes)
    state = with_team(state, CPU, persona=persona, fan_of=fans.club(know.by_id[mine].team) if persona == "fan" else None)
    if persona in ("contender", "tilted"):
        state = results(state, CPU, [persona == "contender"] * 3)
    return state, know


@pytest.mark.parametrize("persona", rp.PERSONAS)
def test_no_round_trip_with_one_cpu_leaves_the_user_ahead_by_more_than_the_styles_stated_edge(persona):
    """A for B (his best player he takes for A, or his own offer for A), then B back for the best player he takes
    for B: the user ends up at most `stated_edge` ahead of where he started, on neutral chart."""
    user, trips = rs.user_team(league()[0]), 0
    for mine in [pid for pid in user.roster if league()[1].by_id[pid].position in SKILL][:4]:
        state, know = _setup(persona, mine)
        firsts = [(b, False) for b in (_best_single(state, know, mine),) if b is not None]
        offered = ai._best_deal(state, SETTINGS, know, rs.user_team(state), rs.team(state, CPU), want=True,
                                wanted=mine)
        firsts += [(offered[1][0], True) for _ in (1,) if offered is not None and offered[0] == (mine,)
                   and len(offered[1]) == 1]
        for got, as_offer in firsts:
            verdict = rt.evaluate(state, SETTINGS, know, USER, CPU, (mine,), (got,), offered=as_offer)
            swapped = rt._apply(state, SETTINGS, know, USER, CPU, (mine,), (got,), verdict)
            back = _best_single(replace(swapped, traded={}), know, got)
            end = chart(know, back) if back is not None else chart(know, got)
            assert end - chart(know, mine) <= tv.stated_edge(persona) * chart(know, mine) + 0.5, (persona, mine, got, back)
            trips += 1
    assert trips >= 2


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [3, 8])
def test_needs_prices_and_decline_reasons_never_see_the_future(week):
    from tests import replay_fakes as fakes
    from tests.test_replay_trades import chart_knowledge
    data = fakes.fake_season()
    state = with_team(replace(league()[0], week=week), CPU, persona="contender")
    state = results(state, CPU, [True] * (week - 1))
    know, shaken = chart_knowledge(week, data=data), chart_knowledge(week, data=fakes.future_changed(data, week))
    user, cpu = rs.user_team(state), rs.team(state, CPU)
    mine = [pid for pid in user.roster if know.by_id[pid].position in SKILL][:3]
    theirs = [pid for pid in cpu.roster if know.by_id[pid].position in SKILL][:3]

    assert st.position_needs(state, SETTINGS, know) == st.position_needs(state, SETTINGS, shaken)
    assert st.mind_for(state, SETTINGS, know, cpu, theirs[:1], mine[:1]) == st.mind_for(
        state, SETTINGS, shaken, cpu, theirs[:1], mine[:1])
    for give in mine:
        for get in theirs:
            assert rt.evaluate(state, SETTINGS, know, USER, CPU, [give], [get]) == rt.evaluate(
                state, SETTINGS, shaken, USER, CPU, [give], [get])
