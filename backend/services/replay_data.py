"""Replay League: one-time import of a past season's point-in-time data.

    python -m services.replay_data --seasons 2018-2025 [--force]

prepare_season makes one season playable, skipping whatever is already stored (unless forced):
1. game results from nflverse: weekly player stats, schedule, kicker games and team game stats, then
   ("snaps") its snap counts into the shared player_snap_counts_weekly (optional: a failure never blocks);
2. the 32 team-defense pseudo-players;
3. preseason ADP (Fantasy Football Calculator, then MyFantasyLeague for the players it lacks) into replay_adp,
   one list per draft format (ppr, half, std, 2qb; the league's settings pick one, services/replay_adp_format),
   then ("experts") FantasyPros position ranks into replay_expert_ranks (services/replay_experts.py; 2020 on;
   optional: a failure never blocks);
4. ESPN's and Sleeper's pre-game projections for every regular-season week into replay_projections;
5. each week's injury tags (official report and roster status, nflverse) into replay_injuries
   (services/replay_injuries.py), matched after the ADP step because it creates drafted players who never played.

Outside requests go through one polite client (a pause between requests, one retry). Problems are
reported as exception type names only. Whether a season is ready to play is services/replay_pool's call.
"""
import argparse
import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence

import httpx
from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database.models import (
    KickerGame, NflGame, Player, PlayerSnapCountsWeekly, PlayerStatsWeekly, ReplayAdp, ReplayProjection,
    TeamGameStats,
)
from integrations import adp_history, espn_history, sleeper_projections
from integrations.adp_history import PAUSE_SECONDS, AdpEntry, HistoryClient, HistoryError, SeasonMatcher, name_key
from integrations.espn_history import WeekProjection
from integrations.weekly_sources import normalize_team
from services import replay_experts, replay_pool, system_status
from services.replay_experts import load_expert_ranks
from services.replay_injuries import load_injuries

log = logging.getLogger(__name__)

STATUS_KEY = "replay_prepare"
ADP_NO_DATA_KEY = "replay_adp_no_data"          # {season: [ADP formats FFC answered without a list]}
RESULT_TABLES = {"player_stats_weekly": PlayerStatsWeekly, "nfl_games": NflGame,
                 "kicker_games": KickerGame, "team_game_stats": TeamGameStats}
SOURCES = ((espn_history.SOURCE, espn_history.week_projections),
           (sleeper_projections.SOURCE, sleeper_projections.week_projections))
UNMATCHED_SHOWN = 10
_prepare_lock = threading.Lock()


class ReplayDataError(RuntimeError):
    """A season's game results could not be loaded, so nothing else can be matched to it."""


def _check_season(season: int) -> None:
    seasons = replay_pool.SUPPORTED_SEASONS         # ESPN and Sleeper have no pre-game projections before them
    if season not in seasons:
        raise ValueError(f"Replay seasons run from {seasons[0]} to {seasons[-1]}; {season} can't be prepared.")


def table_counts(session: Session, season: int) -> dict[str, int]:
    return {name: session.scalar(select(func.count()).select_from(model).where(model.season == season)) or 0
            for name, model in RESULT_TABLES.items()}


def rename_game_teams(session: Session, season: int) -> int:
    """nflverse schedules keep old codes (OAK, SD, STL) while its stats use today's; store today's."""
    renamed = 0
    for game in session.scalars(select(NflGame).where(NflGame.season == season)):
        teams = (normalize_team(game.home_team), normalize_team(game.away_team))
        if teams != (game.home_team, game.away_team):
            game.home_team, game.away_team = teams
            renamed += 1
    session.commit()
    return renamed


def _missing_scores(session: Session, season: int) -> int:
    """Team games without the opponent's score: they were loaded before the schedule's codes were fixed."""
    return session.scalar(select(func.count()).select_from(TeamGameStats).where(
        TeamGameStats.season == season, TeamGameStats.points_against.is_(None))) or 0


