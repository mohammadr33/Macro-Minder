"""
Persistence layer. Two tables, two different jobs:

1. food_cache -- avoids re-hitting USDA/OFF for a food we've already
   normalized. Nutrition data doesn't change often, so caching
   indefinitely (no TTL/expiry) is a reasonable simplification for now --
   flagged as a known limitation, not silently assumed correct forever.

2. evaluation_history -- a log of every food/goal check made, so the CLI
   can show "what have I checked recently" instead of being fully
   stateless between runs.

Food objects are stored as JSON (dataclasses.asdict), not as individual
columns -- the schema is simple/stable enough that a JSON blob is fine
here, and it avoids a schema migration every time a nutrient field gets
added to Food (which has already happened once this project -- is_processed).
"""

import sqlite3
import json
import dataclasses
from datetime import datetime, timezone
from normalize import Food

DB_PATH = "food_tool.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_cache (
            cache_key TEXT PRIMARY KEY,
            food_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            food_name TEXT NOT NULL,
            goal_name TEXT NOT NULL,
            tier TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            details_json TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def cache_key_for(source: str, identifier: str) -> str:
    """E.g. 'usda:522183' or 'off:3017620422003'. Keeps cache keys
    consistent regardless of which part of the app is calling in."""
    return f"{source}:{identifier}"


def get_cached_food(key: str) -> Food | None:
    conn = get_connection()
    row = conn.execute("SELECT food_json FROM food_cache WHERE cache_key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return None
    data = json.loads(row["food_json"])
    return Food(**data)


def cache_food(key: str, food: Food):
    conn = get_connection()
    food_json = json.dumps(dataclasses.asdict(food))
    conn.execute(
        "INSERT OR REPLACE INTO food_cache (cache_key, food_json, fetched_at) VALUES (?, ?, ?)",
        (key, food_json, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def log_evaluation(food: Food, goal_name: str, tier: str, reasons: list[str]):
    conn = get_connection()
    conn.execute(
        "INSERT INTO evaluation_history (food_name, goal_name, tier, evaluated_at, details_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (food.name, goal_name, tier, datetime.now(timezone.utc).isoformat(), json.dumps(reasons)),
    )
    conn.commit()
    conn.close()


def get_recent_history(limit: int = 10) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT food_name, goal_name, tier, evaluated_at FROM evaluation_history "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


if __name__ == "__main__":
    # Quick self-test with a throwaway food, no live API needed.
    init_db()
    test_food = Food(
        name="Test Food", source="usda", serving_size_g=100,
        calories=200, protein_g=10, total_fat_g=5, saturated_fat_g=1,
        carbs_g=20, sugar_g=5, fiber_g=3, sodium_mg=100, cholesterol_mg=10,
        is_processed=False,
    )
    key = cache_key_for("usda", "test123")
    cache_food(key, test_food)

    fetched = get_cached_food(key)
    print("Cached and re-fetched:", fetched)

    log_evaluation(test_food, "High Cholesterol Management", "suitable", ["Cholesterol: 10 within limit"])
    print("\nRecent history:")
    for entry in get_recent_history():
        print(" ", entry)
