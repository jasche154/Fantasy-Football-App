"""Preseason ADP for past seasons (Replay League), fetched once and stored by services/replay_data.py.

- Fantasy Football Calculator (FFC): 12-team mock-draft ADP from the days before week 1, one list per format
  (FFC_FORMATS: PPR, half-PPR, non-PPR and 2-QB, every season 2018-2025). Free with attribution ("ADP:
  Fantasy Football Calculator"); they ask not to be called often, so it is stored.
- MyFantasyLeague (MFL): real-draft ADP after August 15, about 300 deep. It supplies the long tail and the
  kickers and defenses FFC lacks, and its ids map to gsis ids through the DynastyProcess crosswalk.

Both report a player's team as of today or the end of that season, which is hindsight for anyone who moved.
The team is kept for every player (a defense is matched by it; replay_pool uses it only for a drafted player
who never plays) and is not validated here (MFL says 'FA'). Malformed rows are skipped. Also home of what the history imports share: HistoryClient (the polite fetcher)
and SeasonMatcher (a site's player -> players.id for a past season).
"""
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database.models import KickerGame, Player, PlayerStatsWeekly
from integrations.weekly_sources import SourceError, normalize_name, normalize_team

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0"
PAUSE_SECONDS = 1.0
TIMEOUT_SECONDS = 45.0          # the biggest payloads (Sleeper, MFL players) are 1-2 MB
ATTEMPTS = 2                    # one retry
FFC_URL = "https://fantasyfootballcalculator.com/api/v1/adp/{format}"
# replay_adp.format -> (FFC's path, the meta "type" FFC answers with). 2qb stands in for superflex.
FFC_FORMATS = {"ppr": ("ppr", "PPR"), "half": ("half-ppr", "Half-PPR"), "std": ("standard", "Non-PPR"),
               "2qb": ("2qb", "2 QB")}
MFL_URL = "https://api.myfantasyleague.com/{season}/export"
FFC, MFL = "ffc", "mfl"
POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "PK": "K", "DEF": "DST"}   # FFC "DEF", MFL "Def"
# MyFantasyLeague team codes that weekly_sources.TEAM_ALIASES does not already cover.
MFL_TEAMS = {"GBP": "GB", "KCC": "KC", "NEP": "NE", "NOS": "NO", "TBB": "TB", "SDC": "LAC", "RAM": "LA",
             "HST": "HOU", "BLT": "BAL", "CLV": "CLE", "ARZ": "ARI"}


class HistoryError(SourceError):
    """A historical source could not be fetched or parsed. The message never carries a URL."""


class HistoryClient:
    """One shared httpx client: a pause between requests, a timeout, one retry, then HistoryError."""

    def __init__(self, http: httpx.Client | None = None, pause: float = PAUSE_SECONDS,
                 sleep: Callable[[float], None] = time.sleep):
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._owns_http = http is None
        self._pause, self._sleep = pause, sleep
        self.requests = 0

    def get_json(self, label: str, url: str, params: Any = None, headers: Mapping[str, str] | None = None) -> Any:
        error: Exception | None = None
        for _ in range(ATTEMPTS):
            if self.requests:
                self._sleep(self._pause)
            self.requests += 1
            try:
                response = self._http.get(url, params=params, timeout=TIMEOUT_SECONDS,
                                          headers={"User-Agent": USER_AGENT, **(headers or {})})
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:      # ValueError: the body was not JSON
                error = exc
                log.warning("%s: request failed (%s)", label, type(exc).__name__)
        raise HistoryError(f"{label} could not be fetched ({type(error).__name__}).") from error

    def close(self) -> None:
        if self._owns_http:
            self._http.close()


