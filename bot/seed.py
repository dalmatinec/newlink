"""Начальные данные: настройки, тексты и системные кнопки дозаливаются при каждом старте,
если ключа ещё нет. Так новые ключи из обновлений появляются сами и не затирают правки из админки.
Стандартные тексты прошлых версий (которые админ не трогал) обновляются на новые."""
import json
import re
from pathlib import Path

from .db import Database

SEED_PATH = Path(__file__).with_name("seed.json")


def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def _plain_typography(s: str) -> str:
    """Как выглядели бы старые тексты без длинных тире, ёлочек и символа многоточия."""
    s = re.sub("\\s*[\u2014\u2013]\\s*", " - ", s)
    return s.replace("\u00ab", "").replace("\u00bb", "").replace("\u2026", "...")


async def apply_seed(db: Database) -> None:
    seed = load_seed()
    await db.executemany("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                         ((k, json.dumps(v)) for k, v in seed["settings"].items()))
    await db.executemany("INSERT OR IGNORE INTO texts(key, html) VALUES (?, ?)", seed["texts"].items())
    await db.executemany("INSERT OR IGNORE INTO buttons(key, label, icon, style) VALUES (?, ?, ?, ?)",
                         ((k, b["label"], b.get("icon"), b.get("style")) for k, b in seed["buttons"].items()))

    await convert_layout(db)

    # тексты, оставшиеся стандартными, приводим к новой версии
    for r in await db.fetchall("SELECT key, html FROM texts"):
        new = seed["texts"].get(r["key"])
        if new is not None and r["html"] != new and _plain_typography(r["html"]) == new:
            await db.execute("UPDATE texts SET html = ? WHERE key = ?", (new, r["key"]))
    for r in await db.fetchall("SELECT key, label FROM buttons"):
        new = seed["buttons"].get(r["key"], {}).get("label")
        if new is not None and r["label"] != new and _plain_typography(r["label"]) == new:
            await db.execute("UPDATE buttons SET label = ? WHERE key = ?", (new, r["key"]))


async def convert_layout(db: Database) -> None:
    """Один раз: раскладка "N кнопок в ряду + широкие" превращается в явные ряды."""
    if await db.fetchval("SELECT 1 FROM settings WHERE key = 'layout_rows'"):
        return
    per_row = json.loads(await db.fetchval("SELECT value FROM settings WHERE key = 'per_row'") or "2")
    per_row = max(1, min(int(per_row), 4))
    rows: list[tuple[int, int, int]] = []
    r = p = 0
    for item in await db.fetchall("SELECT id, wide FROM items ORDER BY position, id"):
        if item["wide"]:
            if p:
                r, p = r + 1, 0
            rows.append((r, 0, item["id"]))
            r += 1
            continue
        rows.append((r, p, item["id"]))
        p += 1
        if p >= per_row:
            r, p = r + 1, 0
    await db.executemany("UPDATE items SET row = ?, position = ? WHERE id = ?", rows)
    await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('layout_rows', '1')")
