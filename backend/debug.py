"""
Quick debug script: check whether a player exists in `players`,
and if so, whether they have any rows in `player_rushing_advanced_weekly`.

Usage: edit SEARCH_NAME below and run from the project root:
    python debug_player_lookup.py
"""

from sqlalchemy import select
from sqlalchemy.orm import Session
from database.models import Player, PlayerRushingAdvancedWeekly
from database.db import engine

SEARCH_NAME = "tuten"  # partial, case-insensitive match

with Session(engine) as session:
    # Step 1: find any player whose name contains the search string
    matches = session.execute(
        select(Player).where(Player.full_name.ilike(f"%{SEARCH_NAME}%"))
    ).scalars().all()

    if not matches:
        print(f"No player found in `players` matching '{SEARCH_NAME}'.")
    else:
        print(f"Found {len(matches)} matching player(s):\n")
        for p in matches:
            print(f"  id={p.id}  name={p.full_name!r}  position={p.position}  "
                  f"gsis_id={p.gsis_id}  team={p.nfl_team}")

        print()

        # Step 2: for each match, check rushing advanced stats rows
        for p in matches:
            rows = session.execute(
                select(PlayerRushingAdvancedWeekly)
                .where(PlayerRushingAdvancedWeekly.player_id == p.id)
            ).scalars().all()

            print(f"--- {p.full_name} (id={p.id}) ---")
            print(f"  rushing_advanced_weekly rows: {len(rows)}")

            if rows:
                seasons = sorted(set(r.season for r in rows))
                print(f"  seasons present: {seasons}")
                total_attempts = sum(r.rush_attempts or 0 for r in rows)
                print(f"  total rush_attempts across all rows: {total_attempts}")
            else:
                print("  -> No rushing advanced stats rows at all for this player.")
            print()
    for p in matches:
        rows = session.execute(
            select(PlayerRushingAdvancedWeekly)
            .where(PlayerRushingAdvancedWeekly.player_id == p.id)
        ).scalars().all()

        for r in rows:
            print(f"  week={r.week}  season_type={r.season_type}  rush_attempts={r.rush_attempts}")