def load_game_results(session: Session, season: int, force: bool = False) -> dict:
    """Step 1. Only the loaders replay needs: no depth charts (they crash before 2025), NGS or snaps."""
    from database import advanced_stats, populate_stats as ps

    before = table_counts(session, season)
    index = ps.PlayerIndex(session)
    loaded = []
    if force or not before["player_stats_weekly"]:
        # Creates players the first time they appear (retired ones included). Their current team is left
        # alone on purpose: populate_stats.apply_latest_teams would set it to this old season's team.
        ps.load_feed(session, next(f for f in ps.FEEDS if f.model is PlayerStatsWeekly), [season], index)
        loaded.append("player_stats_weekly")
    errors: dict[str, str] = {}
    try:
        ps.sync_crosswalk(session, index)               # ESPN and Sleeper ids, retried on every prepare
    except Exception as exc:                            # prepare_season holds back ADP and projections
        log.warning("crosswalk: sync failed (%s)", type(exc).__name__)
        session.rollback()
        errors["crosswalk"] = type(exc).__name__
    if force or not before["nfl_games"]:
        ps.load_schedules(session, [season])
        loaded.append("nfl_games")
    renamed = rename_game_teams(session, season)
    if force or not (before["kicker_games"] and before["team_game_stats"]) or _missing_scores(session, season):
        advanced_stats.load_advanced_stats(session, [season], index)    # also creates the kickers
        loaded.append("kicker_games, team_game_stats")
    if loaded and season >= ps.FIRST_SEASON:            # older seasons feed no live model: keep its caches
        ps.record_load(session, [season])               # cached valuations notice the new rows
    counts = table_counts(session, season)
    missing = [name for name, rows in counts.items() if not rows]
    if missing:
        raise ReplayDataError(f"nflverse data for {season} is missing: {', '.join(missing)}.")
    return {**counts, "loaded": loaded, "games_renamed": renamed,
            "team_games_without_score": _missing_scores(session, season), "errors": errors}


def load_snaps(session: Session, season: int, force: bool = False) -> dict:
    """Step 1b. Snap counts (the Players screen's snap %). Every reader of the shared table filters by season
    and seasons before FIRST_SEASON are never read live. Any failure is reported, never raised."""
    from database import populate_stats as ps

    def stored() -> int:
        return session.scalar(select(func.count()).select_from(PlayerSnapCountsWeekly).where(
            PlayerSnapCountsWeekly.season == season)) or 0

    try:
        # A live season's rows belong to the live loader: --force here must not wipe them on a bad download.
        if stored() and (not force or season >= ps.FIRST_SEASON):
            return {"rows": stored(), "skipped": "already loaded"}
        ps.load_snap_counts(session, [season], ps.PlayerIndex(session))     # keeps the old rows if it fails
        rows = stored()
        if rows and season >= ps.FIRST_SEASON:
            ps.record_load(session, [season])
        return {"rows": rows, "errors": {} if rows else {"snaps": "NoRows"}}
    except Exception as exc:                            # optional data: the season stays playable without it
        log.warning("snaps %s: failed (%s)", season, type(exc).__name__)
        session.rollback()
        return {"errors": {"snaps": type(exc).__name__}}


def ensure_defenses(session: Session) -> int:
    """Step 2. Every team's D/ST pseudo-player exists (the live app only creates the rostered ones)."""
    from services.kdst_rankings import TEAM_NAMES, dst_player

    for team in TEAM_NAMES:
        dst_player(session, team)
    session.commit()
    return len(TEAM_NAMES)


def mfl_crosswalk() -> dict[str, str]:
    """MyFantasyLeague id -> gsis id (DynastyProcess, via nflreadpy). HistoryError when it cannot be loaded."""
    import nflreadpy as nfl

    try:
        frame = nfl.load_ff_playerids().select(["mfl_id", "gsis_id"]).drop_nulls()
    except Exception as exc:                            # nflreadpy raises a mix of HTTP and parsing errors
        raise HistoryError(f"The player id crosswalk could not be loaded ({type(exc).__name__}).") from exc
    return {str(int(mfl_id)): gsis for mfl_id, gsis in frame.iter_rows()}


def create_drafted_players(session: Session, entries: Iterable[AdpEntry], crosswalk: dict[str, str]) -> int:
    """Players drafted that summer who never played a loaded season (hurt in camp) still need a row."""
    wanted = {crosswalk[e.mfl_id]: e for e in entries if e.position != "DST" and e.mfl_id in crosswalk}
    known = set(session.scalars(select(Player.gsis_id).where(Player.gsis_id.in_(list(wanted)))))
    new = [Player(gsis_id=gsis, full_name=e.name, position=e.position)
           for gsis, e in wanted.items() if gsis not in known]
    session.add_all(new)
    session.flush()
    return len(new)


