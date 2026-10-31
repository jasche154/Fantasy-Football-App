"""How much of the rest of the season an injured player is expected to lose, and how that recovers.

Measured on 2021-25 injury spells (services/injury_recovery_fit.py writes injury_recovery.json):

- **At the injury**, the discount comes from the body part: e.g. a concussion costs about 2.2
  games, an ankle or hamstring about 2.6, a knee 3.0 (fitted averages, shrunk toward 2.8).
- **While out**, the evidence moves it. How he practiced this week scales the games still to
  miss: a limited practice cuts them, a missed practice (or no report at all, mostly injured
  reserve) stretches them. Waiting alone doesn't predict a return in the data (a player who has
  missed several weeks is usually on a longer injury), but long injuries often go weeks without
  news, so the discount (measured against the season left when he got hurt) also shrinks by
  WEEKLY_RECOVERY per game missed. That's a product rule, not a fitted number. Injured reserve
  keeps its IR_MIN_GAMES minimum, counted from when his absence began (the placement week isn't
  stored, so a player Out for two weeks before IR gets a two-game floor).
- **Sleeper news** (services/injury_news.py) overrides the averages when it has something:
  "out for the season" means no rest-of-season value, and a reported timeline ("12-to-16 weeks")
  replaces the body-part average, counted down from the day it was published. Sleeper's
  "Surgery" note without a timeline sets at least SURGERY_GAMES (a product default, not fitted:
  surgeries are too rare in the injury reports to measure).
- **Out for the season** (a torn ACL or Achilles, or the news says so): no rest-of-season value.
- **After he returns**, players score less for a few games (fitted: the first game back is about
  87% of his rate, the next two about 93% and 95%, relative to healthy players) and are re-injured more
  often; both count as "rust" in games, then he's back to normal.
- **Role on return**: a returning RB whose fill-in scored at least FILL_IN_RATIO of his rate over
  2+ games is flagged with the fill-in's name, and loses the fitted rb_role_loss share of his
  rate. On 2021-25 that share is 0: starters got their snaps and scoring back even after a
  strong fill-in (37 RB cases), so the flag is a warning and the expert ROS ranks in the blend
  carry any real role change.

The shares feed services/valuation.py (availability), services/lineup_gain.py and the season
simulation, so the trade calculator, league trades and waivers agree.
"""
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import InjuryReport, PlayerStatus
from services import injury_news
from services.availability import team_weeks
from services.injuries import team_games
from services.projections import PlayerSeason
from services.ros_consensus import IR_STATUSES, SHORT_OUT, season_ending
from services.schedule_strength import Schedule

PARAMS_PATH = Path(__file__).with_name("injury_recovery.json")
WEEKLY_RECOVERY = 0.10        # the injury discount shrinks 10% per game missed (product rule, not fitted)
IR_MIN_GAMES = 4              # injured reserve: at least four games, counted from when his absence began
SURGERY_GAMES = 6             # Sleeper's "Surgery" note with no reported timeline (product default, not fitted)
FILL_IN_RATIO = 0.5           # a fill-in who scored half the starter's rate "played well"
MIN_FILL_IN_GAMES = 2
RELEVANT_PPG = 8.0            # role changes only for real starters
DEFAULT_PARAMS = {             # used only if injury_recovery.json is missing
    "games_missed": {"overall": 2.9}, "practice_factor": {"none": 1.0, "dnp": 1.0, "practicing": 1.0},
    "rust": [0.87, 0.93, 0.95], "reinjury_excess": 0.08, "rb_role_loss": 0.0,
}
GROUP_WORDS = (               # first match wins
    ("soft_tissue", ("tendinitis", "tendonitis", "soreness")),    # Achilles tendinitis isn't a tear
    ("acl_achilles", ("acl", "achilles")),
    ("non_injury", ("not injury", "personal", "illness", "rest")),
    ("concussion", ("concussion", "head")),
    ("hamstring", ("hamstring",)),
    ("ankle", ("ankle",)),
    ("knee", ("knee", "mcl", "meniscus")),
    ("foot", ("foot", "toe", "heel")),
    ("soft_tissue", ("groin", "quad", "calf", "thigh", "hip", "oblique", "abdom", "core")),
    ("upper_body", ("shoulder", "pectoral", "elbow", "wrist", "hand", "thumb", "finger", "forearm", "arm",
                    "collarbone", "clavicle")),
    ("back_ribs", ("back", "neck", "rib", "chest", "spine")),
)
GROUP_LABELS = {"soft_tissue": "Soft-tissue", "upper_body": "Upper-body", "back_ribs": "Back/ribs",
                "acl_achilles": "ACL/Achilles", "non_injury": "Non-injury"}


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def body_group(injury: str | None) -> str:
    text = (injury or "").lower()
    if not text or text == "undisclosed":
        return "unknown"
    return next((group for group, words in GROUP_WORDS if any(w in text for w in words)), "other")


