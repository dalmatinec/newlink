import os

# Здесь только то, без чего бот не может стартовать. Группа, тексты и лимиты
# антифлуда хранятся в БД и меняются командами админа без перезапуска.

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")

# Главные админы (через запятую). Их нельзя удалить командой /deladmin.
SUPER_ADMIN_IDS = {
    int(x) for x in os.getenv("SUPER_ADMIN_ID", "").replace(" ", "").split(",") if x
}

DB_PATH = os.getenv("DB_PATH", "bot.db")

# Необязательно: id группы при первом запуске (потом меняется через /setgroup).
DEFAULT_GROUP_ID = int(os.getenv("GROUP_ID", "0"))
