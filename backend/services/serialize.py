"""Convert service results into JSON-ready dicts shared by the REST API and agent tools."""
from collections.abc import Mapping

from services.leaderboards import LeaderboardRow
from services.players import PlayerRef
from services.valuation import PlayerValue, Valuation


def player_ref(ref: PlayerRef | PlayerValue | LeaderboardRow) -> dict:
    player_id = ref.id if isinstance(ref, PlayerRef) else ref.player_id
    return {"id": player_id, "name": ref.name, "position": ref.position, "team": ref.team}


def ranked_player(value: PlayerValue, ecr: Mapping[int, float] | None = None) -> dict:
    return {
        "rank": value.rank, "pos_rank": value.pos_rank, "player": player_ref(value),
        "games": value.games, "ppg": value.ppg, "expected_ppg": value.expected_ppg,
        "ros_ppg": value.ros_ppg, "vorp_per_game": value.vorp_per_game,
        "trade_value": value.trade_value, "ecr": (ecr or {}).get(value.player_id),
    }


def rankings(valuation: Valuation, values: list[PlayerValue], ecr: Mapping[int, float]) -> dict:
    ranked = [{**ranked_player(v, ecr), "rank": i} for i, v in enumerate(values, start=1)]
    return {"season": valuation.season, "through_week": valuation.through_week,
            "scoring": valuation.league.scoring, "weeks_remaining": valuation.weeks_remaining,
            "results": ranked}


def leaderboard_row(row: LeaderboardRow) -> dict:
    return {"rank": row.rank, "player": player_ref(row), "value": row.value, "volume": row.volume}
