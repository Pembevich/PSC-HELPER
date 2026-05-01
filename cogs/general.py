from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import asyncio
import logging

import discord
from PIL import Image, ImageOps
from discord import Embed, Color
from discord.ext import commands

try:
    from moviepy import VideoFileClip, vfx
except Exception:
    VideoFileClip = None
    vfx = None

from config import (
    allowed_guild_ids,
    allowed_role_ids,
    UPDATE_LOG_CHANNEL_ID,
    UPDATE_LOG_MARKER,
)
from logging_utils import send_log_embed, ensure_log_category_and_channels
from utils import collect_runtime_health

# Из commands.py
from commands import (
    generate_gif_from_attachments,
    _is_image_attachment,
    sbor_channels,
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
        "Лог обновления P.S.C Helper:\n"
        "- обновлён стек зависимостей и приведён в рабочее состояние под Railway;\n"
        "- P.OS переведён на GitHub Models через OpenAI-compatible endpoint;\n"
        "- команда !ai снова отвечает, а диалоговый P.OS использует тот же AI-клиент и тот же характер;\n"
        "- улучшена конфигурация через env-переменные для модели, промпта и таймингов;\n"
        "- сохранены и актуализированы фильтрация, GIF-генерация, логи и форма с \"ОТПИСКИ\";\n"
        "- добавлен aiosqlite и Cogs для оптимизации и масштабируемости."
    )

    try:
        await channel.send(release_message, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        logger.error(f"Failed to send update log: {e}", exc_info=True)


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"✅ Бот запущен как {self.bot.user} (id: {self.bot.user.id})")
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

        try:
            await _send_update_log_if_needed(self.bot)
        except Exception:
            pass

    @commands.command(name="gif")
    async def gif(self, ctx: commands.Context):
        if not ctx.message.attachments:
            await ctx.send("Пожалуйста, прикрепи изображение или видео к команде.")
            return

        try:
            output_path, temp_dir = await generate_gif_from_attachments(ctx.message.attachments)
            await ctx.send(file=discord.File(output_path, filename="psc.gif"))
        except Exception as e:
            logger.error(f"Error generating GIF: {e}", exc_info=True)
            await ctx.send(f"❌ Ошибка при создании GIF: {e}")
            return
        finally:
            if "temp_dir" in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

    @commands.command(name="health")
    async def health(self, ctx: commands.Context):
        runtime = collect_runtime_health()
        status_lines = [
            f"`{name}`: {'✅ OK' if enabled else '⚠️ отсутствует'}"
            for name, enabled in runtime.items()
        ]

        embed = Embed(
            title="Состояние бота",
            description="\n".join(status_lines),
            color=Color.green() if runtime["DISCORD_TOKEN"] else Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Latency", value=f"`{round(self.bot.latency * 1000)}ms`", inline=False)
        embed.add_field(name="P.OS Core", value="`operational`", inline=True)
        embed.add_field(name="P.OS Profile", value="`PSC-2058`", inline=True)
        await ctx.send(embed=embed)

    @discord.app_commands.command(name="sbor", description="Начать сбор: создаёт голосовой канал и пингует роль")
    @discord.app_commands.describe(role="Роль, которую нужно пинговать")
    async def sbor(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild.id not in allowed_guild_ids:
            await interaction.response.send_message("❌ Команда недоступна на этом сервере.", ephemeral=True)
            return

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not any(r.id in allowed_role_ids for r in member.roles):
            await interaction.response.send_message("❌ У тебя нет прав для этой команды.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        existing = discord.utils.get(interaction.guild.voice_channels, name="сбор")
        if existing:
            await interaction.followup.send("❗ Канал 'сбор' уже существует.")
            return

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(connect=False),
            role: discord.PermissionOverwrite(connect=True, view_channel=True)
        }

        category = interaction.channel.category
        voice_channel = await interaction.guild.create_voice_channel("Сбор", overwrites=overwrites, category=category)
        sbor_channels[interaction.guild.id] = voice_channel.id

        webhook = await interaction.channel.create_webhook(name="Сбор")
        await webhook.send(
            content=f"**Сбор! {role.mention}. Заходите в <#{voice_channel.id}>!**",
            username="Сбор",
            avatar_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        await webhook.delete()

        try:
            await send_log_embed(
                interaction.guild,
                "commands",
                "📣 Сбор создан",
                f"{interaction.user.mention} создал сбор.",
                color=Color.blue(),
                fields=[
                    ("Роль", role.mention, False),
                    ("Канал", voice_channel.mention, False)
                ]
            )
        except Exception:
            pass
        await interaction.followup.send("✅ Сбор создан!")

    @discord.app_commands.command(name="sbor_end", description="Завершить сбор и удалить голосовой канал")
    async def sbor_end(self, interaction: discord.Interaction):
        if interaction.guild.id not in allowed_guild_ids:
            await interaction.response.send_message("❌ Команда недоступна на этом сервере.", ephemeral=True)
            return

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not any(r.id in allowed_role_ids for r in member.roles):
            await interaction.response.send_message("❌ У тебя нет прав для этой команды.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        channel_id = sbor_channels.get(interaction.guild.id)
        if not channel_id:
            await interaction.followup.send("❗ Канал 'сбор' не найден.")
            return

        channel = interaction.guild.get_channel(channel_id)
        if channel:
            await channel.delete()

        webhook = await interaction.channel.create_webhook(name="Сбор")
        await webhook.send(content="*Сбор окончен!*", username="Сбор", avatar_url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        await webhook.delete()
        sbor_channels.pop(interaction.guild.id, None)

        try:
            await send_log_embed(
                interaction.guild,
                "commands",
                "🧹 Сбор завершён",
                f"{interaction.user.mention} завершил сбор.",
                color=Color.orange()
            )
        except Exception:
            pass
        await interaction.followup.send("✅ Сбор завершён.")

async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
