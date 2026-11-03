"""All-play records and luck for a Yahoo league (pure; the weekly scores come from Yahoo's scoreboards).

All-play: every week, each team's score is compared with every other team's score that week,
as if it had played the whole league. A team that beats 7 of 11 others goes 7-4 for the week.
All-play expected wins = the sum over weeks of (teams beaten + half the ties) / (teams - 1).
Luck = actual head-to-head wins in the same weeks (a tie counts half) - all-play expected wins: positive means the schedule
handed a team more wins than its scores earned.
"""
from collections.abc import Mapping, Sequence


def all_play(scores: Mapping[int, Mapping[str, float | None]]) -> dict[str, dict]:
    """{team: {wins, losses, ties, expected_wins, weeks}} from {week: {team: points}}.

    A team with no score in a week (None or missing) sits that week out; a week needs 2+ scores.
    """
    out: dict[str, dict] = {}
    for week in sorted(scores):
        board = {team: points for team, points in scores[week].items() if points is not None}
        if len(board) < 2:
            continue
        others = len(board) - 1
        for team, points in board.items():
            beaten = sum(1 for other, p in board.items() if other != team and points > p)
            tied = sum(1 for other, p in board.items() if other != team and points == p)
            row = out.setdefault(team, {"wins": 0, "losses": 0, "ties": 0, "expected_wins": 0.0, "weeks": 0})
            row["wins"] += beaten
            row["ties"] += tied
            row["losses"] += others - beaten - tied
            row["expected_wins"] += (beaten + 0.5 * tied) / others
            row["weeks"] += 1
    for row in out.values():
        row["expected_wins"] = round(row["expected_wins"], 2)
    return out


def record_str(row: Mapping[str, int]) -> str:
    """'52-33', or '52-32-1' with ties."""
    text = f"{row['wins']}-{row['losses']}"
    return f"{text}-{row['ties']}" if row.get("ties") else text


def luck(actual_wins: float, expected_wins: float) -> float:
    """Actual wins minus all-play expected wins, one decimal."""
    return round(actual_wins - expected_wins, 1)


Matchup = Sequence[tuple[str, float | None]]          # [(team key, points), (team key, points)]


def week_points(boards: Mapping[int, Sequence[Matchup]]) -> dict[int, dict[str, float | None]]:
    """{week: {team: points}} from each week's matchups."""
    return {week: {team: points for matchup in matchups for team, points in matchup} for week, matchups in boards.items()}


def head_to_head_wins(boards: Mapping[int, Sequence[Matchup]]) -> dict[str, float]:
    """Real wins (a tie counts half) in the same weeks, from each head-to-head matchup with both scores."""
    wins: dict[str, float] = {}
    for matchups in boards.values():
        for matchup in matchups:
            if len(matchup) != 2 or any(points is None for _, points in matchup):
                continue
            (a, pa), (b, pb) = matchup
            wins[a] = wins.get(a, 0.0) + (1.0 if pa > pb else 0.5 if pa == pb else 0.0)
            wins[b] = wins.get(b, 0.0) + (1.0 if pb > pa else 0.5 if pa == pb else 0.0)
    return wins


def all_play_rows(boards: Mapping[int, Sequence[Matchup]]) -> dict[str, dict]:
    """{team: {all_play, all_play_expected_wins, luck, weeks}} for the power rankings.

    Actual wins are counted from the same weeks' matchups, so luck compares like with like even
    when a week's scoreboard is missing.
    """
    actual = head_to_head_wins(boards)
    rows = {}
    for team, row in all_play(week_points(boards)).items():
        rows[team] = {"all_play": record_str(row), "all_play_expected_wins": round(row["expected_wins"], 1),
                      "luck": luck(actual.get(team, 0.0), row["expected_wins"]), "weeks": row["weeks"]}
    return rows
