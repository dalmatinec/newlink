from datetime import datetime

import database_admin as db_admin
from services.json_store import load

ADS_FILE = "ads.json"
SETTINGS_FILE = "settings.json"


def get_due_ad(user_id: int) -> tuple[int, dict] | None:
    """
    Возвращает (slot_id, slot) первого подходящего по расписанию рекламного
    слота, либо None. Считается лениво в момент обращения — без фонового
    планировщика.

    Режимы расписания:
      every_start     — показывать при каждом запуске/входе в меню
      once_ever        — только один раз за всё время
      interval_hours   — не чаще, чем раз в N часов
      times_per_day    — не больше N раз в сутки, с равномерным интервалом
    """
    settings = load(SETTINGS_FILE)
    if not settings.get("ads_enabled", True):
        return None

    slots = load(ADS_FILE).get("slots", {})
    for slot_id_str, slot in slots.items():
        if not slot.get("enabled") or not slot.get("content"):
            continue
        show_state = db_admin.get_ad_show(user_id, int(slot_id_str))
        if _is_due(slot.get("schedule", {}), show_state):
            return int(slot_id_str), slot
    return None


def _is_due(schedule: dict, show_state: dict) -> bool:
    mode = schedule.get("mode", "every_start")
    last_shown_at = show_state["last_shown_at"]
    shown_count = show_state["shown_count"]
    now = datetime.now()

    if mode == "every_start":
        return True

    if mode == "once_ever":
        return last_shown_at is None

    if mode == "interval_hours":
        if not last_shown_at:
            return True
        hours_passed = (now - datetime.fromisoformat(last_shown_at)).total_seconds() / 3600
        return hours_passed >= schedule.get("interval_hours", 24)

    if mode == "times_per_day":
        n = max(schedule.get("times_per_day", 1), 1)
        if shown_count >= n:
            return False
        if not last_shown_at:
            return True
        min_gap_hours = 24 / n
        hours_passed = (now - datetime.fromisoformat(last_shown_at)).total_seconds() / 3600
        return hours_passed >= min_gap_hours

    return False
