"""Replay League: a CPU filling the starting slots a trade emptied (services/replay_cover.py, re-exported by
replay_waiver_ai), on the fake league."""
from dataclasses import replace

from services import replay_state as rs, replay_values as rv, replay_waiver_ai as ai, replay_waivers
from services.replay_pool import SKILL
from services.replay_state import State
from services.replay_values import Knowledge, ReplayValue
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings()            # 12 teams, 15-man rosters, the user is team 1
USER = SETTINGS.draft_slot
CPUS = tuple(t for t in range(1, SETTINGS.teams + 1) if t != USER)


def league(week: int = 1, phase: str = "waivers") -> State:
    return replace(fakes.drafted_state(SETTINGS), phase=phase, week=week)


def know(week: int = 1) -> Knowledge:
    return fakes.fake_knowledge(week, SETTINGS)


def without(state: State, team_id: int, *player_ids: int) -> State:
    """The state with those players simply gone from a roster (not dropped, so not locked)."""
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p not in player_ids)))


def at(state: State, team_id: int, position: str) -> list[int]:
    return [pid for pid in rs.team(state, team_id).roster if know().by_id[pid].position == position]


# ---------------- covering a slot after a trade ----------------

def test_a_cpu_that_traded_away_its_only_tight_end_adds_the_best_free_one():
    cpu, state = CPUS[0], league(3, "open")
    alone = without(state, cpu, *at(state, cpu, "TE")[1:])
    traded = without(alone, cpu, *at(alone, cpu, "TE"))
    free_te = max((v for v in replay_waivers.available_players(traded, know(3)) if v.position == "TE"),
                  key=lambda v: (v.chart, v.healthy_value, -v.player_id))

    after = ai.cpu_cover(traded, SETTINGS, know(3), [cpu, USER])

    assert at(after, cpu, "TE") == [free_te.player_id] and after.events[-1].kind == "add"
    assert rs.user_team(after) == rs.user_team(traded) and ai.cpu_cover(after, SETTINGS, know(3), [cpu]) == after


def test_a_full_cpu_drops_a_skill_player_no_slot_needs_to_cover():
    cpu, state = CPUS[1], league(3, "open")
    traded = without(state, cpu, *at(state, cpu, "TE"))
    full = rs.with_team(traded, replace(rs.team(traded, cpu), roster=(*rs.team(traded, cpu).roster,
                        *[v.player_id for v in replay_waivers.available_players(traded, know(3))
                          if v.position == "WR"][:SETTINGS.roster_size - len(rs.team(traded, cpu).roster)])))

    after = ai.cpu_cover(full, SETTINGS, know(3), [cpu])

    positions = [know(3).by_id[pid].position for pid in rs.team(after, cpu).roster]
    assert len(positions) == SETTINGS.roster_size and positions.count("TE") == 1
    assert rv.missing_slots(positions, SETTINGS) == []


def _set(know: Knowledge, changes: dict[int, dict]) -> Knowledge:
    values = tuple(replace(v, **changes.get(v.player_id, {})) for v in know.values)
    return replace(know, values=values, by_id={v.player_id: v for v in values})


def _position(know: Knowledge, state: State, team_id: int, position: str) -> list[int]:
    return [pid for pid in rs.team(state, team_id).roster if know.by_id[pid].position == position]


def _best_free(know: Knowledge, state: State, position: str) -> ReplayValue:
    return max((v for v in know.values if v.position == position and v.player_id not in rs.rostered(state)),
               key=lambda v: (v.chart, v.healthy_value, -v.player_id))


def test_cover_skips_out_players_and_waits_out_the_waiver_phase():
    know, cpu = fakes.fake_knowledge(1), CPUS[0]
    state = league(phase="open")
    state = without(state, cpu, *_position(know, state, cpu, "TE"))
    hurt = _best_free(know, state, "TE")
    know = _set(know, {hurt.player_id: {"out": True}})

    covered = ai.cpu_cover(state, SETTINGS, know, [cpu])
    waiting = ai.cpu_cover(replace(state, phase="waivers"), SETTINGS, know, [cpu])

    added = _position(know, covered, cpu, "TE")
    assert len(added) == 1 and added != [hurt.player_id]
    assert waiting == replace(state, phase="waivers")


