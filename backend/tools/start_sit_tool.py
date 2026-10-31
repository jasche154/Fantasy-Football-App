"""The `start_sit` agent tool: 2-4 players in, one start decision out (services/start_sit.py).

Rows use the shared compact weekly shape (tools/rows.py); the verdict sentence is built in code,
so the model only explains it.
"""
from sqlalchemy.orm import Session

from services.start_sit import StartSitError, decide
from services.weekly_rankings import WeeklyPlayer
from tools.registry import Tool, ToolError
from tools.rows import header, player_str, weekly_row

MAX_TEXT = 80
NOTE = ("proj already counts chance to play; range = floor-ceiling if he plays; p_outscores = chance the "
        "start outscores the runner-up in simulated weeks; a locked player's game has kicked off")


def _name(p: WeeklyPlayer) -> str:
    return player_str(p.name, p.position, p.team)


def start_sit(session: Session, players: list[str], league: str | None = None, slot: str | None = None) -> dict:
    try:
        result = decide(session, players, league, slot)
    except StartSitError as exc:
        raise ToolError(str(exc)) from exc
    rows = []
    for p in result["players"]:
        row = weekly_row(p, kickoff=result["kickoff"].get(p.player_id), sources=False)
        if p.player_id in result["locked"]:
            row["locked"] = True
        if result["trends"].get(p.player_id):
            row["trend"] = result["trends"][p.player_id]
        rows.append(row)
    decision = result["decision"]
    out_decision = {"start": _name(decision["start"]) if decision["start"] else None,
                    "confidence": decision["confidence"], "gap": decision["gap"],
                    "p_outscores": decision["p_outscores"], "verdict": decision["verdict"]}
    out = {"players": rows, "decision": {k: v for k, v in out_decision.items() if v is not None},
           "notes": result["notes"], "alerts": result["alerts"], "note": NOTE}
    return {**header(result["settings"].scoring, result["week"], result["league"]),
            **{k: v for k, v in out.items() if v not in (None, [], {})}}


START_SIT = Tool(
    "start_sit",
    "Decide who to start this week among 2-4 players (start/sit, flex A or B, sit my stud?). Uses the user's "
    "league scoring and kickoff locks; returns each player's projection, range, chance to play, matchup and "
    "usage trend, plus the pick, confidence (toss_up / lean / clear), point gap, chance he outscores the "
    "runner-up and a verdict. For a whole-roster lineup use get_my_team; for win chances use get_my_matchup.",
    {"type": "object", "properties": {
        "players": {"type": "array", "minItems": 2, "maxItems": 4,
                    "items": {"type": "string", "maxLength": MAX_TEXT,
                              "description": "Player name (e.g. \"Bijan Robinson\") or numeric player id."}},
        "league": {"type": "string", "maxLength": MAX_TEXT,
                   "description": "The user's imported Yahoo league (name). Optional when they have only one."},
        "slot": {"type": "string", "maxLength": 12, "description": "Lineup slot to fill, e.g. FLEX, RB, WR."}},
     "required": ["players"]},
    start_sit)
