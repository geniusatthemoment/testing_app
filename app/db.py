import sqlite3
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    photos TEXT NOT NULL DEFAULT '[]',
    calories REAL NOT NULL,
    proteins REAL NOT NULL,
    fats REAL NOT NULL,
    carbs REAL NOT NULL,
    composition TEXT,
    category TEXT NOT NULL,
    cooking_requirement TEXT NOT NULL,
    vegan INTEGER NOT NULL DEFAULT 0,
    gluten_free INTEGER NOT NULL DEFAULT 0,
    sugar_free INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS dishes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    photos TEXT NOT NULL DEFAULT '[]',
    calories REAL NOT NULL,
    proteins REAL NOT NULL,
    fats REAL NOT NULL,
    carbs REAL NOT NULL,
    portion_size REAL NOT NULL,
    category TEXT NOT NULL,
    vegan INTEGER NOT NULL DEFAULT 0,
    gluten_free INTEGER NOT NULL DEFAULT 0,
    sugar_free INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS dish_ingredients (
    dish_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (dish_id, product_id),
    FOREIGN KEY (dish_id) REFERENCES dishes(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_dishes_name ON dishes(name);
CREATE INDEX IF NOT EXISTS idx_ingredients_product ON dish_ingredients(product_id);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
