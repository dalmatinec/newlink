import os

# Все остальные настройки (тексты, кнопки, реклама, флаги) — в JSON/БД,
# сюда попадает только то, без чего бот физически не может стартовать.

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
