"""Player search, name resolution, and the player profile."""
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Player, PlayerPassingAdvancedWeekly, PlayerReceivingAdvancedWeekly, PlayerRushingAdvancedWeekly,
    PlayerStatsWeekly,
)
from services.insights import player_signals
from services.labels import label
from services.league import POSITIONS, LeagueSettings
from services.player_index import TYPO_CUTOFF, get_index, normalize
from services.projections import GameLine, PlayerSeason, load_game_lines
from services.valuation import build_valuation

FUZZY_CUTOFF = 0.75
ADVANCED_MODELS = {
    "rushing": (PlayerRushingAdvancedWeekly, (
        "rush_attempts", "rush_yards", "avg_rush_yards", "rush_touchdowns", "expected_rush_yards",
        "rush_yards_over_expected", "rush_yards_over_expected_per_att", "rush_pct_over_expected",
        "efficiency", "avg_time_to_los", "percent_attempts_gte_eight_defenders")),
    "receiving": (PlayerReceivingAdvancedWeekly, (
        "targets", "receptions", "yards", "rec_touchdowns", "catch_percentage", "avg_separation",
        "avg_cushion", "avg_intended_air_yards", "percent_share_of_intended_air_yards", "avg_yac",
        "avg_expected_yac", "avg_yac_above_expectation")),
    "passing": (PlayerPassingAdvancedWeekly, (
        "attempts", "completions", "pass_yards", "pass_touchdowns", "interceptions", "passer_rating",
        "completion_percentage", "expected_completion_percentage",
        "completion_percentage_above_expectation", "avg_time_to_throw", "aggressiveness",
        "avg_intended_air_yards", "avg_completed_air_yards", "avg_air_yards_to_sticks")),
}


@dataclass(frozen=True)
class PlayerRef:
    id: int
    name: str
    position: str | None
    team: str | None


class PlayerNotFound(LookupError):
    pass


class AmbiguousPlayer(LookupError):
    def __init__(self, query: str, candidates: list[PlayerRef]):
        super().__init__(f"'{label(query)}' matches several players: "
                         + ", ".join(f"{c.name} ({c.position}, {c.team}, id {c.id})" for c in candidates))
        self.candidates = candidates


def _refs(rows) -> list[PlayerRef]:
    return [PlayerRef(id=r.id, name=r.full_name, position=r.position, team=r.nfl_team) for r in rows]


def _latest_seasons(session: Session, player_ids: list[int]) -> dict[int, int]:
    rows = session.execute(select(PlayerStatsWeekly.player_id, func.max(PlayerStatsWeekly.season))
                           .where(PlayerStatsWeekly.player_id.in_(player_ids))
                           .group_by(PlayerStatsWeekly.player_id))
    return dict(rows.all())


def search_players(session: Session, query: str, position: str | None = None,
                   limit: int = 10) -> list[PlayerRef]:
    """Case-insensitive name search over fantasy positions, best match first.

    Exact name matches come first; then players who played most recently (so "chase"
    finds Ja'Marr Chase before retired Chase Edmonds); then prefix before substring
    matches. Falls back to fuzzy matching for typos.
    """
    text = query.strip().lower()
    if not text:
        return []
    positions = [position] if position else list(POSITIONS)
    base = select(Player.id, Player.full_name, Player.position, Player.nfl_team).where(
        Player.position.in_(positions))
    name_matches = func.lower(Player.full_name).contains(text, autoescape=True)   # "%" / "_" are literal
    candidates = _refs(session.execute(base.where(name_matches)))
    if not candidates:      # nicknames, last names, "AJ Brown" for "A.J. Brown"
        indexed = get_index(session).resolve(query)
        candidates = _refs(session.execute(base.where(Player.full_name.in_(indexed)))) if indexed else []
    if not candidates:
        names = {r.full_name: r for r in session.execute(base)}
        close = get_close_matches(query.strip(), list(names), n=limit, cutoff=FUZZY_CUTOFF)
        candidates = _refs(names[name] for name in close)
    recency = _latest_seasons(session, [c.id for c in candidates])

    def rank(ref: PlayerRef) -> tuple:
        name = ref.name.lower()
        prefix_tier = 0 if name.startswith(text) else 1
        return name != text, -recency.get(ref.id, 0), prefix_tier, ref.name

    return sorted(candidates, key=rank)[:limit]


def resolve_player(session: Session, reference: str | int) -> PlayerRef:
    """Turn an id or a (possibly misspelled) name into exactly one player."""
    if isinstance(reference, int) or str(reference).isdigit():
        player = session.get(Player, int(reference))
        if player is None:
            raise PlayerNotFound(f"No player with id {reference}.")
        return PlayerRef(player.id, player.full_name, player.position, player.nfl_team)
    text = str(reference).strip()
    indexed = get_index(session).resolve(text)     # exact, nickname, unique active last name, typo
    if indexed and not _whole_words(text, indexed) and any(
            _whole_words(text, (m.name,)) for m in search_players(session, text, limit=5)):
        indexed = ()      # a typo match never beats a player whose name really contains these words
    if indexed:
        refs = _refs(session.execute(select(Player.id, Player.full_name, Player.position, Player.nfl_team)
                                     .where(Player.full_name.in_(indexed), Player.position.in_(POSITIONS))
                                     .order_by(Player.id)))
        if len(refs) == 1:
            return refs[0]
        if refs:
            raise AmbiguousPlayer(text, refs)
    matches = search_players(session, text, limit=5)
    if not matches:
        raise PlayerNotFound(f"No QB/RB/WR/TE found matching '{label(text)}'.")
    exact = [m for m in matches if normalize(m.name) == normalize(text)]
    if len(exact) == 1:
        return exact[0]
    words = [m for m in matches if _whole_words(text, (m.name,))]
    if len(words) == 1 and not exact:
        return words[0]
    if len(matches) == 1 and not exact:
        if _close_enough(text, matches[0].name):
            return matches[0]
        # A lone partial hit ("Gibs" inside "Antonio Gibson") is a guess: ask instead of answering.
        only = matches[0]
        raise PlayerNotFound(f"No player named '{label(text)}'. Closest: {only.name} ({only.position}, {only.team}, "
                             f"id {only.id}). Pass that full name or id if that's who you mean.")
    raise AmbiguousPlayer(text, exact or matches)


def _whole_words(query: str, names: Iterable[str]) -> bool:
    """Every word of the query is a whole word of one of the names ("gibb" in "Rookie Gibb")."""
    wanted = normalize(query).split()
    return bool(wanted) and any(all(w in normalize(n).split() for w in wanted) for n in names)


def _close_enough(query: str, name: str) -> bool:
    """A unique hit we can trust: a prefix, whole words of the name, or a near-exact spelling."""
    wanted, full = normalize(query), normalize(name)
    if not wanted:
        return False
    words = full.split()
    if full.startswith(wanted) or all(w in words for w in wanted.split()):
        return True
    return SequenceMatcher(None, wanted, full).ratio() >= TYPO_CUTOFF


def _game_log_row(line: GameLine) -> dict:
    return {
        "week": line.week, "season_type": line.season_type, "opponent": line.opponent,
        "fantasy_points": line.fantasy_points, "expected_points": line.expected_points,
        "passing_yards": line.passing_yards, "passing_tds": line.passing_tds,
        "interceptions": line.interceptions, "carries": line.carries,
        "rushing_yards": line.rushing_yards, "rushing_tds": line.rushing_tds,
        "targets": line.targets, "receptions": line.receptions,
        "receiving_yards": line.receiving_yards, "receiving_tds": line.receiving_tds,
        "snap_pct": line.snap_pct,
    }


def _summary(season: PlayerSeason) -> dict:
    games = season.games
    shares = [g.target_share for g in games if g.target_share is not None]
    snaps = [g.snap_pct for g in games if g.snap_pct is not None]
    expected = season.expected_ppg
    return {
        "games": season.games_played, "fantasy_points": season.total_points, "ppg": season.ppg,
        "expected_ppg": expected,
        "fpoe_per_game": season.fpoe_per_game,
        "targets": sum(g.targets for g in games), "receptions": sum(g.receptions for g in games),
        "carries": sum(g.carries for g in games), "passing_yards": sum(g.passing_yards for g in games),
        "rushing_yards": sum(g.rushing_yards for g in games),
        "receiving_yards": sum(g.receiving_yards for g in games),
        "total_tds": sum(g.total_tds for g in games),
        "target_share": round(mean(shares), 3) if shares else None,
        "snap_pct": round(mean(snaps), 1) if snaps else None,
    }


def _advanced(session: Session, player_id: int, season: int) -> dict:
    """NGS season-total (week 0) rows as published by nflverse, one per stat type."""
    result = {}
    for key, (model, columns) in ADVANCED_MODELS.items():
        row = session.execute(select(*(getattr(model, c) for c in columns)).where(
            model.player_id == player_id, model.season == season, model.week == 0,
            model.season_type == "REG")).first()
        result[key] = ({c: (round(v, 3) if isinstance(v, float) else v) for c, v in row._mapping.items()}
                       if row else None)
    return result


def player_profile(session: Session, player_id: int, league: LeagueSettings,
                   season: int | None = None) -> dict:
    player = session.get(Player, player_id)
    if player is None:
        raise PlayerNotFound(f"No player with id {player_id}.")
    valuation = build_valuation(session, league, season)
    season = valuation.season
    scoring = league.scoring_settings()
    loaded = load_game_lines(session, season, scoring, [player_id], regular_season_only=False,
                             positions=[player.position] if player.position else POSITIONS)
    all_games = loaded.get(player_id, (None, []))[1]
    regular = tuple(g for g in all_games if g.season_type == "REG")
    player_season = PlayerSeason(player.id, player.full_name, player.position, player.nfl_team, season, regular)
    value = valuation.by_id.get(player_id)
    return {
        "player": {"id": player.id, "name": player.full_name, "position": player.position,
                   "team": player.nfl_team, "gsis_id": player.gsis_id},
        "season": season, "scoring": league.scoring,
        "summary": _summary(player_season),
        "projection": ({"ros_ppg": value.ros_ppg, "vorp_per_game": value.vorp_per_game,
                        "trade_value": value.trade_value, "pos_rank": value.pos_rank} if value else None),
        "advanced": _advanced(session, player_id, season),
        "game_log": [_game_log_row(g) for g in all_games],
        "signals": player_signals(player_season, valuation),
    }
