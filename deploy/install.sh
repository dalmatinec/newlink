#!/usr/bin/env bash
# Установка, обновление или восстановление бота на сервере Ubuntu/Debian. Запускать от root:
#   bash /opt/newlink/deploy/install.sh                        установить или обновить
#   bash /opt/newlink/deploy/install.sh /root/backup_XXXX.zip  установить и восстановить всё из бэкапа
# Токен и ID владельца спросит один раз и запишет в /opt/newlink/.env
set -euo pipefail

DIR=/opt/newlink
cd "$DIR" || { echo "Сначала: git clone https://github.com/dalmatinec/newlink $DIR"; exit 1; }

echo "==> Проверяю python3-venv"
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    apt-get update -qq && apt-get install -y -qq python3-venv
fi
id newlink >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin newlink

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

echo "==> Запускаю сервис newlink"
systemctl stop newlink 2>/dev/null || true
if [ $# -ge 1 ]; then
    .venv/bin/python -m bot.restore "$1"
fi

chown -R newlink:newlink data
chown newlink:newlink .env && chmod 600 .env
cp deploy/newlink.service /etc/systemd/system/newlink.service
systemctl daemon-reload
systemctl enable --now newlink
sleep 3
systemctl --no-pager --lines=5 status newlink || true
echo
echo "Готово. Логи: journalctl -u newlink -f"
