"""The Ask AI evaluation, offline: the saved cases route their tools, the checks score answers,
the runner writes/resumes a JSON report and the diff flags regressions. No model or network."""
import json
from collections import Counter

import pytest
from sqlalchemy.orm import Session

from services import agent_eval, eval_report
from services.agent_routing import route_plan
from services.eval_cases import CASES, PLAYERS, SPLITS, Case
from services.eval_checks import check_grounding, score_answer, score_live
from services.player_index import PlayerIndex
from tests.agent_fakes import ScriptedLLM, answer, call
from tools.definitions import TOOLS

INDEX = PlayerIndex(PLAYERS)
TOOL_NAMES = {t.name for t in TOOLS}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_case_routes_its_expected_tools(case):
    plan = route_plan([{"role": "user", "content": t} for t in case.turns], INDEX)
    routed = set(plan.tools)
    assert not case.expect_any or set(case.expect_any) & routed, (case.turns, plan.intents, plan.tools)
    assert set(case.require_all) <= routed, (case.turns, plan.tools)


def test_cases_are_well_formed_and_cover_every_intent():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids)) and len(CASES) >= 60
    for case in CASES:
        named = {*case.expect_any, *case.require_all, *case.forbid_tools}
        assert named <= TOOL_NAMES, (case.id, named - TOOL_NAMES)
        assert case.split in SPLITS and case.intent
        assert case.no_tools != bool(case.expect_any or case.require_all), case.id
    holdout = sum(c.split == "holdout" for c in CASES) / len(CASES)
    assert 0.25 <= holdout <= 0.45
    intents = Counter(c.intent for c in CASES)
    assert all(n >= 2 for name, n in intents.items() if name not in ("memory", "trending")), intents
    assert sum(bool(c.before) for c in CASES) >= 6 and sum(c.no_tools for c in CASES) >= 3


START = Case("x", "Who should I start?", ("get_my_team",), True, decision=True)


def test_answer_checks_pass_a_good_answer_and_name_each_problem():
    good = score_answer(START, "Start Gibbs over Hall: lean.", ["get_my_team"], [True])
    assert good.passed and good.checks["decision"] and good.checks["players_grounded"] is None
    bad = score_answer(START, "## Roster\nWhich players are on your team? It's guaranteed.", ["get_weekly_rankings"],
                       [False])
    assert set(bad.problems) >= {"asked for the roster", "every tool call failed", "markdown in the reply",
                                 "no clear decision"}
    assert any("guarantee" in p for p in bad.problems) and any("expected one of" in p for p in bad.problems)


def test_tool_rules_require_all_forbid_and_out_of_scope():
    case = Case("y", "q", require_all=("a", "b"), forbid_tools=("c",))
    problems = score_answer(case, "Fine.", ["a", "c"], [True, True]).problems
    assert any("missing required tools ['b']" in p for p in problems) and any("forbidden" in p for p in problems)
    oos = Case("z", "Write me a poem", no_tools=True)
    assert score_answer(oos, "I only help with fantasy football.", ["remember"], [True]).passed
    assert not score_answer(oos, "x" * 700, ["get_weekly_rankings"], [True]).passed


def test_older_score_live_keeps_working():
    assert score_live(START, "Start Gibbs.", ["get_my_team"], [True]) == []
    assert len(score_live(START, "Which players are on your team?", ["get_weekly_rankings"], [True])) == 3


def test_grounding_check_uses_prompt_turns_and_tool_results_but_not_caveats():
    asked = [{"role": "system", "content": "Rosters: Puka Nacua"}, {"role": "user", "content": "Gibbs or Nacua?"}]
    evidence = ['{"player": "Jahmyr Gibbs (RB, DET)", "proj": 18.4}']
    ok = check_grounding("Start Gibbs (18.4) over Nacua.", asked, evidence, INDEX)
    assert ok.ok
    stray = check_grounding("Start Bijan Robinson, 31.7 points.\nNot verified against the app's data: x.",
                            asked, evidence, INDEX)
    assert stray.players == ("Bijan Robinson",) and stray.numbers == ("31.7 points",)


def result(id_, passed, split="dev", intent="start_sit", repeat=0, tokens=1000):
    return {"id": id_, "intent": intent, "split": split, "repeat": repeat, "passed": passed,
            "checks": {"tools": passed, "decision": None}, "rounds": 2, "tokens": {"prompt": tokens, "completion": 0},
            "seconds": 1.0, "unverified": [] if passed else ["9.9"]}


def test_summary_and_compare_flag_holdout_regressions(tmp_path):
    before = [result("a", True), result("b", True, "holdout"), result("c", False, "holdout")]
    after = [result("a", True), result("b", False, "holdout"), result("c", True, "holdout", tokens=3000),
             result("c", False, "holdout", repeat=1)]
    summary = eval_report.summarize(after)
    assert summary["cases"] == 3 and summary["runs"] == 4 and summary["pass_all_repeats"] == round(1 / 3, 3)
    assert summary["checks"] == {"tools": 0.5} and summary["splits"]["holdout"] == round(1 / 3, 3)
    eval_report.save(tmp_path / "b.json", {"git": "old"}, before)
    eval_report.save(tmp_path / "a.json", {"git": "new"}, after)
    lines, regressed = eval_report.compare(eval_report.load(tmp_path / "b.json"), eval_report.load(tmp_path / "a.json"))
    assert regressed and "FAIL now  b" in lines
    _, improved = eval_report.compare(eval_report.load(tmp_path / "a.json"), eval_report.load(tmp_path / "b.json"))
    assert not improved
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(ValueError, match="not an eval report"):
        eval_report.load(tmp_path / "x.json")


