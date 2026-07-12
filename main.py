import asyncio
import logging
import os
import signal

import discord
from dotenv import load_dotenv
from discord.ext import commands

# Config modules read env vars at import time, so local .env must be loaded
# before importing config/storage.
load_dotenv()

from config import BOT_COMMAND_PREFIX, POS_AI_MODEL, POS_AI_PROVIDER  # noqa: E402
from storage import init_db  # noqa: E402
from utils import sanitize_discord_token  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Cogs to load in order. Each must expose an async setup(bot) function.
COGS = [
    "cogs.general",
    "cogs.forms",
    "cogs.security",
    "cogs.logging_events",
    "cogs.mod",
    "cogs.ai_chat",
]


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.presences = False
    intents.typing = False
    bot = commands.Bot(
        command_prefix=BOT_COMMAND_PREFIX,
        intents=intents,
        case_insensitive=True,
        help_command=None,
    )
    return bot


async def run_bot() -> None:
    # init_db теперь async — вызываем через await
    await init_db()

    bot = create_bot()

    # Загружаем все коги до старта бота
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"✅ Cog loaded: {cog}")
        except Exception as e:
            logger.error(f"❌ Failed to load cog {cog}: {e}", exc_info=True)
            await bot.close()
            raise RuntimeError(f"Required cog failed to load: {cog}") from e

    raw_token = os.getenv("DISCORD_TOKEN")
    token = sanitize_discord_token(raw_token)

    if not token:
        raise RuntimeError("DISCORD_TOKEN не найден в переменных окружения Railway")

    # Graceful shutdown: ловим SIGTERM (Railway) и SIGINT (Ctrl+C).
    # Повторный сигнал не должен запускать второй бэкап/close — держим ссылку
    # на задачу и выполняем shutdown ровно один раз.
    loop = asyncio.get_running_loop()
    shutdown_task: asyncio.Task | None = None

    async def _shutdown() -> None:
        print("⚠️ Получен сигнал завершения, закрываем бота...")
        try:
            # Сбрасываем накопленную память P.OS в БД до бэкапа.
            from pos_ai import flush_ai_memory
            await flush_ai_memory()
        except Exception as e:
            print(f"Ошибка сброса памяти P.OS перед остановкой: {e}")
        try:
            from storage import backup_db_to_discord
            await backup_db_to_discord(bot)
        except Exception as e:
            print(f"Ошибка сохранения бэкапа перед остановкой: {e}")
        try:
            from storage import close_all_connections
            await close_all_connections()
        except Exception as e:
            print(f"Ошибка закрытия соединений с БД: {e}")
        await bot.close()

    def _on_signal() -> None:
        nonlocal shutdown_task
        if shutdown_task is None or shutdown_task.done():
            shutdown_task = asyncio.create_task(_shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler — молча игнорируем
            pass

    try:
        print(f"⏳ Запуск бота... AI provider={POS_AI_PROVIDER}, model={POS_AI_MODEL}")
        # Login authenticates the HTTP client without opening the gateway. This
        # lets us restore persistent state before Discord starts dispatching
        # messages, joins, moderation and AI events to cogs.
        await bot.login(token)

        from storage import restore_db_from_discord

        restore_result: bool | None = None
        for attempt, delay in enumerate((0.0, 2.0, 5.0), start=1):
            if delay:
                await asyncio.sleep(delay)
            restore_result = await restore_db_from_discord(bot)
            if restore_result is not None:
                break
            logger.warning("Database restore attempt %s/3 failed safely.", attempt)

        if restore_result is None:
            raise RuntimeError(
                "Не удалось безопасно проверить/восстановить БД после трёх попыток; "
                "gateway не запущен, чтобы не работать поверх неверного состояния"
            )
        if restore_result:
            from guild_config import invalidate
            from pos_ai import reset_ai_runtime_caches_after_restore

            invalidate()
            reset_ai_runtime_caches_after_restore()
            logger.info("Database restored and runtime caches invalidated before gateway start.")

        import antiraid
        from storage import get_active_raid_states

        restored_raid_modes = antiraid.restore_raid_modes(await get_active_raid_states())
        if restored_raid_modes:
            logger.warning(
                "Restored %s active anti-raid mode(s) before gateway start.",
                restored_raid_modes,
            )

        if not bot.is_closed():
            await bot.connect(reconnect=True)
    except discord.errors.LoginFailure:
        logger.critical("Неверный DISCORD_TOKEN.")
        raise
    except asyncio.CancelledError:
        print("✅ Бот остановлен корректно.")
        raise
    except Exception as e:
        logger.critical("Критическая ошибка при запуске: %s", e, exc_info=True)
        raise
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
