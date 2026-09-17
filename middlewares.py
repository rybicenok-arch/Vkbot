import logging
import time
from typing import Optional

from aiogram import BaseMiddleware
from aiogram.types import (CallbackQuery, InlineKeyboardButton as Btn,
                           InlineKeyboardMarkup, Message)

from config import config
from store import channels
from utils import esc

log = logging.getLogger(__name__)

sub_cache: dict = {}        # user_id -> (ok, ts)
_CACHE_TTL = 180
_last_prompt: dict = {}
_PROMPT_TTL = 6


async def is_subscribed(bot, user_id: int) -> bool:
    if not channels.items:
        return True
    now = time.time()
    cached = sub_cache.get(user_id)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    ok = True
    for ch in channels.items:
        try:
            member = await bot.get_chat_member(ch, user_id)
        except Exception as e:
            log.warning("get_chat_member(%s) failed: %r", ch, e)
            ok = False  # бот не админ канала / канал не найден
            break
        if member.status not in ("member", "administrator", "creator"):
            ok = False
            break
    sub_cache[user_id] = (ok, now)
    return ok


async def send_subscribe(bot, chat_id: int, user_id: int) -> None:
    rows, missed = [], []
    for ch in channels.items:
        title, url = str(ch), None
        try:
            chat = await bot.get_chat(ch)
            title = chat.title or title
            if chat.username:
                url = "https://t.me/" + chat.username.lstrip("@")
            elif chat.invite_link:
                url = chat.invite_link
        except Exception:
            pass
        if url:
            rows.append([Btn(text=f"📢 {title}"[:60], url=url)])
        else:
            missed.append(title)
    rows.append([Btn(text="✅ Я подписался — проверить", callback_data="sub:check")])

    text = "🔒 <b>Доступ только для подписчиков</b>\n\n" \
           "Подпишись на канал(ы) 👆, потом нажми «Проверить».\n\n"
    if missed:
        text += "Каналы без ссылки: " + ", ".join(esc(n) for n in missed) + "\n\n"
    text += ("<i>Уже подписан, но не пускает? Проверь, что бот — админ канала.</i>")
    await bot.send_message(chat_id, text,
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


class SubGate(BaseMiddleware):
    """Пускает к боту только подписчиков (админов — всегда)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.id in config.ADMIN_IDS:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "sub:check":
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)

        bot = data.get("bot")
        if await is_subscribed(bot, user.id):
            return await handler(event, data)

        chat_id: Optional[int] = None
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("🔒 Сначала подпишись на канал!", show_alert=True)
            except Exception:
                pass
            if event.message is not None:
                chat_id = event.message.chat.id
        elif isinstance(event, Message):
            chat_id = event.chat.id

        now = time.time()
        if chat_id and now - _last_prompt.get(user.id, 0) > _PROMPT_TTL:
            _last_prompt[user.id] = now
            try:
                await send_subscribe(bot, chat_id, user.id)
            except Exception as e:
                log.warning("send_subscribe failed: %r", e)
        return None
