import json

_cache: dict[str, dict] = {}


def load(path: str) -> dict:
    """Читаем файл один раз и кэшируем в памяти — при последующих обращениях
    не парсим JSON заново. Кэш конкретного файла сбрасывается функцией save()."""
    if path not in _cache:
        with open(path, "r", encoding="utf-8") as f:
            _cache[path] = json.load(f)
    return _cache[path]


def save(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _cache[path] = data
