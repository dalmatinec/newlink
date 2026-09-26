from aiogram.filters.callback_data import CallbackData


class OpenButton(CallbackData, prefix="open"):
    """Юзер нажал кнопку меню."""
    id: int


class Adm(CallbackData, prefix="adm"):
    """Любое действие в админке: act — что сделать, id/val — над чем."""
    act: str
    id: int = 0
    val: str = ""
