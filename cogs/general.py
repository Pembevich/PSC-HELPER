from __future__ import annotations

import shutil
import logging

import discord
from discord import Color
from discord.ext import commands, tasks

from config import allowed_guild_ids
from logging_utils import send_log_embed, ensure_log_category_and_channels
from utils import collect_runtime_health

# Из commands.py
from commands import (
    format_gif_error_for_user,
    generate_gif_from_attachments,
    gif_output_limit_for_guild,
    parse_gif_options_from_text,
)
from message_gate import wait_for_moderation

logger = logging.getLogger(__name__)


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._commands_synced = False
        self._startup_logged_guild_ids: set[int] = set()
        self.db_backup_task.start()

    def cog_unload(self):
        self.db_backup_task.cancel()

    @tasks.loop(minutes=10)
    async def db_backup_task(self):
        await self.bot.wait_until_ready()
        try:
            # Сбрасываем накопленную память P.OS в БД, чтобы бэкап был актуальным.
            from pos_ai import flush_ai_memory
            await flush_ai_memory()
        except Exception:
            logger.exception("Error flushing AI memory before backup; this backup cycle is skipped.")
            return
        try:
            from storage import backup_db_to_discord
            await backup_db_to_discord(self.bot)
        except Exception:
            logger.exception("Error backing up DB in periodic task.")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"✅ Бот запущен как {self.bot.user} (id: {self.bot.user.id})")

        runtime = collect_runtime_health()
        missing_optional = [k for k, available in runtime.items() if not available and k != "DISCORD_TOKEN"]
        if runtime.get("AI_PROVIDER_CONFIGURED"):
            missing_optional = [
                key for key in missing_optional
                if key not in {"GITHUB_MODELS_TOKEN", "POS_AI_API_KEY", "POS_AI_PROVIDER_KEYS"}
            ]
        if missing_optional:
            logger.warning(f"⚠️ Не настроены опциональные ключи: {', '.join(missing_optional)}")

        for guild in self.bot.guilds:
            if allowed_guild_ids and guild.id not in allowed_guild_ids:
                continue
            try:
                await ensure_log_category_and_channels(guild)
            except Exception as e:
                logger.error(f"Не удалось обнаружить настроенные каналы логов: {e}", exc_info=True)

        if not self._commands_synced:
            sync_succeeded = True
            try:
                # Slash-команд больше нет. Пустая синхронизация удаляет старые
                # /sbor и /sbor_end из Discord после обновления.
                self.bot.tree.clear_commands(guild=None)
                await self.bot.tree.sync()
                guild_ids = {guild.id for guild in self.bot.guilds}
                guild_ids.update(allowed_guild_ids)
                for guild_id in sorted(guild_ids):
                    guild = discord.Object(id=guild_id)
                    self.bot.tree.clear_commands(guild=guild)
                    await self.bot.tree.sync(guild=guild)
                logger.info("✅ Устаревшие slash-команды удалены")
            except Exception as e:
                sync_succeeded = False
                logger.error("❌ Ошибка удаления slash-команд: %s", e, exc_info=True)
            self._commands_synced = sync_succeeded

        try:
            for guild in self.bot.guilds:
                if guild.id in self._startup_logged_guild_ids:
                    continue
                await send_log_embed(
                    guild,
                    "server",
                    "✅ Бот запущен",
                    f"Бот запущен как {self.bot.user}.",
                    color=Color.green()
                )
                self._startup_logged_guild_ids.add(guild.id)
        except Exception:
            logger.exception("Не удалось отправить журнал запуска P.OS.")

    @commands.command(name="gif")
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def gif(self, ctx: commands.Context, *, args: str = ""):
        moderation_result = await wait_for_moderation(ctx.message.id)
        if moderation_result is not False:
            if moderation_result is None:
                await ctx.send(
                    "Не удалось безопасно проверить сообщение. Попробуй ещё раз.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return

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
                max_output_bytes=gif_output_limit_for_guild(ctx.guild),
            )
            await ctx.send(file=discord.File(output_path, filename="psc.gif"))
        except Exception as exc:
            logger.error("Error generating GIF: %s", exc, exc_info=True)
            await ctx.send(
                f"❌ Ошибка при создании GIF: {format_gif_error_for_user(exc)}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        finally:
            if "temp_dir" in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

    @gif.error
    async def gif_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            try:
                await ctx.send(f"⏳ Не так быстро: попробуй через {error.retry_after:.0f} сек.")
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
