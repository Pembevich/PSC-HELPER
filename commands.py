from __future__ import annotations

import os
import shutil
import tempfile
import uuid

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
)
from logging_utils import send_log_embed
from utils import collect_runtime_health

sbor_channels: dict[int, int] = {}
GIF_MAX_DIMENSION = 640
GIF_MAX_VIDEO_SECONDS = 6
GIF_MIN_VIDEO_FPS = 8
GIF_MAX_VIDEO_FPS = 12
GIF_IMAGE_FRAME_MS = 700


def _normalize_attachment_extension(attachment: discord.Attachment) -> str:
    filename = (attachment.filename or "").lower()
    ext = os.path.splitext(filename)[1].lower().strip(".")
    if ext:
        return ext
    content_type = (attachment.content_type or "").lower()
    if content_type.startswith("image/"):
        guessed = content_type.split("/", 1)[1]
        return "jpg" if guessed == "jpeg" else guessed
    if content_type.startswith("video/"):
        return content_type.split("/", 1)[1]
    return ""


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    filename = (attachment.filename or "").lower()
    return content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"))


def _normalize_image_frame(path: str, canvas_size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as source:
        frame = ImageOps.exif_transpose(source)
        if getattr(frame, "is_animated", False):
            frame.seek(0)
        frame = frame.convert("RGBA")
        contained = ImageOps.contain(frame, canvas_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        offset = ((canvas_size[0] - contained.width) // 2, (canvas_size[1] - contained.height) // 2)
        canvas.paste(contained, offset, contained)
        return canvas


def _build_gif_from_images(image_paths: list[str], output_path: str):
    target_width = 1
    target_height = 1

    for path in image_paths:
        with Image.open(path) as source:
            frame = ImageOps.exif_transpose(source)
            if getattr(frame, "is_animated", False):
                frame.seek(0)
            frame = frame.convert("RGBA")
            if max(frame.size) > GIF_MAX_DIMENSION:
                frame.thumbnail((GIF_MAX_DIMENSION, GIF_MAX_DIMENSION), Image.Resampling.LANCZOS)
            target_width = max(target_width, frame.width)
            target_height = max(target_height, frame.height)

    frames = [_normalize_image_frame(path, (target_width, target_height)) for path in image_paths]
    if len(frames) == 1:
        frames.append(frames[0].copy())

    frames[0].save(
        output_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=GIF_IMAGE_FRAME_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )


def _build_gif_from_video(video_path: str, output_path: str):
    if not VideoFileClip:
        raise RuntimeError("moviepy недоступен в окружении")

    with VideoFileClip(video_path) as source_clip:
        if not source_clip.duration or source_clip.duration <= 0:
            raise RuntimeError("видео не удалось прочитать")

        render_clip = source_clip.subclipped(0, min(float(source_clip.duration), GIF_MAX_VIDEO_SECONDS))
        clips_to_close = [render_clip] if render_clip is not source_clip else []
        try:
            if vfx and max(render_clip.size) > GIF_MAX_DIMENSION:
                if render_clip.w >= render_clip.h:
                    resized_clip = render_clip.with_effects([vfx.Resize(width=GIF_MAX_DIMENSION)])
                else:
                    resized_clip = render_clip.with_effects([vfx.Resize(height=GIF_MAX_DIMENSION)])
                if resized_clip is not render_clip:
                    clips_to_close.append(resized_clip)
                render_clip = resized_clip

            fps = int(round(render_clip.fps or GIF_MIN_VIDEO_FPS))
            fps = max(GIF_MIN_VIDEO_FPS, min(GIF_MAX_VIDEO_FPS, fps))
            render_clip.write_gif(output_path, fps=fps, loop=0, logger=None)
        finally:
            for clip_to_close in reversed(clips_to_close):
                try:
                    clip_to_close.close()
                except Exception:
                    pass


async def generate_gif_from_attachments(attachments: list[discord.Attachment]) -> tuple[str, str]:
    image_files: list[str] = []
    video_files: list[str] = []
    temp_dir = tempfile.mkdtemp(prefix="psc-gif-")

    try:
        for attachment in attachments:
            ext = _normalize_attachment_extension(attachment)
            if ext not in ["jpg", "jpeg", "png", "webp", "bmp", "heic", "gif", "mp4", "mov", "webm", "avi", "mkv"]:
                continue
            unique_name = f"{uuid.uuid4().hex}.{ext or 'bin'}"
            file_path = os.path.join(temp_dir, unique_name)
            await attachment.save(file_path)

            if ext in ["jpg", "jpeg", "png", "webp", "bmp", "heic", "gif"]:
                image_files.append(file_path)
            elif ext in ["mp4", "mov", "webm", "avi", "mkv"]:
                video_files.append(file_path)

        output_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.gif")
        if image_files:
            _build_gif_from_images(image_files, output_path)
        elif video_files:
            _build_gif_from_video(video_files[0], output_path)
        else:
            raise RuntimeError("Не нашёл подходящих вложений для GIF (нужны изображения или короткое видео).")

        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def register_commands(bot: commands.Bot):
    @bot.command(name="gif")
    async def gif(ctx: commands.Context):
        if not ctx.message.attachments:
            await ctx.send("Пожалуйста, прикрепи изображение или видео к команде.")
            return

        try:
            output_path, temp_dir = await generate_gif_from_attachments(ctx.message.attachments)
            await ctx.send(file=discord.File(output_path, filename="psc.gif"))
        except Exception as e:
            await ctx.send(f"❌ Ошибка при создании GIF: {e}")
            return
        finally:
            if "temp_dir" in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

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
        embed.add_field(name="P.OS Core", value="`operational`", inline=True)
        embed.add_field(name="P.OS Profile", value="`PSC-2058`", inline=True)
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
        from pos_ai import ask_pos

        image_urls = [attachment.url for attachment in ctx.message.attachments if _is_image_attachment(attachment)]
        async with ctx.typing():
            reply = await ask_pos(question, image_urls=image_urls, author_name=ctx.author.display_name)

        if not reply:
            await ctx.send("P.OS сейчас молчит как сервер в понедельник утром. Проверь AI-ключ и модель в Railway.")
            return

        chunks = [reply[i:i + 1900] for i in range(0, len(reply), 1900)]
        for chunk in chunks:
            await ctx.send(chunk, allowed_mentions=discord.AllowedMentions.none())