def test_pacer_waits_for_the_token_window():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
    pacer = agent_eval.Pacer(8_000, 30, clock=lambda: now[0], sleep=sleep)
    assert pacer.wait() == 0
    pacer.record(5_000, 3)
    now[0] = 10.0
    assert pacer.wait() == 50.0 and slept == [50.0]          # 5k used + ~5k next > 8k: wait out the minute
    assert agent_eval.Pacer(None, None).wait() == 0


def with_usage(step, prompt=100, completion=20):
    return {**step, "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


MULTI = Case("mt", "should I start him?", ("get_player_profile",), before=("How is Buy Low Guy doing?",),
             decision=True)
PROFILE = {"player": "Buy Low Guy"}


def multi_turn_steps():
    return [with_usage(call("get_player_profile", PROFILE)), with_usage(answer("Buy Low Guy: 12.0 ppg.")),
            with_usage(call("get_player_profile", PROFILE)), with_usage(answer("Start Buy Low Guy (12.0 ppg)."))]


def test_run_case_plays_every_turn_and_scores_the_last(session, seeded):
    meter = agent_eval.UsageMeter(ScriptedLLM(multi_turn_steps()))
    outcome = agent_eval.run_case(session, meter, MULTI)
    assert outcome["passed"], outcome["problems"]
    assert outcome["tools"] == ["get_player_profile"] and outcome["requests"] == 4
    assert outcome["tokens"] == {"prompt": 400, "completion": 80} and outcome["checks"]["numbers_grounded"]
    second_turn = meter._client.requests[2]["messages"]
    assert [m["role"] for m in second_turn[1:4]] == ["user", "assistant", "user"]


class ClosableLLM(ScriptedLLM):
    closed = False

    def close(self):
        self.closed = True


def test_live_run_writes_a_report_and_resumes(engine, seeded, monkeypatch, tmp_path):
    clients = [ClosableLLM(multi_turn_steps())]
    monkeypatch.setattr(agent_eval, "_client", lambda provider: (clients[-1], provider or "groq"))
    monkeypatch.setattr(agent_eval, "_session_factory", lambda db: lambda: Session(engine))
    monkeypatch.setattr(agent_eval, "CASES", (MULTI,))
    monkeypatch.setattr(agent_eval, "PACE", {})
    out = tmp_path / "run.json"
    args = ["--out", str(out), "--provider", "groq"]
    assert agent_eval.run_live(agent_eval.parse_args(args)) == 0
    report = json.loads(out.read_text())
    assert report["schema"] == 1 and report["summary"]["pass_rate"] == 1.0
    assert set(report["meta"]["hashes"]) == {"prompt", "tools", "routing"} and report["meta"]["provider"] == "groq"
    assert report["cases"][0]["reply"].startswith("Start Buy Low Guy") and clients[0].closed
    clients.append(ScriptedLLM([]))           # nothing left to run: no requests
    assert agent_eval.run_live(agent_eval.parse_args(["--resume", str(out)])) == 0
    assert clients[-1].requests == [] and len(json.loads(out.read_text())["cases"]) == 1


def test_select_cases_filters_by_split_id_and_sample():
    holdout = agent_eval.select_cases(None, "holdout", None, 0)
    assert holdout and all(c.split == "holdout" for c in holdout)
    assert all("trade" in c.id for c in agent_eval.select_cases("trade", "all", None, 0))
    sample = agent_eval.select_cases(None, "all", 5, 1)
    assert len(sample) == 5 and sample == agent_eval.select_cases(None, "all", 5, 1)


def test_redacted_report_keeps_metrics_but_no_answer_text(engine, seeded, monkeypatch, tmp_path):
    monkeypatch.setattr(agent_eval, "_client", lambda provider: (ScriptedLLM(multi_turn_steps()), "groq"))
    monkeypatch.setattr(agent_eval, "_session_factory", lambda db: lambda: Session(engine))
    monkeypatch.setattr(agent_eval, "CASES", (MULTI,))
    monkeypatch.setattr(agent_eval, "PACE", {})
    out = tmp_path / "run.json"
    assert agent_eval.run_live(agent_eval.parse_args(["--out", str(out), "--redact"])) == 0
    case = json.loads(out.read_text())["cases"][0]
    assert case["reply"] == "" and case["passed"] and case["checks"]["decision"]
    assert "Buy Low Guy" not in out.read_text()


def test_freeze_copies_without_tokens_and_never_overwrites(tmp_path):
    import sqlite3

    source = tmp_path / "app.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, hashed_password TEXT)")
        db.execute("CREATE TABLE platform_connections (id INTEGER PRIMARY KEY, access_token TEXT, "
                   "refresh_token TEXT, token_expires_at TEXT)")
        db.execute("CREATE TABLE leagues (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users VALUES (1, 'hash')")
        db.execute("INSERT INTO platform_connections VALUES (1, 'secret-access', 'secret-refresh', '2026-11-01')")
        db.execute("INSERT INTO leagues VALUES (1, 'Family League')")
    target = tmp_path / "frozen.db"
    agent_eval.freeze(target, source=str(source))
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT access_token, refresh_token, token_expires_at FROM platform_connections"
                          ).fetchall() == [(None, None, None)]
        assert db.execute("SELECT name FROM leagues").fetchall() == [("Family League",)]
    assert b"secret-access" not in target.read_bytes()
    with pytest.raises(SystemExit, match="already exists"):
        agent_eval.freeze(target, source=str(source))
    agent_eval.freeze(target, force=True, source=str(source))
    with pytest.raises(SystemExit, match="other than the app database"):
        agent_eval.freeze(source, force=True, source=str(source))
    with sqlite3.connect(source) as db:      # the original is untouched
        assert db.execute("SELECT access_token FROM platform_connections").fetchone() == ("secret-access",)
