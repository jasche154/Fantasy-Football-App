"""Expert consensus rankings (FantasyPros ECR) stored in the expert_rankings table."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import ExpertRanking, Player

SOURCE_PREFERENCE = ("fantasypros_api", "nflverse_ffrankings")


def latest_source(session: Session) -> str | None:
    available = set(session.scalars(select(ExpertRanking.source).distinct()))
    return next((s for s in SOURCE_PREFERENCE if s in available), None)


def weekly_ecr_by_player(session: Session) -> dict[int, float]:
    """{player_id: ecr} from the free weekly snapshot, used as a column in rankings."""
    rows = session.execute(select(ExpertRanking.player_id, ExpertRanking.ecr).where(
        ExpertRanking.source == "nflverse_ffrankings", ExpertRanking.player_id.is_not(None)))
    return {r.player_id: r.ecr for r in rows}


def expert_rankings(session: Session, position: str | None = None, limit: int = 50,
                    source: str | None = None) -> dict:
    """ECR rows from the preferred stored source.

    The free nflverse snapshot only has per-position pages, so its ranks are positional
    (every position has a rank 1). Unfiltered, it's grouped by position, never interleaved
    as if it were an overall ranking.
    """
    source = source or latest_source(session)
    if source is None:
        return {"source": None, "ranking_type": None, "scope": None, "season": None, "week": None,
                "results": []}
    scope = "positional" if source == "nflverse_ffrankings" else "overall"
    query = (select(ExpertRanking, Player).outerjoin(Player, Player.id == ExpertRanking.player_id)
             .where(ExpertRanking.source == source))
    if position:
        query = query.where(ExpertRanking.position == position)
    if scope == "positional" and not position:
        query = query.order_by(ExpertRanking.position, ExpertRanking.rank)
    else:
        query = query.order_by(ExpertRanking.rank, ExpertRanking.ecr)
    rows = session.execute(query.limit(limit)).all()
    first = rows[0][0] if rows else None
    return {
        "source": source,
        "ranking_type": first.ranking_type if first else None, "scope": scope,
        "season": first.season if first else None,
        "week": first.week if first else None,
        "results": [{
            "player": ({"id": p.id, "name": p.full_name, "position": p.position, "team": p.nfl_team}
                       if p else None),
            "name": r.player_name, "position": r.position, "team": r.team, "rank": r.rank,
            "ecr": r.ecr, "pos_rank": r.pos_rank, "best": r.best, "worst": r.worst, "sd": r.sd,
        } for r, p in rows],
    }
