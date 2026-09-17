import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

import handlers
from config import config
from middlewares import SubGate
from vk_client import VK


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    if not config.BOT_TOKEN:
        raise RuntimeError("❌ BOT_TOKEN не задан (переменные окружения)")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    http = aiohttp.ClientSession(headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
    })
    vk = VK(config.VK_TOKEN, config.VK_VERSION) if config.VK_TOKEN else None

    dp["http"] = http
    dp["vk"] = vk
    dp.include_router(handlers.router)

    dp.message.outer_middleware(SubGate())
    dp.callback_query.outer_middleware(SubGate())

    await bot.set_my_commands([
        BotCommand(command="start", description="🎛 Запустить бота"),
        BotCommand(command="menu", description="📂 Главное меню"),
    ])

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("🚀 Бот запущен")
        await dp.start_polling(bot)
    finally:
        await http.close()
        if vk:
            await vk.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("👋 Бот остановлен")