@dataclass(frozen=True)
class AdpEntry:
    source: str                   # "ffc" | "mfl"
    name: str                     # "First Last"; defenses keep the site's label
    position: str                 # QB, RB, WR, TE, K, DST
    adp: float
    stdev: float | None = None
    team: str | None = None       # the site's team code, normalized; end-of-season (hindsight), unvalidated
    mfl_id: str | None = None
    espn_id: str | None = None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def team_code(code: str | None) -> str | None:
    upper = (code or "").strip().upper()
    return normalize_team(MFL_TEAMS.get(upper, upper))


def parse_rows(label: str, rows: Iterable[Any], convert: Callable[[Any], Any]) -> list:
    """convert() each row, dropping None results and rows too malformed to read (one warning, no content)."""
    out, skipped = [], 0
    for row in rows:
        try:
            item = convert(row)
        except (AttributeError, KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if item is not None:
            out.append(item)
    if skipped:
        log.warning("%s: skipped %d malformed rows", label, skipped)
    return out


def _ffc_entry(row: dict) -> AdpEntry | None:
    position, adp = POSITIONS.get(str(row.get("position") or "").upper()), _number(row.get("adp"))
    if position is None or adp is None or not row.get("name"):
        return None
    return AdpEntry(FFC, str(row["name"]), position, adp, _number(row.get("stdev")), team_code(row.get("team")))


def parse_ffc(payload: Any, adp_format: str = "ppr") -> list[AdpEntry]:
    """FFC answers HTTP 200 with status "Error", or "Success" with no players, when it has no data. A list of
    another format (FFC's meta "type") is refused rather than stored under the wrong one."""
    ok = isinstance(payload, dict) and payload.get("status") == "Success" and isinstance(payload.get("players"), list)
    meta = payload.get("meta") if isinstance(payload, dict) else None
    kind = meta.get("type") if isinstance(meta, dict) else None
    if ok and kind is not None and kind != FFC_FORMATS[adp_format][1]:
        raise HistoryError("Fantasy Football Calculator returned ADP for another format.")
    entries = parse_rows("Fantasy Football Calculator ADP", payload["players"], _ffc_entry) if ok else []
    if not entries:                     # an empty list too: a forced load would replace the season with MFL alone
        raise HistoryError("Fantasy Football Calculator has no ADP for that season.")
    return sorted(entries, key=lambda e: e.adp)


def fetch_ffc(http: HistoryClient, season: int, adp_format: str = "ppr") -> list[AdpEntry]:
    url = FFC_URL.format(format=FFC_FORMATS[adp_format][0])
    return parse_ffc(http.get_json("Fantasy Football Calculator ADP", url, params={"teams": 12, "year": season}),
                     adp_format)


def _first_last(name: str) -> str:
    """'Chase, Ja'Marr' -> 'Ja'Marr Chase'."""
    last, _, first = name.partition(",")
    return f"{first.strip()} {last.strip()}".strip()


def parse_mfl(adp_payload: Any, players_payload: Any) -> list[AdpEntry]:
    """Join MFL's ADP ids to that season's player file; defensive players (LB, DT, ...) are dropped."""
    try:
        picks = adp_payload["adp"]["player"]
        details = {str(p["id"]): p for p in players_payload["players"]["player"] if isinstance(p, dict) and "id" in p}
    except (KeyError, TypeError) as exc:
        raise HistoryError("MyFantasyLeague returned an unexpected response.") from exc

    def entry(pick: dict) -> AdpEntry | None:
        player = details.get(str(pick.get("id"))) or {}
        position, adp = POSITIONS.get(str(player.get("position") or "").upper()), _number(pick.get("averagePick"))
        if position is None or adp is None or not player.get("name"):
            return None
        return AdpEntry(MFL, _first_last(str(player["name"])), position, adp, team=team_code(player.get("team")),
                        mfl_id=str(pick["id"]), espn_id=str(player["espn_id"]) if player.get("espn_id") else None)

    entries = parse_rows("MyFantasyLeague ADP", picks if isinstance(picks, list) else [], entry)
    if not entries:
        raise HistoryError("MyFantasyLeague has no ADP for that season.")
    return sorted(entries, key=lambda e: e.adp)


def fetch_mfl(http: HistoryClient, season: int) -> list[AdpEntry]:
    url = MFL_URL.format(season=season)
    adp = http.get_json("MyFantasyLeague ADP", url, params={
        "TYPE": "adp", "JSON": 1, "PERIOD": "AUG15", "FCOUNT": 12, "IS_PPR": 1, "IS_KEEPER": "N", "IS_MOCK": 0})
    players = http.get_json("MyFantasyLeague players", url, params={"TYPE": "players", "JSON": 1, "DETAILS": 1})
    return parse_mfl(adp, players)


# Other sites' names -> the nflverse name stored in players.full_name (both normalized).
ALIASES = {"hollywood brown": "marquise brown", "joshua palmer": "josh palmer", "gabriel davis": "gabe davis",
           "kenneth gainwell": "kenny gainwell", "chigoziem okonkwo": "chig okonkwo",
           "michael badgley": "mike badgley"}
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
# Two-way players nflverse files under their defensive position: gsis id -> the position fantasy sites draft
# and project them at. Everyone else stored under another position (real defenders) stays unmatched.
# shortcut: a hand-kept list (one such player in 2018-2025); store the site's position with the ADP if it grows.
TWO_WAY = {"00-0040718": "WR"}          # Travis Hunter, stored as CB


def name_key(name: str) -> str:
    key = normalize_name(name)
    return ALIASES.get(key, key)


class SeasonMatcher:
    """Resolves a site's player to players.id for one past season: by platform id, else by name.

    FFC has no usable ids and every site lists a few players under ids the crosswalk lacks, so names are
    matched among the six fantasy positions, preferring players who played that season: old seasons
    bring namesakes (two Mike Williams, two Adrian Petersons) that today's rosters do not have.
    A TWO_WAY player counts as his fantasy position; nobody else stored under another position is known.
    """

    def __init__(self, session: Session, season: int):
        rows = session.execute(select(Player.id, Player.full_name, Player.position, Player.nfl_team, Player.gsis_id,
                                      Player.espn_player_id, Player.sleeper_player_id)
                               .where(or_(Player.position.in_(FANTASY_POSITIONS), Player.gsis_id.in_(TWO_WAY)))).all()
        played = set(session.scalars(select(PlayerStatsWeekly.player_id).where(
            PlayerStatsWeekly.season == season).distinct()))
        played |= set(session.scalars(select(KickerGame.player_id).where(KickerGame.season == season).distinct()))
        self.by_gsis = {r.gsis_id: r.id for r in rows if r.gsis_id}
        self.by_espn = {r.espn_player_id: r.id for r in rows if r.espn_player_id}
        self.by_sleeper = {r.sleeper_player_id: r.id for r in rows if r.sleeper_player_id}
        self._defenses = {r.nfl_team: r.id for r in rows if r.position == "DST"}
        self.positions = {r.id: TWO_WAY.get(r.gsis_id, r.position) for r in rows}      # players.id -> position
        self._names: dict[str, list[tuple[int, str, bool]]] = {}       # name -> (player id, position, played)
        for r in rows:
            if r.position != "DST":
                position = TWO_WAY.get(r.gsis_id, r.position)
                self._names.setdefault(normalize_name(r.full_name), []).append((r.id, position, r.id in played))

    def defense(self, team: str | None) -> int | None:
        return self._defenses.get(normalize_team(team) or "")

    def by_name(self, name: str, position: str) -> int | None:
        """The one player with this name: played that season at the position, else played that season
        (sites disagree on positions), else anyone at the position, else anyone. Ambiguous = None."""
        named = self._names.get(name_key(name), [])
        for pool in ([c for c in named if c[2] and c[1] == position], [c for c in named if c[2]],
                     [c for c in named if c[1] == position], named):
            if pool:
                return pool[0][0] if len(pool) == 1 else None
        return None
