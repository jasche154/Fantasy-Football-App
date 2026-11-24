"""Replay League: the ADP step for a season the app saw live, where it differs from a past season's (replay_data
step 3).

Fantasy Football Calculator answers "no data" for the season in progress (it had no 2026 list in November
2026), so its preseason ADP comes from MyFantasyLeague alone: MFL's own average pick in real 12-team PPR
drafts that started from August 15 on, stored as the "ppr" list (replay_pool reads the nearest stored list for
the other formats). Once FFC has a list, replay_data.load_adp takes over as for any season.

Known limit: MFL's ADP request has no end date (PERIOD only sets the earliest draft start, and no other
parameter bounds it), so a first prepare made in-season also counts real drafts held after week 1 (late-starting
leagues): a little hindsight in the "preseason" ADP a live league's full-season draft uses (a season can only be
prepared once its week 1 is final). Accepted: it is fetched once, at the first prepare, and kept (again only by a
forced prepare, which would let in more of them); a jump-in league drafts on the simulated market instead.
"""
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from database.models import ReplayAdp
from integrations import adp_history
from integrations.adp_history import HistoryClient, SeasonMatcher
from services import replay_data


def load_live_adp(session: Session, season: int, client: HistoryClient, force: bool = False) -> dict:
    """replay_data.load_adp, or MFL's ADP as the PPR list when FFC has none. HistoryError when MFL fails too.
    `force` reaches this path through load_adp: it asks FFC for PPR again only when forced or when no complete
    PPR list (one with MFL rows) is stored, so a stored MFL list is kept until a forced run."""
    try:
        return replay_data.load_adp(session, season, client, force)
    except adp_history.FfcNoDataError:
        pass                                            # FFC was asked for PPR: forced, or nothing complete stored
    mfl = adp_history.fetch_mfl(client, season)
    crosswalk = replay_data.mfl_crosswalk()
    created = replay_data.create_drafted_players(session, mfl, crosswalk)
    matcher = SeasonMatcher(session, season)
    rows, seen = [], set()
    for entry in mfl:
        player_id = replay_data.adp_player(matcher, entry, crosswalk, {})
        if player_id is not None and player_id not in seen:
            seen.add(player_id)
            rows.append({"season": season, "player_id": player_id, "format": "ppr", "adp": entry.adp,
                         "stdev": None, "source": adp_history.MFL, "team": entry.team})
    session.execute(delete(ReplayAdp).where(ReplayAdp.season == season, ReplayAdp.format == "ppr"))
    if rows:
        session.execute(insert(ReplayAdp), rows)
    session.commit()
    return {"rows": len(rows), "source": adp_history.MFL, "ffc": "no data", "mfl_matched": f"{len(rows)}/{len(mfl)}",
            "players_created": created}
