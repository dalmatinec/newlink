import time
from datetime import datetime, timedelta

from aiogram import Bot


async def generate_invite_link(bot: Bot, chat_id: str, mode: str, duration_minutes: int = 60) -> str:
    """
    mode = "request"     -> ссылка-заявка: creates_join_request=True, живёт
                             duration_minutes, входит по одобрению админа чата,
                             переходить может сколько угодно человек.
    mode = "single_use"  -> обычная invite-ссылка с member_limit=1: первый,
                             кто перешёл, входит сразу без одобрения, дальше
                             ссылка "сгорает" — Telegram сам её деактивирует.
    """
    if mode == "single_use":
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            name=f"single-{int(time.time())}",
        )
    else:
        expire_date = datetime.now() + timedelta(minutes=duration_minutes)
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=True,
            expire_date=expire_date,
            name=f"req-{int(time.time())}",
        )
    return link.invite_link