def practice_key(practice: str | None) -> str:
    """'none' (no report), 'dnp', or 'practicing' (limited or full)."""
    value = (practice or "").upper()
    return "none" if not value else "dnp" if value == "DNP" else "practicing"


@lru_cache(maxsize=1)
def params() -> dict:
    try:
        return {**DEFAULT_PARAMS, **json.loads(PARAMS_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_PARAMS)


@dataclass(frozen=True)
class Recovery:
    status: str | None              # live tag (OUT, IR, ...) or None
    group: str
    practice: str | None            # this week's practice key, None when there's no report yet
    games_out: int                  # team games missed in a row, up to now
    games_back: int | None          # games played since returning from a missed stretch (None: no recent absence)
    season_ending: bool = False
    news_weeks: float | None = None  # games still out per the reported timelines (injury_news; byes removed)
    surgery: bool = False           # Sleeper's injury note says surgery
    from_news: bool = False         # season_ending came from the news

    @property
    def out_now(self) -> bool:
        return self.season_ending or (self.status or "") in IR_STATUSES | SHORT_OUT

    def evidence_missed(self, remaining: int) -> float:
        """Games he's expected to miss from now on (this week included), from the injury and this
        week's practice report alone (no credit for time passed)."""
        if self.season_ending:
            return float(remaining)
        if not self.out_now:
            return 0.0
        fitted = params()
        ir = self.status in IR_STATUSES
        if self.news_weeks is not None:
            # A reported timeline already counts time passed; a practice report is newer evidence.
            factor = fitted["practice_factor"].get(self.practice, 1.0) if self.practice in ("dnp", "practicing") else 1.0
            missed = 1 + max(0.0, self.news_weeks - 1) * factor
            return round(min(max(missed, IR_MIN_GAMES - self.games_out) if ir else missed, remaining), 2)
        onset = fitted["games_missed"].get(self.group, fitted["games_missed"]["overall"])
        if self.surgery:
            onset = max(onset, SURGERY_GAMES)
        factor = fitted["practice_factor"].get(self.practice or ("none" if ir else ""), 1.0)
        missed = 1 + max(0.0, onset - 1) * factor
        if ir:
            missed = max(missed, IR_MIN_GAMES - self.games_out)
        return round(min(missed, remaining), 2)

    def rust_games(self, remaining: int) -> float:
        """Points lost after he returns (slower first games, re-injury), in games."""
        if self.season_ending or not (self.out_now or self.games_out or self.games_back is not None):
            return 0.0
        rust = params()["rust"]
        start = self.games_back if self.games_back is not None and not self.games_out else 0
        if start >= len(rust):
            return 0.0
        room = max(0, int(remaining - self.evidence_missed(remaining)))
        terms = [1 - r for r in rust[start:]][:room]
        reinjury = params()["reinjury_excess"] * params()["games_missed"]["overall"] * len(terms) / len(rust)
        return round(sum(terms) + reinjury, 2)

    def discount(self, remaining: int) -> float:
        """Share of his remaining value lost to the injury.

        The evidence (games to miss plus the dip after his return) is measured against the season
        that was left when he got hurt, then shrinks WEEKLY_RECOVERY for each game he's missed, so
        with no news his value still creeps back up week by week. Injured reserve keeps its minimum.
        """
        if self.season_ending:
            return 1.0
        lost = self.evidence_missed(remaining) + self.rust_games(remaining)
        if self.news_weeks is not None:                  # the timeline already counts down
            discount = lost / max(remaining, 1)
        else:
            discount = lost / max(remaining + self.games_out, 1) * (1 - WEEKLY_RECOVERY) ** self.games_out
        if self.status in IR_STATUSES:
            discount = max(discount, (IR_MIN_GAMES - self.games_out) / max(remaining, 1))
        return round(min(1.0, max(0.0, discount)), 3)

    def share(self, remaining: int) -> float:
        """Share of the remaining games' value he keeps."""
        return round(1 - self.discount(remaining), 3)

    def expected_missed(self, remaining: int) -> float:
        """Games he's expected to miss from now on: the discount in games, less the post-return dip."""
        if self.season_ending:
            return float(remaining)
        if not self.out_now:
            return 0.0
        games = self.discount(remaining) * remaining - self.rust_games(remaining)
        floor = max(1.0, IR_MIN_GAMES - self.games_out) if self.status in IR_STATUSES else 1.0
        return round(min(float(remaining), max(floor, games)), 2)

    def note(self, remaining: int) -> str | None:
        """Plain words for the UI and Ask AI: whole games ("likely out 2 more games"), not decimals."""
        label = GROUP_LABELS.get(self.group, self.group.capitalize()) if self.group not in ("unknown", "other") else None
        if self.season_ending:
            return "Out for the season (per news)" if self.from_news else "Out for the season"
        if self.out_now:
            games = max(1, math.floor(self.expected_missed(remaining) + 0.5))
            practiced = {"practicing": ", practicing", "dnp": ", not practicing"}.get(self.practice or "", "")
            source = " (reported timeline)" if self.news_weeks is not None else " (surgery)" if self.surgery else ""
            subject = "Not injury-related" if self.group == "non_injury" else f"{label} injury" if label else "Injured"
            return f"{subject}: likely out {games} more game{'' if games == 1 else 's'}{source}{practiced}"
        if self.games_out:
            word = label if self.group == "acl_achilles" else (label or "").lower()
            injury = "an injury" if not label or self.group == "non_injury" else f"{_article(word)} {word} injury"
            return f"Returning from {injury}: first games back usually run a bit low"
        if self.games_back is not None and self.games_back < len(params()["rust"]):
            return f"{self.games_back} game{'' if self.games_back == 1 else 's'} back from injury"
        return None


def history(player: PlayerSeason, schedule: Schedule, before_week: int) -> tuple[int, int | None, tuple[int, ...]]:
    """(games missed in a row up to now, games played since the last missed stretch, that stretch's weeks).

    Only team games after his first game for this team count, so a trade or a late start isn't an injury.
    """
    if not player.team:
        return 0, None, ()
    mine = set(team_games(player, player.team, schedule))
    weeks = [w for w in team_weeks(schedule, player.team, before_week) if mine and w >= min(mine)]
    i = len(weeks) - 1
    while i >= 0 and weeks[i] not in mine:
        i -= 1
    if i < len(weeks) - 1:
        return len(weeks) - 1 - i, None, tuple(weeks[i + 1:])
    end = i
    while i >= 0 and weeks[i] in mine:
        i -= 1
    if i < 0:
        return 0, None, ()
    start = i
    while start >= 0 and weeks[start] not in mine:
        start -= 1
    return 0, end - i, tuple(weeks[start + 1:i + 1])


def _reports(session: Session, season: int) -> dict[int, dict[int, InjuryReport]]:
    out: dict[int, dict[int, InjuryReport]] = defaultdict(dict)
    for report in session.scalars(select(InjuryReport).where(InjuryReport.season == season)):
        out[report.player_id][report.week] = report
    return out


def _last_game(player: PlayerSeason, schedule: Schedule) -> datetime | None:
    """His last kickoff (news from before it is about an earlier injury); the game day if no time is listed."""
    if not player.games or not player.team:
        return None
    week = player.games[-1].week
    game = next((g for g in schedule.games if g.week == week and player.team in (g.home_team, g.away_team)), None)
    if game is None:
        return None
    if game.kickoff is not None:
        return game.kickoff
    try:
        return datetime.fromisoformat(game.gameday).replace(tzinfo=timezone.utc) if game.gameday else None
    except ValueError:
        return None


def news_games(weeks: float, team: str | None, schedule: Schedule, week: int) -> float:
    """Calendar weeks out (a reported timeline) as games: a bye inside the window isn't a missed game."""
    if not team:
        return weeks
    byes = sum(1 for w in range(week, week + int(weeks)) if schedule.opponent(team, w) is None)
    return max(0.0, weeks - byes)


def recovery_states(session: Session, seasons: Iterable[PlayerSeason], schedule: Schedule,
                    season: int, week: int, now: datetime | None = None,
                    ) -> tuple[dict[int, Recovery], dict[int, tuple[int, ...]]]:
    """({player: Recovery} for players hurt now or back lately, {player: weeks of his last absence}).

    week is the upcoming week. A stretch of missed games without a live tag or an injury report in
    it (a healthy scratch) doesn't count. News evidence only applies while he's out.
    """
    now = now or datetime.now(timezone.utc)
    statuses = {s.player_id: s for s in session.scalars(select(PlayerStatus).where(PlayerStatus.injury_status.is_not(None)))}
    reports = _reports(session, season)
    news = injury_news.load(session)
    states, absences = {}, {}
    for player in seasons:
        games_out, games_back, absent = history(player, schedule, week)
        status = statuses.get(player.player_id)
        mine = reports.get(player.player_id, {})
        reported = any(w in mine for w in absent)
        if status is None and not reported:
            continue
        current = mine.get(week)
        # Sleeper splits "Achilles" (body part) from "Tendinitis" (note); both decide the group.
        sleeper_text = " ".join(t for t in ((status.injury_body_part, status.injury_notes) if status else ()) if t)
        injury = sleeper_text or next((mine[w].injury for w in sorted(mine, reverse=True) if mine[w].injury), None)
        tag = (status.injury_status or "").upper() if status else None
        out_now = (tag or "") in IR_STATUSES | SHORT_OUT
        evidence = (injury_news.evidence(news.get(player.player_id, ()), _last_game(player, schedule), now)
                    if out_now else None)
        tagged_ending = bool(status) and season_ending(tag or "", status.injury_body_part)
        news_ending = bool(evidence and evidence.season_ending)
        states[player.player_id] = Recovery(
            status=tag, group=body_group(injury), practice=practice_key(current.practice_status) if current else None,
            games_out=games_out, games_back=games_back,
            season_ending=tagged_ending or news_ending,
            news_weeks=(news_games(evidence.weeks_left, player.team, schedule, week)
                        if evidence and not news_ending and evidence.weeks_left is not None else None),
            surgery=out_now and "surgery" in ((status.injury_notes if status else None) or "").lower(),
            from_news=news_ending and not tagged_ending)
        absences[player.player_id] = absent
    return states, absences


QB_START_YARDS = 100           # a game with 100+ passing yards counts as a start
MIN_STARTS = 2


def season_starters(seasons: Iterable[PlayerSeason], states: Mapping[int, "Recovery"] | None = None) -> dict[str, int]:
    """{team: the QB with the most starts this season}, so an injured starter isn't read as a backup.

    With states, only starters out now (IR or Out) count: a veteran benched mid-season who picks up
    a Questionable tag stays the backup."""
    best: dict[str, tuple[int, int]] = {}
    for p in seasons:
        if p.position != "QB" or not p.team:
            continue
        starts = sum(1 for g in p.games if g.passing_yards >= QB_START_YARDS)
        if starts >= MIN_STARTS and starts > best.get(p.team, (0, 0))[0]:
            best[p.team] = (starts, p.player_id)
    return {team: pid for team, (_, pid) in best.items()
            if states is None or (pid in states and states[pid].out_now)}


def shares(states: Mapping[int, Recovery], remaining: int) -> dict[int, float]:
    return {pid: state.share(remaining) for pid, state in states.items()}


def rb_role_cuts(seasons: Iterable[PlayerSeason], states: Mapping[int, Recovery],
                 absences: Mapping[int, tuple[int, ...]]) -> dict[int, tuple[str, float]]:
    """{returning RB: (fill-in's name, share of his rate to take off)} when the fill-in played well.

    The share is the fitted rb_role_loss, which can be 0 (then it's a flag only)."""
    loss = params()["rb_role_loss"]
    by_team: dict[str, list[PlayerSeason]] = defaultdict(list)
    for p in seasons:
        if p.position == "RB" and p.team:
            by_team[p.team].append(p)
    out = {}
    for team, backs in by_team.items():
        for star in backs:
            state, absent = states.get(star.player_id), set(absences.get(star.player_id, ()))
            if not state or state.season_ending or len(absent) < MIN_FILL_IN_GAMES or star.ppg < RELEVANT_PPG:
                continue
            if not (state.games_out or (state.games_back or 0) < len(params()["rust"])):
                continue
            best = None
            for mate in backs:
                games = [g.fantasy_points for g in mate.games if g.week in absent]
                if mate is not star and len(games) >= MIN_FILL_IN_GAMES and mean(games) >= FILL_IN_RATIO * star.ppg:
                    if best is None or mean(games) > best[1]:
                        best = (mate.name, mean(games))
            if best:
                out[star.player_id] = (best[0], loss)
    return out
