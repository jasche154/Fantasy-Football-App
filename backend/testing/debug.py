from database.db import engine
from sqlalchemy import text
conn = engine.connect()
print(conn.execute(text("PRAGMA table_info(players)")).fetchall())
print(conn.execute(text("SELECT COUNT(*) FROM players")).scalar())