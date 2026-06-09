from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import asyncio
import re
import subprocess
from typing import Any
import imageio_ffmpeg

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


def parse_gif_options_from_text(text: str) -> dict[str, Any]:
    options = {}
    if not text:
        return options
    
    text_lower = text.lower().strip()
    
    # 1. Search for FPS: fps=12, фпс=12, 12fps, 12фпс, 12 кадров в секунду, etc.
    fps_match = re.search(r'(?:fps|фпс|кадров\s*(?:в|\/)\s*сек(?:унду)?)\s*[:=]?\s*(\d+)|(\d+)\s*(?:fps|фпс)', text_lower)
    if fps_match:
        val = fps_match.group(1) or fps_match.group(2)
        try:
            val_i = int(val)
            if 1 <= val_i <= 30:
                options['fps'] = val_i
        except ValueError:
            pass
            
    # 2. Search for Delay/Duration in ms: delay=300, задержка=300, 300ms, 300мс, 300 ms, etc.
    delay_match = re.search(r'(?:delay|ms|мс|задержк[аи])\s*[:=]?\s*(\d+)|(\d+)\s*(?:ms|мс|миллисекунд)', text_lower)
    if delay_match:
        val = delay_match.group(1) or delay_match.group(2)
        try:
            val_i = int(val)
            if 10 <= val_i <= 5000:
                options['duration'] = val_i
        except ValueError:
            pass
            
    # 3. Search for Duration in seconds: 0.5s, 0.5сек, 1.5с, 1.5 сек, etc.
    sec_match = re.search(r'(?:duration|sec|сек|время)\s*[:=]?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:s|sec|сек|с)(?!\w)', text_lower)
    if sec_match:
        val = sec_match.group(1) or sec_match.group(2)
        try:
            val_f = float(val)
            if 0.1 <= val_f <= 15.0:
                options['max_video_seconds'] = val_f
                options['duration'] = int(val_f * 1000)
        except ValueError:
            pass
            
    # 4. Search for standalone numbers if no key-value matched
    if 'fps' not in options and 'duration' not in options:
        # find any float/integer in the text
        # e.g., "!gif 0.5" or "!gif 12"
        numbers = re.findall(r'\b\d+(?:\.\d+)?\b', text_lower)
        for num in numbers:
            try:
                val = float(num)
                # If it's a number like 0.1 to 5.0, it's likely a frame duration in seconds
                if 0.1 <= val < 5.0:
                    options['duration'] = int(val * 1000)
                    options['max_video_seconds'] = val
                    break
                # If it's an integer between 5 and 30, it's likely FPS
                elif 5 <= val <= 30 and val.is_integer():
                    options['fps'] = int(val)
                    break
                # If it's between 50 and 3000, it's likely milliseconds delay
                elif 50 <= val <= 3000:
                    options['duration'] = int(val)
                    break
            except ValueError:
                pass
                
    return options


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


def _build_gif_from_images(image_paths: list[str], output_path: str, *, duration: int | None = None):
    actual_duration = duration if duration is not None else GIF_IMAGE_FRAME_MS

    # Special case: if there is only 1 image and it is an animated GIF,
    # extract all its frames to allow resizing/speed modifications.
    if len(image_paths) == 1 and image_paths[0].lower().endswith(".gif"):
        frames = []
        try:
            with Image.open(image_paths[0]) as img:
                # Read original frame duration if not specified
                gif_duration = img.info.get("duration", GIF_IMAGE_FRAME_MS)
                if duration is None:
                    actual_duration = gif_duration
                
                for frame_idx in range(getattr(img, "n_frames", 1)):
                    img.seek(frame_idx)
                    frame = ImageOps.exif_transpose(img)
                    frame = frame.convert("RGBA")
                    if max(frame.size) > GIF_MAX_DIMENSION:
                        frame.thumbnail((GIF_MAX_DIMENSION, GIF_MAX_DIMENSION), Image.Resampling.LANCZOS)
                    frames.append(frame.copy())
        except Exception:
            frames = []

        if len(frames) > 1:
            frames[0].save(
                output_path,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=actual_duration,
                loop=0,
                optimize=True,
                disposal=2,
            )
            return

    # Standard slideshow logic
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
        duration=actual_duration,
        loop=0,
        optimize=True,
        disposal=2,
    )


def _build_gif_from_video(
    video_path: str,
    output_path: str,
    *,
    fps: int | None = None,
    max_duration: float | None = None,
    max_dim: int = GIF_MAX_DIMENSION,
):
    actual_duration = max_duration if max_duration is not None else GIF_MAX_VIDEO_SECONDS
    actual_fps = fps if fps is not None else 10

    # 1. Try ffmpeg first (highly optimized, fast, beautiful palette)
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        # scale logic: w=if(gt(iw,ih),640,-1), h=if(gt(iw,ih),-1,640)
        filter_str = (
            f"fps={actual_fps},"
            f"scale='if(gt(iw,ih),min(iw,{max_dim}),-1)':'if(gt(ih,iw),min(ih,{max_dim}))':flags=lanczos,"
            f"split[s0][s1];[s0]palettegen=stats_mode=single[p];[s1][p]paletteuse=dither=sierra2_4a"
        )
        
        cmd = [
            ffmpeg_exe,
            "-y",
            "-ss", "0",
            "-t", str(actual_duration),
            "-i", video_path,
            "-vf", filter_str,
            output_path
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return
    except Exception:
        pass

    # 2. Fallback using MoviePy
    if not VideoFileClip:
        raise RuntimeError("Ни ffmpeg, ни moviepy недоступны для конвертации видео.")

    with VideoFileClip(video_path) as source_clip:
        if not source_clip.duration or source_clip.duration <= 0:
            raise RuntimeError("видео не удалось прочитать")

        render_clip = source_clip.subclipped(0, min(float(source_clip.duration), actual_duration))
        clips_to_close = [render_clip] if render_clip is not source_clip else []
        try:
            if vfx and max(render_clip.size) > max_dim:
                if render_clip.w >= render_clip.h:
                    resized_clip = render_clip.with_effects([vfx.Resize(width=max_dim)])
                else:
                    resized_clip = render_clip.with_effects([vfx.Resize(height=max_dim)])
                if resized_clip is not render_clip:
                    clips_to_close.append(resized_clip)
                render_clip = resized_clip

            fps_val = int(round(render_clip.fps or GIF_MIN_VIDEO_FPS))
            fps_val = max(GIF_MIN_VIDEO_FPS, min(GIF_MAX_VIDEO_FPS, fps_val))
            if fps is not None:
                fps_val = fps
            render_clip.write_gif(output_path, fps=fps_val, loop=0, logger=None)
        finally:
            for clip_to_close in reversed(clips_to_close):
                try:
                    clip_to_close.close()
                except Exception:
                    pass


async def generate_gif_from_attachments(
    attachments: list[discord.Attachment],
    *,
    duration: int | None = None,
    fps: int | None = None,
    max_video_seconds: float | None = None,
) -> tuple[str, str]:
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
            await asyncio.to_thread(_build_gif_from_images, image_files, output_path, duration=duration)
        elif video_files:
            await asyncio.to_thread(
                _build_gif_from_video,
                video_files[0],
                output_path,
                fps=fps,
                max_duration=max_video_seconds,
            )
        else:
            raise RuntimeError("Не нашёл подходящих вложений для GIF (нужны изображения или короткое видео).")

        return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise



