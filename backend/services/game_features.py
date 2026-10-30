"""Game conditions, team profiles and red-zone roles, as of a given week (no look-ahead).

Used by the weekly projection adjustments (services/projection_factors.py), the kicker and
defense models, and the backtest. Everything for week W is built from games before W:
- Team profiles from team_game_stats: EPA per play allowed (pass, rush), sack and takeaway
  rates (made and allowed), red-zone trips and TD rate, 4th-down aggressiveness, plays and
  field goals per game. Each is shrunk toward the league average with PRIOR_GAMES of
  league-average evidence, so two games can't produce an extreme team.
- Red-zone roles from player_red_zone: a player's carries + targets inside the 10 and the
  20 per game played.
- Conditions from the schedule: roof, temperature and wind (historical games), or a
  forecast for upcoming outdoor games (services/weather.py).
"""
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import NflGame, PlayerRedZone, TeamGameStats

PRIOR_GAMES = 4                 # league-average games mixed into every team rate
INDOORS = {"dome", "closed"}


@dataclass(frozen=True)
class Conditions:
    indoors: bool
    temp: float | None          # deg F; None when unknown (indoors, or no forecast yet)
    wind: float | None          # mph

    @property
    def wind_over(self) -> float:
        """Wind above 10 mph outdoors (what actually hurts passing and kicking)."""
        return 0.0 if self.indoors or self.wind is None else max(0.0, self.wind - 10.0)

    @property
    def cold(self) -> float:
        """Degrees below 40 F outdoors."""
        return 0.0 if self.indoors or self.temp is None else max(0.0, 40.0 - self.temp)


UNKNOWN = Conditions(indoors=False, temp=None, wind=None)


def conditions_for(game: NflGame | None, forecast: Mapping[str, Conditions] | None = None) -> Conditions:
    """Recorded conditions for a played game; the forecast (by game_id) for an upcoming one."""
    if game is None:
        return UNKNOWN
    indoors = (game.roof or "").lower() in INDOORS
    if indoors:
        return Conditions(True, None, None)
    if game.wind is not None or game.temp is not None:
        return Conditions(False, game.temp, game.wind)
    return (forecast or {}).get(game.game_id, UNKNOWN)


@dataclass
class TeamProfile:
    games: int = 0
    # offense
    plays_pg: float = 0.0
    dropbacks_pg: float = 0.0
    pass_epa_per_db: float = 0.0
    rush_epa_per_play: float = 0.0
    sacks_taken_per_db: float = 0.0
    giveaways_pg: float = 0.0
    rz_trips_pg: float = 0.0
    rz_td_rate: float = 0.0
    fourth_go_rate: float = 0.0
    fga_pg: float = 0.0
    points_pg: float = 0.0
    # defense (what it allowed / made)
    pass_epa_allowed: float = 0.0
    rush_epa_allowed: float = 0.0
    sack_rate: float = 0.0           # sacks made per opponent dropback
    takeaways_pg: float = 0.0
    def_tds_pg: float = 0.0
    rz_td_allowed: float = 0.0       # opponents' red-zone TD rate (1 - stop rate)
    points_allowed_pg: float = 0.0


@dataclass
class TeamProfiles:
    teams: dict[str, TeamProfile] = field(default_factory=dict)
    league: TeamProfile = field(default_factory=TeamProfile)

    def get(self, team: str | None) -> TeamProfile:
        return self.teams.get(team or "", self.league)


def _ratio(num: float, den: float, prior: float, prior_den: float) -> float:
    return (num + prior * prior_den) / (den + prior_den) if den + prior_den else prior


def team_profiles(session: Session, season: int, before_week: int) -> TeamProfiles:
    rows = list(session.scalars(select(TeamGameStats).where(
        TeamGameStats.season == season, TeamGameStats.week < before_week)))
    return build_profiles(rows)


