"""Конфигурация из .env. Только то, что нельзя хранить в базе: всё остальное меняется в /admin."""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    owner_ids: frozenset[int]
    data_dir: Path
    log_level: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


def load_config() -> Config:
    _load_dotenv(Path(".env"))
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")
    raw = os.environ.get("OWNER_IDS") or os.environ.get("ADMIN_IDS", "")
    owners = frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)
    if not owners:
        raise SystemExit("OWNER_IDS не задан: укажите Telegram ID владельца (через запятую)")
    return Config(
        bot_token=token,
        owner_ids=owners,
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
