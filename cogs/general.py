from __future__ import annotations

import shutil
import logging

import discord
from discord import Color
from discord.ext import commands, tasks

from config import (
    allowed_guild_ids,
    UPDATE_LOG_CHANNEL_ID,
    UPDATE_LOG_MARKER,
)
from logging_utils import send_log_embed, ensure_log_category_and_channels
from utils import collect_runtime_health

# Из commands.py
from commands import (
    generate_gif_from_attachments,
    parse_gif_options_from_text,
)

logger = logging.getLogger(__name__)

async def _send_update_log_if_needed(bot: commands.Bot):
    if not UPDATE_LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(UPDATE_LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        async for existing in channel.history(limit=20):
            if existing.author.id == bot.user.id and UPDATE_LOG_MARKER in (existing.content or ""):
                return
    except Exception:
        return

    release_message = (
        f"[{UPDATE_LOG_MARKER}]\n"
        "Лог обновления P.S.C Helper — версия 0.8 «ECOSYSTEM REACTIVATION»:\n"
        "- Пумба закреплён как создатель и абсолютный владелец P.OS с полным доступом;\n"
        "- P.OS управляет сервером через ИИ полностью: роли, каналы, права, наказания, рассылка, настройки;\n"
        "- кросс-серверность: владелец управляет любым сервером P.OS, не находясь на нём;\n"
        "- владелец может попросить P.OS покинуть сервер или полностью остановиться (с подтверждением в ЛС);\n"
        "- приоритет API на Gemini; остальные провайдеры — только при его недоступности;\n"
        "- усилена защита от джейлбрейков и от раскрытия API-ключей/системного промпта;\n"
        "- улучшено распознавание контекста: кто что написал, упоминания, ответы — без галлюцинаций;\n"
        "- БЕЗОПАСНОСТЬ: новый антирейд (массовый заход аккаунтов → kick/ban/alert/lockdown);\n"
        "- многослойная модерация: масс-пинг, кросс-канальный спам, флуд + ИИ-второе мнение (Gemini);\n"
        "- скрининг заходов (возраст аккаунта, аватар, подозрительный ник) и оповещение владельца;\n"
        "- маты и оскорбления по-прежнему разрешены; все пороги настраиваются через ИИ по серверам."
    )

    try:
        await channel.send(release_message, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        logger.error(f"Failed to send update log: {e}", exc_info=True)


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # #2: on_ready срабатывает при КАЖDOM реконнекте. Восстановление БД
        # должно выполняться один раз за процесс, иначе свежие данные затираются
        # последним бэкапом после любого обрыва соединения.
        self._restored_once = False
        self.db_backup_task.start()

    def cog_unload(self):
        self.db_backup_task.cancel()

    @tasks.loop(minutes=10)
    async def db_backup_task(self):
        await self.bot.wait_until_ready()
        try:
            from storage import backup_db_to_discord
            await backup_db_to_discord(self.bot)
        except Exception as e:
            logger.error(f"Error backing up DB in task: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"✅ Бот запущен как {self.bot.user} (id: {self.bot.user.id})")

        # #2: восстанавливаем БД только один раз за процесс, а не на каждый reconnect.
        if not self._restored_once:
            self._restored_once = True
            try:
                from storage import restore_db_from_discord
                did_restore = await restore_db_from_discord(self.bot)
                if did_restore:
                    logger.info("✅ База данных успешно восстановлена из Discord резервной копии.")
                else:
                    logger.info("ℹ️ Резервная копия базы данных не найдена на Discord. Сессия с чистого листа.")
            except Exception as e:
                logger.error(f"Ошибка при восстановлении базы данных: {e}", exc_info=True)
        runtime = collect_runtime_health()
        missing_optional = [k for k, available in runtime.items() if not available and k != "DISCORD_TOKEN"]
        if missing_optional:
            logger.warning(f"⚠️ Не настроены опциональные ключи: {', '.join(missing_optional)}")

        for guild in self.bot.guilds:
            if allowed_guild_ids and guild.id not in allowed_guild_ids:
                continue
            try:
                await ensure_log_category_and_channels(guild)
            except Exception as e:
                logger.error(f"Не удалось создать каналы логов: {e}", exc_info=True)

        for guild_id in allowed_guild_ids:
            try:
                guild = discord.Object(id=guild_id)
                await self.bot.tree.sync(guild=guild)
                logger.info(f"✅ Команды синхронизированы с сервером {guild_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка при синхронизации: {e}", exc_info=True)

        try:
            for guild in self.bot.guilds:
                await send_log_embed(
                    guild,
                    "server",
                    "✅ Бот запущен",
                    f"Бот запущен как {self.bot.user}.",
                    color=Color.green()
                )
        except Exception:
            pass



    @commands.command(name="gif")
    async def gif(self, ctx: commands.Context, *, args: str = ""):
        attachments = list(ctx.message.attachments)
        if not attachments and ctx.message.reference and ctx.message.reference.message_id:
            try:
                ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                attachments = list(ref_msg.attachments)
            except Exception:
                pass

        if not attachments:
            await ctx.send("Пожалуйста, прикрепи изображение или видео к команде (или ответь на сообщение с ними).")
            return

        options = parse_gif_options_from_text(args or ctx.message.content)
        duration = options.get("duration")
        fps = options.get("fps")
        max_video_seconds = options.get("max_video_seconds")

        try:
            output_path, temp_dir = await generate_gif_from_attachments(
                attachments,
                duration=duration,
                fps=fps,
                max_video_seconds=max_video_seconds,
            )
            await ctx.send(file=discord.File(output_path, filename="psc.gif"))
        except Exception as e:
            logger.error(f"Error generating GIF: {e}", exc_info=True)
            await ctx.send(f"❌ Ошибка при создании GIF: {e}")
            return
        finally:
            if "temp_dir" in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