def build_profiles(rows: Iterable[TeamGameStats]) -> TeamProfiles:
    rows = list(rows)
    offense: dict[str, list[TeamGameStats]] = defaultdict(list)
    against: dict[str, list[TeamGameStats]] = defaultdict(list)    # rows of offenses facing this defense
    for r in rows:
        offense[r.team].append(r)
        against[r.opponent].append(r)
    league = _league(rows)
    profiles = TeamProfiles(league=league)
    for team, own in offense.items():
        opp = against.get(team, [])
        n, k = len(own), PRIOR_GAMES
        dbs, opp_dbs = sum(r.dropbacks for r in own), sum(r.dropbacks for r in opp)
        rush_plays = sum(r.plays - r.dropbacks for r in own)
        opp_rush = sum(r.plays - r.dropbacks for r in opp)
        trips, opp_trips = sum(r.rz_trips for r in own), sum(r.rz_trips for r in opp)
        fourth = sum(r.fourth_downs for r in own)
        per_game = lambda total, avg: (total + avg * k) / (n + k)  # noqa: E731
        profiles.teams[team] = TeamProfile(
            games=n,
            plays_pg=per_game(sum(r.plays for r in own), league.plays_pg),
            dropbacks_pg=per_game(dbs, league.dropbacks_pg),
            pass_epa_per_db=_ratio(sum(r.pass_epa for r in own), dbs, league.pass_epa_per_db, k * league.dropbacks_pg),
            rush_epa_per_play=_ratio(sum(r.rush_epa for r in own), rush_plays, league.rush_epa_per_play,
                                     k * (league.plays_pg - league.dropbacks_pg)),
            sacks_taken_per_db=_ratio(sum(r.sacks_taken for r in own), dbs, league.sacks_taken_per_db, k * league.dropbacks_pg),
            giveaways_pg=per_game(sum(r.ints_thrown + r.fumbles_lost for r in own), league.giveaways_pg),
            rz_trips_pg=per_game(trips, league.rz_trips_pg),
            rz_td_rate=_ratio(sum(r.rz_tds for r in own), trips, league.rz_td_rate, k * league.rz_trips_pg),
            fourth_go_rate=_ratio(sum(r.fourth_down_goes for r in own), fourth, league.fourth_go_rate, k * 4.0),
            fga_pg=per_game(sum(r.fg_att for r in own), league.fga_pg),
            points_pg=per_game(sum(r.points_for or 0 for r in own), league.points_pg),
            pass_epa_allowed=_ratio(sum(r.pass_epa for r in opp), opp_dbs, league.pass_epa_per_db, k * league.dropbacks_pg),
            rush_epa_allowed=_ratio(sum(r.rush_epa for r in opp), opp_rush, league.rush_epa_per_play,
                                    k * (league.plays_pg - league.dropbacks_pg)),
            sack_rate=_ratio(sum(r.def_sacks for r in own), opp_dbs, league.sack_rate, k * league.dropbacks_pg),
            takeaways_pg=per_game(sum(r.def_ints + r.def_fumble_rec for r in own), league.takeaways_pg),
            def_tds_pg=per_game(sum(r.def_tds for r in own), league.def_tds_pg),
            rz_td_allowed=_ratio(sum(r.rz_tds for r in opp), opp_trips, league.rz_td_rate, k * league.rz_trips_pg),
            points_allowed_pg=per_game(sum(r.points_against or 0 for r in own), league.points_pg),
        )
    return profiles


# Long-run NFL averages, used before any games are played in a season.
LEAGUE_DEFAULT = TeamProfile(games=0, plays_pg=61.0, dropbacks_pg=37.0, pass_epa_per_db=0.03, rush_epa_per_play=-0.06,
                             sacks_taken_per_db=0.065, giveaways_pg=1.2, rz_trips_pg=3.3, rz_td_rate=0.57,
                             fourth_go_rate=0.22, fga_pg=1.9, points_pg=22.5, pass_epa_allowed=0.03,
                             rush_epa_allowed=-0.06, sack_rate=0.065, takeaways_pg=1.2, def_tds_pg=0.12,
                             rz_td_allowed=0.57, points_allowed_pg=22.5)


def _league(rows: list[TeamGameStats]) -> TeamProfile:
    if len(rows) < 32:
        return LEAGUE_DEFAULT
    n = len(rows)
    dbs = sum(r.dropbacks for r in rows) or 1
    rush = sum(r.plays - r.dropbacks for r in rows) or 1
    trips = sum(r.rz_trips for r in rows) or 1
    fourth = sum(r.fourth_downs for r in rows) or 1
    takeaways = sum(r.def_ints + r.def_fumble_rec for r in rows) / n
    return TeamProfile(
        games=n, plays_pg=sum(r.plays for r in rows) / n, dropbacks_pg=dbs / n,
        pass_epa_per_db=sum(r.pass_epa for r in rows) / dbs, rush_epa_per_play=sum(r.rush_epa for r in rows) / rush,
        sacks_taken_per_db=sum(r.sacks_taken for r in rows) / dbs,
        giveaways_pg=sum(r.ints_thrown + r.fumbles_lost for r in rows) / n,
        rz_trips_pg=trips / n, rz_td_rate=sum(r.rz_tds for r in rows) / trips,
        fourth_go_rate=sum(r.fourth_down_goes for r in rows) / fourth, fga_pg=sum(r.fg_att for r in rows) / n,
        points_pg=sum(r.points_for or 0 for r in rows) / n,
        pass_epa_allowed=sum(r.pass_epa for r in rows) / dbs, rush_epa_allowed=sum(r.rush_epa for r in rows) / rush,
        sack_rate=sum(r.def_sacks for r in rows) / dbs, takeaways_pg=takeaways,
        def_tds_pg=sum(r.def_tds for r in rows) / n, rz_td_allowed=sum(r.rz_tds for r in rows) / trips,
        points_allowed_pg=sum(r.points_against or 0 for r in rows) / n)


def red_zone_totals(session: Session, season: int, before_week: int) -> dict[int, tuple[int, int]]:
    """{player_id: (carries + targets inside the 10, inside the 20)} before the week; divide by games played."""
    totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for r in session.scalars(select(PlayerRedZone).where(PlayerRedZone.season == season,
                                                         PlayerRedZone.week < before_week)):
        totals[r.player_id][0] += r.i10_carries + r.i10_targets
        totals[r.player_id][1] += r.rz_carries + r.rz_targets
    return {pid: (t[0], t[1]) for pid, t in totals.items()}
