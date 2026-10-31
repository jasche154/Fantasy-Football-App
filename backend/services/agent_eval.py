"""Ask AI evaluation: saved questions (services/eval_cases.py) through the real assistant, scored.

Two levels:
- Offline (pytest, tests/test_agent_eval.py): every case's turns must route its expected tools,
  and the checks are unit tested. No model or network involved; this is what CI runs.
- Live (manual; needs a provider key and uses free-tier quota): runs each case through
  run_agent, then checks tools, grounding (players and numbers must come from the tools, the
  prompt or the user), a clear decision, required/forbidden phrases, plain text and errors.
  Writes a JSON report after every case, so a run stopped by a daily cap can be resumed.

    python -m services.agent_eval                              # every case, LLM_CHAIN from .env
    python -m services.agent_eval --split holdout --provider gemini
    python -m services.agent_eval --only trade --repeat 3      # ids containing "trade", 3 times each
    python -m services.agent_eval --sample 10 --seed 1         # quick smoke run
    python -m services.agent_eval --freeze eval_runs/week9.db  # copy the DB once ...
    python -m services.agent_eval --db eval_runs/week9.db      # ... so before/after runs see the same data
    python -m services.agent_eval --resume eval_runs/<file>.json
    python -m services.agent_eval --compare before.json after.json   # exit 1 if holdout got worse

Judge a change on the holdout split, against a baseline run made on the same frozen DB.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import logging
import random
import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from services import eval_report
from services.eval_cases import CASES, Case  # noqa: F401 (re-exported for older imports)
from services.eval_checks import check_grounding, score_answer, score_live  # noqa: F401

log = logging.getLogger(__name__)
RUNS_DIR = eval_report.BACKEND / "eval_runs"
MAX_REPLY_CHARS = 12_000           # what the chat sends back as an earlier assistant turn
REPORT_REPLY_CHARS = 2_000
MAX_RATE_WAIT = 120                # seconds; a longer Retry-After means a daily cap: stop and resume later
# Free-tier pacing for the first provider in the chain: (tokens per minute, requests per minute).
PACE: dict[str, tuple[int | None, int | None]] = {"groq": (8_000, 30), "gemini": (200_000, 10),
                                                  "openrouter": (100_000, 15), "ollama": (None, None)}
FIRST_CASE_TOKENS = 4_000          # guess for the first case, before real usage is known


class UsageMeter:
    """Wraps an LLM client and adds up the tokens and requests it reports; budgets pass through."""

    def __init__(self, client: Any):
        self._client = client
        self.prompt = self.completion = self.requests = 0

    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        self.requests += 1
        response = self._client.complete(messages, tools)
        usage = response.get("usage") or {}
        self.prompt += int(usage.get("prompt_tokens") or 0)
        self.completion += int(usage.get("completion_tokens") or 0)
        return response

    @property
    def profile(self) -> Any:
        return getattr(self._client, "profile", None)

    @property
    def last_provider(self) -> str | None:
        return getattr(self._client, "last_provider", None)

    def snapshot(self) -> tuple[int, int, int]:
        return self.prompt, self.completion, self.requests


class Pacer:
    """Waits between cases so the last minute's tokens and requests stay under the free tier."""

    def __init__(self, tpm: int | None, rpm: int | None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.tpm, self.rpm, self._clock, self._sleep = tpm, rpm, clock, sleep
        self._window: deque[tuple[float, int, int]] = deque()      # (time, tokens, requests)
        self._last = FIRST_CASE_TOKENS

    def wait(self) -> float:
        """Sleeps until the next case (sized like the last one) fits; returns the seconds waited."""
        waited = 0.0
        while self._over():
            pause = max(1.0, self._window[0][0] + 60 - self._clock())
            self._sleep(pause)
            waited += pause
            self._window.popleft()
        return waited

    def record(self, tokens: int, requests: int) -> None:
        self._window.append((self._clock(), tokens, requests))
        self._last = tokens or self._last

    def _over(self) -> bool:
        now = self._clock()
        while self._window and self._window[0][0] <= now - 60:
            self._window.popleft()
        if not self._window:
            return False
        tokens = sum(t for _, t, _ in self._window) + self._last
        requests = sum(r for _, _, r in self._window)
        return bool((self.tpm and tokens > self.tpm) or (self.rpm and requests >= self.rpm))


def _session_factory(db: Path | None):
    if db is None:
        from database.db import SessionLocal
        return SessionLocal
    from sqlalchemy.orm import sessionmaker

    from database.db import make_engine
    if not Path(db).exists():
        raise SystemExit(f"{db} doesn't exist; make it with --freeze first.")
    return sessionmaker(bind=make_engine(f"sqlite:///{Path(db).resolve()}"), expire_on_commit=False)


def freeze(target: Path) -> None:
    """Copies the app database (a consistent snapshot, even while the app runs) for repeatable runs."""
    import sqlite3

    from database.db import engine
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(engine.url.database) as source, sqlite3.connect(target) as copy:
        source.backup(copy)
    log.info("Froze the database to %s", target)


def run_case(session: Any, client: UsageMeter, case: Case) -> dict[str, Any]:
    """Asks the case's turns in one chat and scores the last answer. Raises AgentUnavailable."""
    from services.agent import run_agent
    from services.agent_prompt import league_context
    from services.player_index import get_index

    before = client.snapshot()
    started = time.monotonic()
    history: list[dict] = []
    memory: tuple[str, ...] = ()
    reply = None
    for turn in case.turns:
        history.append({"role": "user", "content": turn})
        reply = run_agent(session, client, history, memory=memory)
        memory = reply.memory
        history.append({"role": "assistant", "content": reply.reply[:MAX_REPLY_CHARS]})
    asked = [{"role": "system", "content": league_context(session)}, *history[:-1]]
    report = check_grounding(reply.reply, asked, reply.evidence, get_index(session))
    tools = [t.name for t in reply.tool_calls]
    result = score_answer(case, reply.reply, tools, [t.ok for t in reply.tool_calls], report)
    prompt, completion, requests = (now - then for now, then in zip(client.snapshot(), before))
    return {"passed": result.passed, "checks": dict(result.checks), "problems": list(result.problems),
            "tools": tools, "tool_ok": [t.ok for t in reply.tool_calls], "rounds": reply.rounds,
            "stop_reason": reply.stop_reason, "provider": reply.provider, "unverified": list(reply.unverified),
            "eval_unverified": list(result.unverified), "tokens": {"prompt": prompt, "completion": completion},
            "requests": requests, "seconds": round(time.monotonic() - started, 1),
            "reply": reply.reply[:REPORT_REPLY_CHARS]}


def _failed(error: str, client: UsageMeter, before: tuple[int, int, int], started: float) -> dict[str, Any]:
    prompt, completion, requests = (now - then for now, then in zip(client.snapshot(), before))
    return {"passed": False, "checks": {}, "problems": [error], "tools": [], "tool_ok": [], "rounds": 0,
            "stop_reason": None, "provider": None, "unverified": [], "eval_unverified": [],
            "tokens": {"prompt": prompt, "completion": completion}, "requests": requests,
            "seconds": round(time.monotonic() - started, 1), "reply": "", "error": error}


def select_cases(only: str | None, split: str, sample: int | None, seed: int) -> list[Case]:
    cases = [c for c in CASES if (only is None or only in c.id) and split in ("all", c.split)]
    if sample and sample < len(cases):
        cases = sorted(random.Random(seed).sample(cases, sample), key=CASES.index)
    return cases


def _client(provider: str | None):
    from config import get_settings
    from services.llm_client import build_llm_client

    settings = get_settings()
    if provider:
        settings = settings.model_copy(update={"llm_chain": provider})
    return build_llm_client(settings), settings.llm_chain


def _meta(args: argparse.Namespace, chain: str, client: Any, session: Any) -> dict[str, Any]:
    from services.agent_prompt import data_weeks

    season, stats_week, lineup_week = data_weeks(session)
    profile = client.profile
    return {"started": datetime.now().isoformat(timespec="seconds"), "git": eval_report.git_state(),
            "provider": chain, "model": getattr(profile, "model", None), "hashes": eval_report.code_hashes(),
            "data": {"season": season, "stats_week": stats_week, "lineup_week": lineup_week,
                     "db": Path(args.db).name if args.db else "app database"},
            "args": {k: v for k, v in vars(args).items() if k not in ("compare", "freeze")}}


def run_live(args: argparse.Namespace) -> int:
    from services.llm_client import AgentUnavailable

    llm, chain = _client(args.provider)
    if llm is None:
        log.error("No AI provider is configured for %r (set its API key in backend/.env).", chain)
        return 2
    client, sessions = UsageMeter(llm), _session_factory(args.db)
    cases = select_cases(args.only, args.split, args.sample, args.seed)
    if args.resume:
        out = Path(args.resume)
        old = eval_report.load(out)
        meta, results = old["meta"], list(old["cases"])
        if meta.get("hashes") != eval_report.code_hashes():
            log.warning("The prompt, tools or routing changed since this run started; results will mix versions.")
    else:
        out = Path(args.out) if args.out else RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{chain.replace(',', '+')}.json"
        with sessions() as session:
            meta, results = _meta(args, chain, client, session), []
    done = {(r["id"], r["repeat"]) for r in results}
    pacer = Pacer(*PACE.get(chain.split(",")[0].strip(), (None, None)))
    for repeat in range(args.repeat):
        for case in cases:
            if (case.id, repeat) in done:
                continue
            pacer.wait()
            outcome = _run_with_retry(sessions, client, case, AgentUnavailable)
            if outcome is None:
                log.error("Provider quota exhausted; saved %s. Continue later with --resume %s", out, out)
                eval_report.save(out, meta, results)
                return 3
            pacer.record(outcome["tokens"]["prompt"] + outcome["tokens"]["completion"], outcome["requests"])
            results.append({"id": case.id, "intent": case.intent, "split": case.split, "repeat": repeat, **outcome})
            eval_report.save(out, meta, results)
            log.info("%s %s%s", "PASS" if outcome["passed"] else "FAIL", case.id,
                     "" if outcome["passed"] else f": {outcome['problems']}")
    summary = eval_report.summarize(results)
    log.info("%s passed (%s), report: %s", summary["pass_rate"], summary["splits"], out)
    return 0 if all(r["passed"] for r in results) else 1


def _run_with_retry(sessions, client: UsageMeter, case: Case, unavailable: type) -> dict[str, Any] | None:
    """One case; a short rate limit is waited out once. None: a long (daily) limit, stop the run."""
    for attempt in range(2):
        before, started = client.snapshot(), time.monotonic()
        with sessions() as session:
            try:
                return run_case(session, client, case)
            except unavailable as exc:
                wait = exc.retry_after or 60
                if exc.kind != "rate_limit":
                    return _failed(exc.kind, client, before, started)
                if wait > MAX_RATE_WAIT:
                    return None
                if attempt:
                    return _failed("rate_limit", client, before, started)
                log.warning("Rate limited on %s; waiting %ss", case.id, wait)
                time.sleep(wait)
            except Exception as exc:      # report and keep going; one bad case shouldn't stop the run
                log.exception("Case %s crashed", case.id)
                return _failed(type(exc).__name__, client, before, started)
    return None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="run cases whose id contains this text")
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="all")
    parser.add_argument("--sample", type=int, help="run a random sample of this many cases")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=1, help="run each case this many times (pass^k)")
    parser.add_argument("--provider", help="LLM chain for this run, e.g. groq or gemini or groq,gemini")
    parser.add_argument("--db", help="a frozen database copy (see --freeze)")
    parser.add_argument("--out", help="report path (default backend/eval_runs/<time>-<provider>.json)")
    parser.add_argument("--resume", help="continue an earlier report, skipping the cases it has")
    parser.add_argument("--freeze", help="copy the app database to this path and exit")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="diff two reports and exit")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = parse_args(argv)
    if args.compare:
        lines, regressed = eval_report.compare(*(eval_report.load(Path(p)) for p in args.compare))
        log.info("\n".join(lines))
        raise SystemExit(1 if regressed else 0)
    if args.freeze:
        freeze(Path(args.freeze))
        raise SystemExit(0)
    raise SystemExit(run_live(args))


if __name__ == "__main__":
    main()
