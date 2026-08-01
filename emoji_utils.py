def extract_custom_emoji_id(message) -> str | None:
    """
    Ищет premium (custom) эмодзи в тексте или подписи сообщения и возвращает
    его custom_emoji_id. Админу не нужно знать ID вручную — он просто
    отправляет эмодзи, бот сам его находит.
    """
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == "custom_emoji":
            return entity.custom_emoji_id
    return None


def extract_html_text(message) -> str:
    """
    Достаёт текст сообщения (или подпись к медиа) в виде HTML — это сохраняет
    жирный/курсив/ссылки и <tg-emoji> premium-эмодзи, чтобы админ мог один раз
    прислать готовый форматированный текст, а бот сохранил его как есть.
    """
    if message.text:
        return message.html_text
    if message.caption:
        return message.caption_html
    return ""
