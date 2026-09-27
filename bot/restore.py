"""Восстановление из архива на сервере (бот должен быть остановлен):
    python -m bot.restore data/backups/backup_20260101_040000.zip
"""
import sys
from pathlib import Path

from .backup import restore_files
from .config import load_config


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    config = load_config()
    restore_files(Path(sys.argv[1]), config.db_path, config.media_dir)
    print("Готово. Запустите бота: python -m bot")


if __name__ == "__main__":
    main()
