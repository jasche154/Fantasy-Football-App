"""The scripts work however they're launched: `python -m ...` from backend/, by file path, or
from another folder (an IDE "Run" button, the repo root). Each runs in a subprocess."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent


def run(args: list[str], cwd: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "FANTASY_ENV_FILE": "", "DATABASE_URL": f"sqlite:///{(tmp_path / 'e.db').as_posix()}"}
    env.pop("GROQ_API_KEY", None)
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("cwd", [REPO_ROOT, BACKEND / "database"], ids=["repo-root", "database-dir"])
def test_create_tables_runs_by_file_path(tmp_path, cwd):
    result = run([str(BACKEND / "database" / "create_tables.py")], cwd, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Tables created." in result.stdout


def test_populate_stats_runs_by_file_path(tmp_path):
    result = run([str(BACKEND / "database" / "populate_stats.py"), "--help"], REPO_ROOT, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout


def test_alembic_runs_from_the_repo_root(tmp_path):
    result = run(["-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"], REPO_ROOT, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "No module named" not in result.stderr


def test_populate_stats_progress_survives_the_schema_upgrade(caplog, engine, monkeypatch):
    """Running migrations in-process must not silence populate_stats' progress logging."""
    import logging

    from database import populate_stats

    monkeypatch.setattr(populate_stats, "engine", engine)
    monkeypatch.setattr(populate_stats, "FEEDS", [])
    for step in ("sync_crosswalk", "load_snap_counts", "load_expert_rankings", "apply_latest_teams"):
        monkeypatch.setattr(populate_stats, step, lambda *args, **kwargs: None)
    with caplog.at_level(logging.INFO, logger="populate_stats"):
        populate_stats.main(["--seasons", "2026"])
    assert "Loading seasons: [2026]" in caplog.text and "Done." in caplog.text