def _adp_player(matcher: SeasonMatcher, entry: AdpEntry, crosswalk: dict[str, str],
                via_mfl: dict[tuple[str, str], int]) -> int | None:
    if entry.position == "DST":
        return matcher.defense(entry.team)
    return (matcher.by_gsis.get(crosswalk.get(entry.mfl_id or "", ""))
            or matcher.by_espn.get(entry.espn_id or "")
            or via_mfl.get((name_key(entry.name), entry.position))
            or matcher.by_name(entry.name, entry.position))


def adp_rows(season: int, ffc: Sequence[tuple[AdpEntry, int | None]],
             mfl: Sequence[tuple[AdpEntry, int | None]]) -> list[dict]:
    """FFC's ADP as published, then the players only MFL has, one pick apart after FFC's last pick."""
    rows: list[dict] = []
    seen: set[int] = set()
    for entry, player_id in ffc:
        if player_id is not None and player_id not in seen:
            seen.add(player_id)
            rows.append({"season": season, "player_id": player_id, "adp": entry.adp, "stdev": entry.stdev,
                         "source": adp_history.FFC, "team": entry.team})
    pick = max((row["adp"] for row in rows), default=0.0)
    for entry, player_id in sorted(mfl, key=lambda pair: pair[0].adp):
        if player_id is not None and player_id not in seen:
            seen.add(player_id)
            pick = round(pick + 1.0, 1)
            rows.append({"season": season, "player_id": player_id, "adp": pick, "stdev": None,
                         "source": adp_history.MFL, "team": entry.team})
    return rows


def _ffc_lists(client: HistoryClient, season: int, formats: Iterable[str]
               ) -> tuple[dict[str, list[AdpEntry]], dict[str, str], set[str]]:
    """({format: FFC's list}, errors, formats FFC answered with no list). PPR is required (HistoryError); another
    format FFC cannot give is reported and left out, so whatever is stored for it stays (replay_pool reads the
    nearest format). A failed request is retried next run; an answer without data is not (see load_adp)."""
    lists: dict[str, list[AdpEntry]] = {}
    errors: dict[str, str] = {}
    no_data: set[str] = set()
    for adp_format in formats:
        if adp_format == "ppr":
            lists["ppr"] = adp_history.fetch_ffc(client, season)
            continue
        try:
            lists[adp_format] = adp_history.fetch_ffc(client, season, adp_format)
        except HistoryError as exc:                     # the type says why: FfcNoDataError or a glitch
            errors[f"adp ffc {adp_format}"] = type(exc).__name__
            log.warning("ADP %s: no %s list from FFC (%s)", season, adp_format, type(exc).__name__)
            if isinstance(exc, adp_history.FfcNoDataError):   # FFC said it has none; anything else is retried
                no_data.add(adp_format)
    return lists, errors, no_data


def _no_ffc_data(session: Session, season: int) -> set[str]:
    return set((system_status.read(session, ADP_NO_DATA_KEY) or {}).get(str(season), ()))


def _record_no_ffc_data(session: Session, season: int, formats: set[str]) -> None:
    known = system_status.read(session, ADP_NO_DATA_KEY) or {}
    system_status.record(session, ADP_NO_DATA_KEY, {**known, str(season): sorted(formats)})


