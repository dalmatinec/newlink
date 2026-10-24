#!/usr/bin/env bash
# Установка, обновление или восстановление бота на сервере Ubuntu/Debian. Запускать от root:
#   bash /opt/<имя>/deploy/install.sh                        установить или обновить
#   bash /opt/<имя>/deploy/install.sh /root/backup_XXXX.zip  установить и восстановить всё из бэкапа
# Имя бота = имя папки: /opt/newlink -> сервис newlink, /opt/mango -> сервис mango. Можно ставить несколько ботов.
# Токен и ID владельца спросит один раз и запишет в <папка>/.env
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="$(basename "$DIR")"
cd "$DIR"

echo "==> Проверяю python3-venv"
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    apt-get update -qq && apt-get install -y -qq python3-venv
fi
id "$NAME" >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$NAME"

echo "==> Ставлю зависимости (1-3 минуты)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    read -rp "Токен бота от @BotFather: " TOKEN
    read -rp "Telegram ID владельца (несколько через запятую): " OWNERS
    printf 'BOT_TOKEN=%s\nOWNER_IDS=%s\nDATA_DIR=data\nLOG_LEVEL=INFO\n' "$TOKEN" "$OWNERS" > .env
fi
mkdir -p data

echo "==> Запускаю сервис $NAME"
systemctl stop "$NAME" 2>/dev/null || true
if [ $# -ge 1 ]; then
    .venv/bin/python -m bot.restore "$1"
fi

chown -R "$NAME:$NAME" data
chown "$NAME:$NAME" .env && chmod 600 .env
sed -e "s#/opt/newlink#$DIR#g" -e "s#^User=newlink#User=$NAME#" -e "s#^Group=newlink#Group=$NAME#" \
    -e "s#^Description=.*#Description=$NAME invite links bot#" deploy/newlink.service > "/etc/systemd/system/$NAME.service"
systemctl daemon-reload
systemctl enable --now "$NAME"
sleep 3
systemctl --no-pager --lines=5 status "$NAME" || true
echo
echo "Готово. Логи: journalctl -u $NAME -f"
