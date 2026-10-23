"""Convert service results into JSON-ready dicts shared by the REST API and agent tools."""
from collections.abc import Mapping, Sequence

from services.insights import Insight
from services.leaderboards import LeaderboardRow
from services.players import PlayerRef
from services.trade import TradeEvaluation, TradeSide
from config import get_settings
from services.valuation import PlayerValue, Valuation
from services.weekly_model import Matchup
from services.weekly_rankings import SOURCE_LABELS, WEIGHTS, WeeklyPlayer, WeeklyRankings, FINAL_SOS_TILT

NEXT_WEEKS = 4


def player_ref(ref: PlayerRef | PlayerValue | Insight | LeaderboardRow) -> dict:
    player_id = ref.id if isinstance(ref, PlayerRef) else ref.player_id
    return {"id": player_id, "name": ref.name, "position": ref.position, "team": ref.team}


def ranked_player(value: PlayerValue, ecr: Mapping[int, float] | None = None) -> dict:
    return {
        "rank": value.rank, "pos_rank": value.pos_rank, "player": player_ref(value),
        "games": value.games, "ppg": value.ppg, "expected_ppg": value.expected_ppg,
        "ros_ppg": value.ros_ppg, "vorp_per_game": value.vorp_per_game,
        "trade_value": value.trade_value, "ecr": (ecr or {}).get(value.player_id),
        "schedule_factor": value.schedule_factor,
        "ros_sources": {"model": value.model_ros_ppg, "experts": value.expert_ros_ppg,
                        "expert_rank": value.expert_ros_rank, "weekly": value.weekly_ppg,
                        "availability": value.availability},
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


def matchup(item: Matchup) -> dict:
    return {"opponent": item.opponent, "home": item.home, "sos_factor": item.sos_factor,
            "sos_rank": item.sos_rank, "implied_total": item.implied_total, "team_ppg": item.team_ppg,
            "game_final": item.game_final}


def flag(item) -> dict:
    return {"key": item.key, "kind": item.kind, "direction": item.direction, "label": item.label,
            "detail": item.detail}


def weekly_player(player: WeeklyPlayer, flags: Sequence = ()) -> dict:
    return {
        "rank": player.rank, "pos_rank": player.pos_rank,
        "player": {"id": player.player_id, "name": player.name, "position": player.position,
                   "team": player.team},
        "projection": player.projection, "if_active": player.if_active, "play_prob": player.play_prob,
        "status": player.status, "floor": player.floor, "ceiling": player.ceiling,
        "boost_points": player.boost_points, "pct_rostered": player.pct_rostered,
        "scenarios": [{"teammate": s.teammate, "teammate_status": s.teammate_status,
                       "extra_points": s.extra_points, "projection": s.projection} for s in player.scenarios],
        "matchup": matchup(player.matchup),
        "sources": {key: {"points": round(v.points, 2), "rank": v.rank} for key, v in player.sources.items()},
        "expert_ranks": dict(player.expert_ranks),
        "flags": [flag(f) for f in flags],
    }


def explanation(player: WeeklyPlayer, flags: Sequence = ()) -> dict:
    return {**weekly_player(player, flags),
            "steps": [{"label": s.label, "value": s.value, "detail": s.detail} for s in player.explanation]}


def weekly_rankings(rankings: WeeklyRankings, position: str | None, limit: int,
                    statuses: Mapping[str, dict] | None, flags: Mapping[int, Sequence] | None = None,
                    ids: Sequence[int] | None = None) -> dict:
    flags = flags or {}
    players = [p for p in rankings.players if (position is None or p.position == position)
               and (not ids or p.player_id in ids)][:limit]
    return {
        "season": rankings.season, "week": rankings.week, "scoring": rankings.scoring,
        "weights": [{"key": k, "label": SOURCE_LABELS[k], "weight": w, "players": rankings.source_counts.get(k, 0)}
                    for k, w in WEIGHTS.items()],
        "sos_tilt": FINAL_SOS_TILT, "refresh": statuses,
        "results": [{**weekly_player(p, flags.get(p.player_id, ())), "rank": i} if position
                    else weekly_player(p, flags.get(p.player_id, ())) for i, p in enumerate(players, start=1)],
    }


def schedule_strength(valuation: Valuation, position: str, week: int) -> dict:
    """Defenses by opponent-adjusted points allowed, plus each offense's upcoming schedule."""
    defense, schedule = valuation.defense, valuation.schedule
    end_week = get_settings().fantasy_end_week
    defenses = sorted(((d, f) for (d, p), f in defense.factors.items() if p == position),
                      key=lambda item: -item[1])
    teams = sorted({g.home_team for g in schedule.games} | {g.away_team for g in schedule.games})
    offenses = []
    for team in teams:
        upcoming = schedule.remaining_opponents(team, week - 1, end_week)
        offenses.append({
            "team": team, "this_week": schedule.opponent(team, week),
            "next_weeks": [{"opponent": o, "sos_factor": defense.factor(o, position)} for o in upcoming[:NEXT_WEEKS]],
            "rest_of_season": defense.schedule_factor(upcoming, position), "games_left": len(upcoming),
        })
    offenses.sort(key=lambda o: -o["rest_of_season"])
    return {
        "season": valuation.season, "week": week, "position": position,
        "defenses": [{"rank": i, "team": d, "sos_factor": f, "games": defense.games[(d, position)],
                      "allowed_per_game": defense.allowed_per_game[(d, position)],
                      "this_week_opponent": schedule.opponent(d, week)}
                     for i, (d, f) in enumerate(defenses, start=1)],
        "offenses": offenses,
    }


def waiver(candidate) -> dict:
    from services.waivers import faab_label

    return {**weekly_player(candidate.player, candidate.flags), "pct_rostered": candidate.pct_rostered,
            "ros_value": candidate.ros_value, "week_value": candidate.week_value,
            "schedule_factor": round(candidate.schedule_factor, 3), "score": candidate.score,
            "faab_pct": candidate.faab_pct, "faab_label": faab_label(candidate.faab_pct),
            "reasons": list(candidate.reasons)}


def trade_offer(offer, weeks_remaining: int) -> dict:
    return {"give": [player_ref(p) for p in offer.give], "get": player_ref(offer.get),
            "get_ros_ppg": offer.get.ros_ppg, "evaluation": trade(offer.evaluation, weeks_remaining)}