def load_adp(session: Session, season: int, client: HistoryClient, force: bool = False) -> dict:
    """Step 3. One list per format in adp_history.FFC_FORMATS (see _ffc_lists), each followed by MFL's tail.
    A format is complete once it has MFL rows; a run fetches only the incomplete ones (all of them when forced),
    and a format FFC answered without a list is remembered (ADP_NO_DATA_KEY) and not asked for again until a
    forced run. If MFL or the id crosswalk fails, every stored format is left exactly as it is (never shrunk) and
    the error reported; a format with nothing stored gets FFC alone for now. The summary's counts are the PPR list's
    (when this run fetched it), plus rows per format.
    shortcut: MFL's PPR real-draft list fills every format's tail (past FFC's last pick the order barely
    differs); fetch MFL per format (IS_PPR=0, 2-QB leagues) if the tail ever matters."""
    stored = {(fmt, source): rows for fmt, source, rows in session.execute(
        select(ReplayAdp.format, ReplayAdp.source, func.count()).where(ReplayAdp.season == season)
        .group_by(ReplayAdp.format, ReplayAdp.source))}
    ppr_stored = sum(rows for (fmt, _), rows in stored.items() if fmt == "ppr")
    complete = {fmt for fmt, source in stored if source == adp_history.MFL}
    no_data = set() if force else _no_ffc_data(session, season)
    wanted = [fmt for fmt in adp_history.FFC_FORMATS if force or fmt not in complete | no_data]
    if not wanted:
        return {"rows": ppr_stored, "skipped": True}
    lists, errors, missing = _ffc_lists(client, season, wanted)
    if force or missing:
        _record_no_ffc_data(session, season, no_data | missing)
    if not lists:                                       # nothing new from FFC: MFL alone changes nothing
        return {"rows": ppr_stored, "kept": True, "errors": errors}
    mfl: list[AdpEntry] = []
    crosswalk: dict[str, str] = {}
    try:
        mfl = adp_history.fetch_mfl(client, season)
        crosswalk = mfl_crosswalk()
    except HistoryError as exc:                         # MFL rows without ids would be matched by name alone
        errors["adp crosswalk" if mfl else "adp mfl"] = type(exc).__name__
        mfl = []
    if not mfl and stored:                             # keep what is stored; add only formats it lacks, FFC alone
        lists = {fmt: entries for fmt, entries in lists.items() if fmt not in {f for f, _ in stored}}
        if not lists:
            return {"rows": ppr_stored, "kept": True, "errors": errors}
        log.warning("ADP %s: no MFL tail (%s); %s stored from FFC alone, the stored formats kept as they are",
                    season, ", ".join(errors), ", ".join(lists))
    created = create_drafted_players(session, mfl, crosswalk)
    matcher = SeasonMatcher(session, season)
    mfl_pairs = [(e, _adp_player(matcher, e, crosswalk, {})) for e in mfl]
    via_mfl = {(name_key(e.name), e.position): pid for e, pid in mfl_pairs if pid is not None}
    pairs = {fmt: [(e, _adp_player(matcher, e, crosswalk, via_mfl)) for e in entries] for fmt, entries in lists.items()}
    by_format = {fmt: adp_rows(season, fmt_pairs, mfl_pairs) for fmt, fmt_pairs in pairs.items()}
    session.execute(delete(ReplayAdp).where(ReplayAdp.season == season, ReplayAdp.format.in_(list(by_format))))
    rows = [{**row, "format": fmt} for fmt, fmt_rows in by_format.items() for row in fmt_rows]
    if rows:
        session.execute(insert(ReplayAdp), rows)
    session.commit()
    missed: dict[tuple[str, str], str] = {}             # one line per player, the PPR list's first
    for e, pid in (*(pair for fmt_pairs in pairs.values() for pair in fmt_pairs), *mfl_pairs):
        if pid is None:
            missed.setdefault((e.name, e.position), f"{e.name} ({e.position}, {e.source} {e.adp})")
    absent = sorted(set(adp_history.FFC_FORMATS) - {f for f, _ in stored} - set(by_format))
    if absent:
        log.warning("ADP %s: no list stored for %s (replay_pool reads the nearest format)", season, ", ".join(absent))
    unmatched = list(missed.values())
    if unmatched:
        log.info("ADP %s: %d unmatched: %s", season, len(unmatched), "; ".join(unmatched))
    by_source = Counter(row["source"] for row in by_format.get("ppr", ()))
    positions = dict(session.execute(select(Player.position, func.count()).join(
        ReplayAdp, ReplayAdp.player_id == Player.id).where(ReplayAdp.season == season, ReplayAdp.format == "ppr")
        .group_by(Player.position)).all())
    return {"rows": len(by_format["ppr"]) if "ppr" in by_format else ppr_stored,
            "ffc": by_source[adp_history.FFC], "mfl": by_source[adp_history.MFL],
            "ffc_matched": f"{sum(pid is not None for _, pid in pairs.get('ppr', ()))}/{len(lists.get('ppr', ()))}",
            "mfl_matched": f"{sum(pid is not None for _, pid in mfl_pairs)}/{len(mfl)}",
            "formats": {fmt: len(fmt_rows) for fmt, fmt_rows in by_format.items()},
            "positions": positions, "players_created": created,
            "unmatched": unmatched[:UNMATCHED_SHOWN], "errors": errors}


