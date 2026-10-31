"""The Ask AI evaluation's JSON report: run metadata, per-case results, summary and before/after diff.

Pure functions plus small file helpers; services/agent_eval.py runs the cases. The report never
holds API keys or error messages (only exception types), but the answer text in it can name your
leagues, teams and league-mates: share a report made with --redact (or only the --compare output).
"""
import hashlib
import inspect
import json
import os
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA = 1
BACKEND = Path(__file__).resolve().parents[1]


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def git_state() -> dict[str, Any]:
    """Commit and whether the working tree has uncommitted changes (None when git isn't available)."""
    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=BACKEND, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None
    status = run("status", "--porcelain")
    return {"sha": run("rev-parse", "--short=12", "HEAD"), "dirty": bool(status) if status is not None else None}


def code_hashes() -> dict[str, str]:
    """Fingerprints of what shapes an answer: the prompt, the tool schemas and the routing table."""
    from services import agent_prompt, agent_routing
    from tools.definitions import TOOLS

    prompt = agent_prompt.SYSTEM_PROMPT + json.dumps(agent_prompt.TEMPLATES, sort_keys=True)
    return {"prompt": short_hash(prompt),
            "tools": short_hash(json.dumps(TOOLS.openai_schemas(), sort_keys=True)),
            "routing": short_hash(inspect.getsource(agent_routing))}


def _rate(values: Sequence[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pass rates overall, per split, per intent and per check; cost per case; pass^k over repeats."""
    by_split: dict[str, list[bool]] = defaultdict(list)
    by_intent: dict[str, list[bool]] = defaultdict(list)
    by_check: dict[str, list[bool]] = defaultdict(list)
    by_case: dict[str, list[bool]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    for r in results:
        by_split[r["split"]].append(r["passed"])
        by_intent[r["intent"]].append(r["passed"])
        by_case[r["id"]].append(r["passed"])
        for name, value in r["checks"].items():
            if value is not None:
                by_check[name].append(value)
        if r.get("error"):
            errors[r["error"]] += 1
    return {
        "cases": len(by_case), "runs": len(results), "pass_rate": _rate([r["passed"] for r in results]),
        "pass_all_repeats": _rate([all(v) for v in by_case.values()]),
        "splits": {k: _rate(v) for k, v in sorted(by_split.items())},
        "intents": {k: _rate(v) for k, v in sorted(by_intent.items())},
        "checks": {k: _rate(v) for k, v in sorted(by_check.items())},
        "mean_rounds": _mean([r["rounds"] for r in results]),
        "mean_tokens": _mean([r["tokens"]["prompt"] + r["tokens"]["completion"] for r in results]),
        "mean_seconds": _mean([r["seconds"] for r in results]),
        "unverified_rate": _rate([bool(r["unverified"]) for r in results]),
        "errors": dict(errors),
    }


def case_status(results: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    """Case id -> passed on every repeat."""
    status: dict[str, bool] = {}
    for r in results:
        status[r["id"]] = status.get(r["id"], True) and r["passed"]
    return status


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[list[str], bool]:
    """Readable lines (summary deltas, then cases that flipped, worst first) and whether holdout regressed."""
    b, a = before["summary"], after["summary"]

    def delta(label: str, old: float | None, new: float | None) -> str:
        if old is None or new is None:
            return f"{label}: {old} -> {new}"
        return f"{label}: {old} -> {new} ({new - old:+.3f})"
    lines = [f"before {before['meta'].get('git')} {before['meta'].get('provider')} | "
             f"after {after['meta'].get('git')} {after['meta'].get('provider')}",
             delta("pass rate", b["pass_rate"], a["pass_rate"])]
    lines += [delta(f"split {k}", b["splits"].get(k), a["splits"].get(k)) for k in sorted(set(b["splits"]) | set(a["splits"]))]
    lines += [delta(f"check {k}", b["checks"].get(k), a["checks"].get(k)) for k in sorted(set(b["checks"]) | set(a["checks"]))]
    lines += [delta("mean tokens", b["mean_tokens"], a["mean_tokens"]), delta("mean rounds", b["mean_rounds"], a["mean_rounds"])]
    old, new = case_status(before["cases"]), case_status(after["cases"])
    lines += [f"FAIL now  {k}" for k in sorted(k for k in old if k in new and old[k] and not new[k])]
    lines += [f"PASS now  {k}" for k in sorted(k for k in old if k in new and not old[k] and new[k])]
    old_holdout, new_holdout = b["splits"].get("holdout"), a["splits"].get("holdout")
    regressed = old_holdout is not None and new_holdout is not None and new_holdout < old_holdout
    return lines, regressed


def load(path: Path) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("schema") != SCHEMA:
        raise ValueError(f"{path} is not an eval report (schema {report.get('schema')!r})")
    return report


def save(path: Path, meta: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> None:
    """Writes the whole report atomically, so a stopped run can be resumed from the file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": SCHEMA, "meta": dict(meta), "summary": summarize(results), "cases": list(results)}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
    os.replace(temp, path)
