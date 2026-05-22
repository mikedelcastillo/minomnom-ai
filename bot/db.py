import aiosqlite
from config import DB_PATH

CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    name        TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_MEALS = """
CREATE TABLE IF NOT EXISTS meals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    description  TEXT NOT NULL,
    calories_min INTEGER NOT NULL,
    calories_max INTEGER NOT NULL,
    protein_min  REAL NOT NULL,
    protein_max  REAL NOT NULL,
    carbs_min    REAL NOT NULL,
    carbs_max    REAL NOT NULL,
    fat_min      REAL NOT NULL,
    fat_max      REAL NOT NULL,
    portion_note TEXT,
    logged_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_USERS)
        await db.execute(CREATE_MEALS)
        await db.commit()


async def upsert_user(telegram_id: int, name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (telegram_id, name) VALUES (?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name",
            (telegram_id, name),
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0]


async def log_meal(user_id: int, description: str, macros: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """INSERT INTO meals
               (user_id, description, calories_min, calories_max,
                protein_min, protein_max, carbs_min, carbs_max,
                fat_min, fat_max, portion_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                description,
                macros["calories"][0],
                macros["calories"][1],
                macros["protein_g"][0],
                macros["protein_g"][1],
                macros["carbs_g"][0],
                macros["carbs_g"][1],
                macros["fat_g"][0],
                macros["fat_g"][1],
                macros.get("portion_note"),
            ),
        ) as cur:
            meal_id = cur.lastrowid
        await db.commit()
        return meal_id


async def get_today(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT description, calories_min, calories_max,
                      protein_min, protein_max, carbs_min, carbs_max,
                      fat_min, fat_max, logged_at
               FROM meals
               WHERE user_id = ? AND date(logged_at) = date('now')
               ORDER BY logged_at""",
            (user_id,),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_week(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT date(logged_at) as day,
                      SUM(calories_min) as cal_min,
                      SUM(calories_max) as cal_max,
                      SUM(protein_min)  as prot_min,
                      SUM(protein_max)  as prot_max,
                      SUM(carbs_min)    as carb_min,
                      SUM(carbs_max)    as carb_max,
                      SUM(fat_min)      as fat_min,
                      SUM(fat_max)      as fat_max,
                      COUNT(*)          as meal_count
               FROM meals
               WHERE user_id = ? AND logged_at >= date('now', '-6 days')
               GROUP BY day
               ORDER BY day DESC""",
            (user_id,),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_history(user_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, description, calories_min, calories_max, logged_at
               FROM meals
               WHERE user_id = ?
               ORDER BY logged_at DESC
               LIMIT ?""",
            (user_id, limit),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_last_meal(user_id: int) -> "dict | None":
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, description FROM meals WHERE user_id = ? ORDER BY logged_at DESC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_meal_by_id(meal_id: int, user_id: int) -> "dict | None":
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, description FROM meals WHERE id = ? AND user_id = ?",
            (meal_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_meal(meal_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "DELETE FROM meals WHERE id = ? AND user_id = ?", (meal_id, user_id)
        ) as cur:
            deleted = cur.rowcount > 0
        await db.commit()
        return deleted