def projection_rows(matcher: SeasonMatcher, source: str, season: int, week: int,
                    entries: Iterable[WeekProjection]) -> tuple[list[dict], list[WeekProjection]]:
    """(rows to store, entries no player matched). A player a site lists twice keeps the first line."""
    by_id = matcher.by_espn if source == espn_history.SOURCE else matcher.by_sleeper
    rows, unmatched, seen = [], [], set()
    for entry in entries:
        player_id = (matcher.defense(entry.team) if entry.position == "DST"
                     else by_id.get(entry.source_id) or matcher.by_name(entry.name, entry.position))
        if player_id is None:
            unmatched.append(entry)
        elif player_id not in seen:
            seen.add(player_id)
            rows.append({"season": season, "week": week, "player_id": player_id, "source": source,
                         "pts_ppr": entry.pts_ppr, "pts_half": entry.pts_half, "pts_std": entry.pts_std})
    return rows, unmatched


def _store_week(session: Session, season: int, week: int, source: str, rows: list[dict]) -> None:
    session.execute(delete(ReplayProjection).where(
        ReplayProjection.season == season, ReplayProjection.week == week, ReplayProjection.source == source))
    session.execute(insert(ReplayProjection), rows)
    session.commit()


def load_projections(session: Session, season: int, client: HistoryClient, force: bool = False) -> dict:
    """Step 4. One request per source and week; a source that fails is left for the next run."""
    last_week = session.scalar(select(func.max(NflGame.week)).where(
        NflGame.season == season, NflGame.game_type == "REG")) or 0
    stored = {(source, week): rows for source, week, rows in session.execute(
        select(ReplayProjection.source, ReplayProjection.week, func.count())
        .where(ReplayProjection.season == season).group_by(ReplayProjection.source, ReplayProjection.week))}
    matcher = SeasonMatcher(session, season)
    out: dict = {"weeks": last_week, "errors": {}}
    for source, fetch in SOURCES:
        rows_by_week: dict[int, int] = {}
        missed: Counter[str] = Counter()
        matched = total = 0
        for week in range(1, last_week + 1):
            if stored.get((source, week)) and not force:
                rows_by_week[week] = stored[(source, week)]
                continue
            try:
                entries = fetch(client, season, week)
            except HistoryError as exc:
                out["errors"][f"projections {source}"] = type(exc).__name__
                break
            if not entries:                             # a changed payload shape parses to nothing: say so
                out["errors"][f"projections {source} week {week}"] = "NoRows"
            rows, unmatched = projection_rows(matcher, source, season, week, entries)
            if rows:                                    # an empty week is not stored, so a re-run asks again
                _store_week(session, season, week, source, rows)
            rows_by_week[week] = len(rows)
            matched, total = matched + len(rows), total + len(entries)
            missed.update({f"{e.name} ({e.position})": e.pts_ppr for e in unmatched})
        out[source] = {"rows": rows_by_week, "matched": f"{matched}/{total}",
                       "unmatched": [name for name, _ in missed.most_common(UNMATCHED_SHOWN)]}
    return out


