"""Replay League advanced stat line (AdvLine, documented in services/replay_views_players.py): rates over a span's
skill games, from their box lines (`SeasonData.box`) and usage (`SeasonData.usage`). Ratios use the span's totals;
every number is null when its denominator is 0 or its data is missing.
"""
from collections.abc import Sequence
from statistics import mean

from services import replay_box
from services.replay_box import BoxLine
from services.replay_pool import Usage

KEYS = ("snap_pct", "target_share", "targets_pg", "carries_pg", "touches_pg", "ypc", "ypr", "ypt", "catch_rate",
        "total_td", "td_rate", "pass_ypg", "td_int", "cmp_pct")


def _ratio(part: float, whole: float, scale: float = 1) -> float | None:
    return round(part / whole * scale, 1) if whole else None


def line(boxes: Sequence[BoxLine], usages: Sequence[Usage], per_game_td: bool = False) -> dict | None:
    """The AdvLine of the span's games (`boxes`, one per game; `usages`, the ones loaded); None without games.
    `per_game_td`: total_td per game ("last4") instead of the span's total."""
    if not boxes:
        return None
    games, t = len(boxes), replay_box.total(boxes)
    snaps = [u.snap_pct for u in usages if u.snap_pct is not None]
    shares = [u.target_share for u in usages if u.target_share is not None]
    touches, tds = t.carries + t.rec, t.rush_td + t.rec_td + t.pass_td
    passed = t.pass_yds or t.pass_td or t.pass_int
    return {"snap_pct": round(mean(snaps), 1) if snaps else None,
            "target_share": round(mean(shares) * 100, 1) if shares else None,
            "targets_pg": _ratio(t.targets, games), "carries_pg": _ratio(t.carries, games),
            "touches_pg": _ratio(touches, games), "ypc": _ratio(t.rush_yds, t.carries),
            "ypr": _ratio(t.rec_yds, t.rec), "ypt": _ratio(t.rec_yds, t.targets),
            "catch_rate": _ratio(t.rec, t.targets, 100),
            "total_td": _ratio(tds, games) if per_game_td else tds,
            "td_rate": _ratio(t.rush_td + t.rec_td, touches, 100),
            "pass_ypg": _ratio(t.pass_yds, games) if passed else None,
            "td_int": round(t.pass_td / t.pass_int, 2) if t.pass_int else (t.pass_td if t.pass_td > 0 else None),
            "cmp_pct": _ratio(t.pass_cmp, t.pass_att, 100)}
