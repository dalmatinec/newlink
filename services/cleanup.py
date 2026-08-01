import os

TEMP_DIR = "temp"


def clean_temp() -> None:
    """Чистим папку temp при старте процесса. Отдельного фонового цикла нет
    специально — ТЗ требует минимум фоновых задач, а временные файлы бот
    создаёт только сам и может подчистить их при следующем запуске."""
    if not os.path.isdir(TEMP_DIR):
        os.makedirs(TEMP_DIR, exist_ok=True)
        return
    for name in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
