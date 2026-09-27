"""Журнал действий админов: понятные русские описания вместо служебных ключей."""
from html import escape

from ...app import App

ACTION_NAMES = {
    "item.create": "создал кнопку", "item.delete": "удалил кнопку", "item.bind": "привязал чат к кнопке",
    "item.mode": "сменил режим кнопки", "item.toggle": "включил/выключил кнопку", "item.revoke": "отозвал ссылки",
    "ad.create": "создал рекламу", "ad.start": "запустил рекламу", "ad.pause": "поставил рекламу на паузу",
    "ad.delete": "удалил рекламу", "item.layout": "переставил кнопки меню",
    "user.ban": "забанил", "user.unban": "разбанил",
    "admin.add": "добавил админа", "admin.remove": "снял админа", "admin.perms": "изменил права админа",
    "broadcast.start": "запустил рассылку", "backup.create": "сделал бэкап", "backup.restore": "восстановил из бэкапа",
    "settings": "изменил настройку", "logchat.set": "подключил канал логов",
}
OBJECT_NAMES = {"item": "кнопка", "text": "текст", "btn": "системная кнопка", "ad": "реклама"}
FIELD_NAMES = {"label": "изменил название", "html": "изменил текст", "media": "изменил картинку",
               "media_removed": "убрал картинку"}


def describe(app: App, action: str, details: str) -> str:
    if action in ACTION_NAMES:
        return f"{ACTION_NAMES[action]} {escape(details[:60])}"
    obj, _, field = action.partition(".")
    key = details.split(":", 1)[0]
    name = key
    if obj == "item" and key.isdigit() and int(key) in app.store.items:
        name = app.store.items[int(key)].label
    elif obj == "ad" and key.isdigit() and int(key) in app.store.ads:
        name = app.store.ads[int(key)].title
    return f"{FIELD_NAMES.get(field, field)}: {OBJECT_NAMES.get(obj, obj)} <b>{escape(str(name)[:40])}</b>"
