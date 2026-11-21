"""Replay League expert ranks: FantasyPros consensus position ranks from the nflverse archive, point in time.

`download` pulls the archive (nflreadpy, imported there only); `expert_rows` matches it to our players
(pure); `week_ranks` picks, for each week, the latest snapshot taken before that week's first Sunday kickoff
(pure); `load_expert_ranks` stores rows in `replay_expert_ranks`. Only the last preseason cheatsheet and
every in-season ROS snapshot are kept. A failed download is reported, never blocks a season.
See .plans/pr1-plan.md section B.
"""
from collections.abc import Mapping
from datetime import date

from sqlalchemy.orm import Session

from integrations.adp_history import SeasonMatcher
from services.replay_pool import PlayerWeek

FIRST_SEASON = 2020
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
PAGE_PATTERN = r"(ros-)?(ppr-)?(qb|rb|wr|te|k|dst)(-cheatsheets)?(\.php)?$"   # after stripping "/nfl/rankings/"


def download() -> list[dict]:
    """The archive's "rp" rows for the six position pages."""
    raise NotImplementedError


def expert_rows(season: int, raw: list[dict], matcher: SeasonMatcher, fp_to_gsis: dict[str, str],
                week1_kickoff: date, last_game: date) -> list[dict]:
    """Rows for `replay_expert_ranks`, matched by FantasyPros id, then name (matcher.by_name), then team
    defense (matcher.defense)."""
    raise NotImplementedError


def week_ranks(rows: list[dict], kickoffs: Mapping[int, date]) -> tuple[dict[PlayerWeek, int], dict[int, int]]:
    """({(player_id, week): position rank}, {week: snapshot age in days}). Week 0 = preseason."""
    raise NotImplementedError


def load_expert_ranks(session: Session, season: int, force: bool = False) -> dict:
    """Downloads and stores a season's ranks; {"rows": n, ...}. Before FIRST_SEASON: skipped."""
    raise NotImplementedError
