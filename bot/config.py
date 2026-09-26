import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    super_admins: frozenset[int]
    db_path: str


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")
    admins = frozenset(
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
    )
    if not admins:
        raise SystemExit("ADMIN_IDS не задан (см. .env.example)")
    return Config(
        token=token,
        super_admins=admins,
        db_path=os.getenv("DB_PATH", "data/bot.db"),
    )
