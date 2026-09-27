"""Начальные данные: настройки, тексты и системные кнопки дозаливаются при каждом старте,
если ключа ещё нет. Так новые ключи из обновлений появляются сами и не затирают правки из админки."""
import json
from pathlib import Path

from .db import Database

SEED_PATH = Path(__file__).with_name("seed.json")


def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


async def apply_seed(db: Database) -> None:
    seed = load_seed()
    await db.executemany("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                         ((k, json.dumps(v)) for k, v in seed["settings"].items()))
    await db.executemany("INSERT OR IGNORE INTO texts(key, html) VALUES (?, ?)", seed["texts"].items())
    await db.executemany("INSERT OR IGNORE INTO buttons(key, label, icon, style) VALUES (?, ?, ?, ?)",
                         ((k, b["label"], b.get("icon"), b.get("style")) for k, b in seed["buttons"].items()))