def prepare_season(session: Session, season: int, http: httpx.Client | None = None, force: bool = False,
                   on_step: Callable[[str], None] | None = None) -> dict:
    """Import everything a replay of `season` needs. Idempotent; returns a summary of each step."""
    _check_season(season)
    client = HistoryClient(http, pause=PAUSE_SECONDS)
    started = time.monotonic()
    summary: dict = {"season": season, "errors": {}}

    def run(step: str, work: Callable[[], dict]) -> None:
        if on_step:
            on_step(step)
        try:
            result = work()
        except HistoryError as exc:
            session.rollback()
            result = {"errors": {step: type(exc).__name__}}
        summary["errors"].update(result.pop("errors", {}))
        summary[step] = result

    try:
        run("game_results", lambda: load_game_results(session, season, force))
        summary["defenses"] = ensure_defenses(session)
        if "crosswalk" in summary["errors"]:          # nothing is stored without ids; the next run retries
            skipped = {"skipped": "player ids not synced"}       # snap counts match players by their PFR id
            for step in ("snaps", "adp", "experts", "projections", "injuries"):
                summary[step] = skipped
        else:
            run("snaps", lambda: load_snaps(session, season, force))
            run("adp", lambda: load_adp(session, season, client, force))
            run("experts", lambda: load_expert_ranks(session, season, force))
            run("projections", lambda: load_projections(session, season, client, force))
            # Reloaded whenever ADP was: that step may have created players the tags must be matched to.
            run("injuries", lambda: load_injuries(session, season, force or not summary["adp"].get("skipped")))
    finally:
        client.close()
        replay_pool.clear_cache()           # a season loaded before this import is read again
    summary["requests"], summary["seconds"] = client.requests, round(time.monotonic() - started, 1)
    return summary


def _state(season: int, state: str, step: str | None, error: str | None = None) -> dict:
    return {"season": season, "status": state, "step": step, "error": error}


def status(session: Session) -> dict | None:
    """The last prepare: {"season", "status": "running" | "done" | "failed", "step", "error", "updated_at"}."""
    value = system_status.read(session, STATUS_KEY)
    if value and value.get("status") == "running" and not _prepare_lock.locked():
        return {**value, "status": "failed", "error": "Interrupted"}      # the app stopped mid-import
    return value


def _run_prepare(bind: Engine, season: int, force: bool) -> None:
    try:
        with Session(bind) as session:
            try:
                summary = prepare_season(session, season, force=force, on_step=lambda step: system_status.record(
                    session, STATUS_KEY, _state(season, "running", step)))
                problems = ", ".join(sorted(set(summary["errors"].values()))) or None
                system_status.record(session, STATUS_KEY, _state(season, "done", None, problems))
            except Exception as exc:                    # never let a background thread die silently
                log.exception("replay prepare failed")
                session.rollback()
                system_status.record(session, STATUS_KEY, _state(season, "failed", None, type(exc).__name__))
    finally:
        replay_experts.clear_cache()
        _prepare_lock.release()


def start_prepare(season: int, bind: Engine | None = None, force: bool = False) -> dict:
    """Prepare a season on a background thread; poll status(). Only one runs at a time: while one is
    running this returns ITS status (maybe another season) and starts nothing."""
    _check_season(season)
    if bind is None:
        from database.db import engine as bind
    with Session(bind) as session:
        if not _prepare_lock.acquire(blocking=False):
            return status(session) or _state(season, "running", None)
        started = _state(season, "running", "starting")
        system_status.record(session, STATUS_KEY, started)
    threading.Thread(target=_run_prepare, args=(bind, season, force), name="replay-prepare", daemon=True).start()
    return started


def parse_seasons(text: str) -> list[int]:
    """'2018-2025' or '2019,2023' -> the seasons, in order."""
    seasons: list[int] = []
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        seasons.extend(range(int(first), int(last or first) + 1))
    return seasons


def main(argv: list[str] | None = None) -> None:
    from database.create_tables import ensure_schema
    from database.db import engine

    parser = argparse.ArgumentParser(description="Import past seasons for Replay League.")
    parser.add_argument("--seasons", required=True, help="e.g. 2018-2025 or 2019,2023")
    parser.add_argument("--force", action="store_true", help="re-import what is already stored")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)      # house rule: request URLs stay out of logs
    logging.getLogger("alembic").setLevel(logging.WARNING)
    print(f"Database: {engine.url.render_as_string(hide_password=True)} (schema {ensure_schema(engine)})")
    failed = False
    for season in parse_seasons(args.seasons):              # one expert archive download serves every season
        with Session(engine) as session:
            try:
                summary = prepare_season(session, season, force=args.force)
            except Exception as exc:
                log.exception("season %s failed", season)
                summary = {"season": season, "errors": {"prepare": type(exc).__name__}}
        print(json.dumps(summary, indent=1))
        failed = failed or bool(summary.get("errors"))
    replay_experts.clear_cache()
    if failed:
        raise SystemExit(1)                         # so a script can tell an import went wrong


if __name__ == "__main__":
    main()
