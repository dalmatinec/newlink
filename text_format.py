SEPARATOR_EMOJI = "➖"
SEPARATOR_COUNT = 6


def separator(count: int = SEPARATOR_COUNT) -> str:
    """Разделитель текста рядом эмодзи ➖ вместо графических линий/тире."""
    return SEPARATOR_EMOJI * count
