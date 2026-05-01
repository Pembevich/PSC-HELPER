import asyncio
import os
import signal

import discord
from dotenv import load_dotenv
from discord.ext import commands

from commands import register_commands
from config import POS_AI_MODEL, POS_AI_PROVIDER
from events import register_events
from storage import init_db
from utils import sanitize_discord_token


def create_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

    try:
        import audioop  # noqa
    except Exception:
        discord.VoiceClient = None

    return bot


async def run_bot() -> None:
    load_dotenv()

    # init_db теперь async — вызываем через await
    await init_db()

    bot = create_bot()
    register_commands(bot)
    register_events(bot)

    raw_token = os.getenv("DISCORD_TOKEN")
    token = sanitize_discord_token(raw_token)

    if not token:
        print("🚨 ОШИБКА: DISCORD_TOKEN не найден в переменных окружения Railway!")
        return

    # Graceful shutdown: ловим SIGTERM (Railway) и SIGINT (Ctrl+C)
    loop = asyncio.get_running_loop()

    async def _shutdown() -> None:
        print("⚠️ Получен сигнал завершения, закрываем бота...")
        await bot.close()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler — молча игнорируем
            pass

    try:
        print(f"⏳ Запуск бота... AI provider={POS_AI_PROVIDER}, model={POS_AI_MODEL}")
        await bot.start(token)
    except discord.errors.LoginFailure:
        print("🚨 ОШИБКА: Неверный токен (Improper token). Проверь DISCORD_TOKEN в Railway.")
    except asyncio.CancelledError:
        print("✅ Бот остановлен корректно.")
    except Exception as e:
        print(f"🚨 Произошла критическая ошибка при запуске: {e}")
    finally:
        if not bot.is_closed():
            await bot.close()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
