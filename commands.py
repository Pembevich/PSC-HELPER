from __future__ import annotations

import os
import uuid

import discord
from discord import Embed, Color
from discord.ext import commands
from moviepy.editor import VideoFileClip, ImageSequenceClip

from config import (
    allowed_guild_ids,
    allowed_role_ids,
)
from logging_utils import send_log_embed
from utils import collect_runtime_health

sbor_channels: dict[int, int] = {}


def register_commands(bot: commands.Bot):
    @bot.command(name="gif")
    async def gif(ctx: commands.Context):
        if not ctx.message.attachments:
            await ctx.send("Пожалуйста, прикрепи изображение или видео к команде.")
            return

        image_files = []
        video_files = []
        os.makedirs("temp", exist_ok=True)

        for attachment in ctx.message.attachments:
            filename = attachment.filename
            ext = os.path.splitext(filename)[1].lower().strip(".")
            unique_name = f"{uuid.uuid4().hex}.{ext}"
            file_path = os.path.join("temp", unique_name)
            await attachment.save(file_path)

            if ext in ["jpg", "jpeg", "png", "webp", "bmp", "heic"]:
                image_files.append(file_path)
            elif ext in ["mp4", "mov", "webm", "avi", "mkv"]:
                video_files.append(file_path)
            else:
                await ctx.send(f"❌ Файл `{filename}` не поддерживается.")
                os.remove(file_path)
                return

        output_path = f"temp/{uuid.uuid4().hex}.gif"
        try:
            if image_files:
                clip = ImageSequenceClip(image_files, fps=1)
                clip.write_gif(output_path, fps=1)
            elif video_files:
                clip = VideoFileClip(video_files[0])
                clip = clip.subclip(0, min(5, clip.duration))
                clip.write_gif(output_path)
            else:
                await ctx.send("❌ Не удалось обработать вложения.")
                return

            await ctx.send(file=discord.File(output_path))
        except Exception as e:
            await ctx.send(f"❌ Ошибка при создании GIF: {e}")
        finally:
            for f in image_files + video_files:
                if os.path.exists(f):
                    os.remove(f)
            if os.path.exists(output_path):
                os.remove(output_path)

    @bot.command(name="health")
    async def health(ctx: commands.Context):
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
        embed.add_field(name="Latency", value=f"`{round(bot.latency * 1000)}ms`", inline=False)
        await ctx.send(embed=embed)

    @bot.tree.command(name="sbor", description="Начать сбор: создаёт голосовой канал и пингует роль")
    @discord.app_commands.describe(role="Роль, которую нужно пинговать")
    async def sbor(interaction: discord.Interaction, role: discord.Role):
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
            avatar_url=bot.user.avatar.url if bot.user.avatar else None
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

    @bot.tree.command(name="sbor_end", description="Завершить сбор и удалить голосовой канал")
    async def sbor_end(interaction: discord.Interaction):
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
        await webhook.send(content="*Сбор окончен!*", username="Сбор", avatar_url=bot.user.avatar.url if bot.user.avatar else None)
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

    @bot.command()
    async def ai(ctx: commands.Context, *, question: str):
        await ctx.send("Команда !ai временно отключена.")
