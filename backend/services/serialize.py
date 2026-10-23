"""Convert service results into JSON-ready dicts shared by the REST API and agent tools."""
from collections.abc import Mapping

from services.insights import Insight
from services.leaderboards import LeaderboardRow
from services.players import PlayerRef
from services.trade import TradeEvaluation, TradeSide
from services.valuation import PlayerValue, Valuation


def player_ref(ref: PlayerRef | PlayerValue | Insight | LeaderboardRow) -> dict:
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


def insight(item: Insight) -> dict:
    return {
        "player": player_ref(item), "score": item.score, "games": item.games, "ppg": item.ppg,
        "expected_ppg": item.expected_ppg, "fpoe_per_game": item.fpoe_per_game,
        "td_diff": item.td_diff, "recent_ppg": item.recent_ppg, "usage_trend": item.usage_trend,
        "trade_value": item.trade_value, "reasons": list(item.reasons),
    }


def insights(valuation: Valuation, items: list[Insight]) -> dict:
    return {"season": valuation.season, "through_week": valuation.through_week,
            "scoring": valuation.league.scoring, "results": [insight(i) for i in items]}


def _trade_side(side: TradeSide) -> dict:
    return {
        "players": [{"player": player_ref(p.value), "trade_value": p.value.trade_value,
                     "ros_ppg": p.value.ros_ppg, "vorp_per_game": p.value.vorp_per_game,
                     "weight": p.weight, "adjusted_value": p.adjusted_value} for p in side.players],
        "raw_total": side.raw_total, "adjusted_total": side.adjusted_total,
    }


def trade(evaluation: TradeEvaluation, weeks_remaining: int) -> dict:
    return {
        "give": _trade_side(evaluation.give), "get": _trade_side(evaluation.get),
        "difference": evaluation.difference, "percent_difference": evaluation.percent_difference,
        "verdict": evaluation.verdict, "summary": evaluation.summary,
        "balance_suggestions": [{"player": player_ref(s.value), "trade_value": s.value.trade_value,
                                 "add_to": s.add_to,
                                 "resulting_percent_difference": s.resulting_percent_difference}
                                for s in evaluation.balance_suggestions],
        "weeks_remaining": weeks_remaining,
    }


def leaderboard_row(row: LeaderboardRow) -> dict:
    return {"rank": row.rank, "player": player_ref(row), "value": row.value, "volume": row.volume}
