from tools.rushleaders import RUSHING_METRICS

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_rushing_leaders",
            "description": "Returns the top running backs ranked by a rushing efficiency metric for a given season.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": list(RUSHING_METRICS.keys()),  # reuses your whitelist directly
                        "description": "Which rushing metric to rank by."
                    },
                    "season": {
                        "type": "integer",
                        "description": "The NFL season year, e.g. 2025."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many players to return, ranked highest to lowest (or lowest to highest if ascending is true)."
                    },
                    "min_attempts": {
                        "type": "integer",
                        "description": "The minimum number of attempts the player must have had to be included on the list"
                    },
                    "ascending": {
                        "type": "boolean",
                        "description": "If true, returns the lowest values first instead of the highest. Defaults to false (highest first)."
                    },
                    "player_name": {
                        "type": "string",
                        "description": "The player's name to look up their individual rank and stat value, instead of returning the full leaderboard. Omit this to get the top-ranked players overall"
                    },
                },
                "required": ["metric", "season"]
            }
        }
    }
]