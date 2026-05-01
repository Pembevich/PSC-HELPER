from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import asyncio

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
            await asyncio.to_thread(_build_gif_from_images, image_files, output_path)
        elif video_files:
            await asyncio.to_thread(_build_gif_from_video, video_files[0], output_path)
        else:
            raise RuntimeError("Не нашёл подходящих вложений для GIF (нужны изображения или короткое видео).")

        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


