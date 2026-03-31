import os

import discord
from discord.ext import commands

from commands import register_commands
from events import register_events
from storage import init_db
from utils import sanitize_discord_token


def create_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="!", intents=intents)

    try:
        import audioop  # noqa
    except Exception:
        discord.VoiceClient = None

    return bot


def main():
    init_db()

    bot = create_bot()
    register_commands(bot)
    register_events(bot)

    raw_token = os.getenv("DISCORD_TOKEN")
    token = sanitize_discord_token(raw_token)

    if not token:
        print("🚨 ОШИБКА: DISCORD_TOKEN не найден в переменных окружения Railway!")
        return

    try:
        print("⏳ Запуск бота...")
        bot.run(token)
    except discord.errors.LoginFailure:
        print("🚨 ОШИБКА: Неверный токен (Improper token). Проверь DISCORD_TOKEN в Railway.")
    except Exception as e:
        print(f"🚨 Произошла критическая ошибка при запуске: {e}")


if __name__ == "__main__":
    main()
