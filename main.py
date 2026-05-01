import os
import asyncio
import logging

import discord
from dotenv import load_dotenv
from discord.ext import commands

from config import POS_AI_MODEL, POS_AI_PROVIDER
from storage import init_db
from utils import sanitize_discord_token

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def create_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

    try:
        import audioop  # noqa
    except Exception:
        discord.VoiceClient = None

    return bot


async def main():
    load_dotenv()
    await init_db()

    bot = create_bot()

    raw_token = os.getenv("DISCORD_TOKEN")
    token = sanitize_discord_token(raw_token)

    if not token:
        print("🚨 ОШИБКА: DISCORD_TOKEN не найден в переменных окружения Railway!")
        return

    async with bot:
        await bot.load_extension("cogs.general")
        await bot.load_extension("cogs.mod")
        await bot.load_extension("cogs.forms")
        await bot.load_extension("cogs.logging_events")
        await bot.load_extension("cogs.ai_chat")
        
        try:
            print(f"⏳ Запуск бота... AI provider={POS_AI_PROVIDER}, model={POS_AI_MODEL}")
            await bot.start(token)
        except discord.errors.LoginFailure:
            print("🚨 ОШИБКА: Неверный токен (Improper token). Проверь DISCORD_TOKEN в Railway.")
        except Exception as e:
            print(f"🚨 Произошла критическая ошибка при запуске: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