def test_cover_tries_every_missing_slot():
    know, cpu = fakes.fake_knowledge(1), CPUS[0]
    state = league(phase="open")
    state = without(state, cpu, *_position(know, state, cpu, "TE"), *_position(know, state, cpu, "K"))
    free_tes = [v.player_id for v in know.values if v.position == "TE" and v.player_id not in rs.rostered(state)]
    state = replace(state, locked=tuple(free_tes))                    # no tight end to be had: the kicker still is

    covered = ai.cpu_cover(state, SETTINGS, know, [cpu])

    assert len(_position(know, covered, cpu, "K")) == 1 and not _position(know, covered, cpu, "TE")


def test_cover_drops_by_healthy_chart_so_a_false_out_for_season_report_costs_nobody():
    know, cpu = fakes.fake_knowledge(1), CPUS[0]
    state = league(phase="open")
    team = rs.team(state, cpu)
    extra = _best_free(know, state, "WR").player_id                   # a full roster with no tight end
    state = rs.with_team(state, replace(team, roster=(*(p for p in team.roster if know.by_id[p].position != "TE"),
                                                      extra)))
    roster = [know.by_id[pid] for pid in rs.team(state, cpu).roster]
    missing = rv.missing_slots([v.position for v in roster], SETTINGS)
    safe = [v for v in sorted((v for v in roster if v.position in SKILL),
                              key=lambda v: (v.healthy_chart, v.healthy_value, v.ros_ppg, v.player_id))
            if len(rv.missing_slots([w.position for w in roster if w is not v] + ["TE"], SETTINGS)) < len(missing)]
    lowest, reported = safe[0], safe[1]
    know = _set(know, {reported.player_id: {"chart": 0.0}})           # reported out for the season: healthy as before

    covered = ai.cpu_cover(state, SETTINGS, know, [cpu])

    held = rs.team(covered, cpu).roster
    assert len(held) == SETTINGS.roster_size and len(_position(know, covered, cpu, "TE")) == 1
    assert reported.player_id in held and lowest.player_id not in held


def test_a_roster_spot_a_trade_cut_left_open_is_refilled_with_the_best_valid_free_agent(monkeypatch):
    """Review: the refill after a trade's forced cut had no test. A spare receiver gone (no starting slot lost):
    the best free agent who is not out, not of the fan's rival club and within the position limits comes in."""
    week, team_id = 3, CPUS[0]
    knowledge, state = know(week), league(week, phase="open")
    receivers = at(state, team_id, "WR")
    bench = min(receivers, key=lambda pid: knowledge.by_id[pid].chart)
    short = without(state, team_id, bench)
    free = [v for v in knowledge.values if v.player_id not in rs.rostered(short) | set(short.locked) and not v.out
            and v.position in SKILL]
    best = max(free, key=lambda v: (v.chart, v.healthy_value, -v.player_id))
    from services import replay_cover
    monkeypatch.setattr(replay_cover.replay_fans, "banned", lambda team, nfl: nfl == best.team)   # his rival's club
    fan = short
    team = rs.team(fan, team_id)
    assert len(team.roster) == SETTINGS.roster_size - 1 and not rv.missing_slots(
        [knowledge.by_id[pid].position for pid in team.roster], SETTINGS)

    after = rs.team(ai.cpu_cover(fan, SETTINGS, knowledge, [team_id]), team_id)

    added = next(pid for pid in after.roster if pid not in team.roster)
    kept = [knowledge.by_id[pid] for pid in rs.held(team)]
    valid = [v for v in knowledge.values if v.player_id not in rs.rostered(fan) | set(fan.locked) and not v.out
             and v.position in SKILL and v.team != best.team
             and not rv.crowded(kept, [v], v.position, SETTINGS)]
    assert len(after.roster) == SETTINGS.roster_size and added != best.player_id
    assert added == max(valid, key=lambda v: (v.chart, v.healthy_value, -v.player_id)).player_id
