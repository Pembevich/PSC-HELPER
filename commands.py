from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import asyncio
import re
import subprocess
import time
from math import sqrt
from pathlib import Path
from typing import Any, cast

import imageio_ffmpeg

import discord
from PIL import Image, ImageOps

# #4: Защита от декомпрессионных бомб (см. moderation.py).
Image.MAX_IMAGE_PIXELS = 24_000_000

GIF_MAX_DIMENSION = 960
GIF_MAX_VIDEO_SECONDS = 8
GIF_MIN_VIDEO_FPS = 8
GIF_MAX_VIDEO_FPS = 24
GIF_DEFAULT_VIDEO_FPS = 20
GIF_IMAGE_FRAME_MS = 700
GIF_MAX_ATTACHMENT_BYTES = 60 * 1024 * 1024
GIF_MAX_TOTAL_BYTES = 80 * 1024 * 1024
GIF_DEFAULT_OUTPUT_BYTES = 8 * 1024 * 1024
GIF_MAX_OUTPUT_BYTES = 25 * 1024 * 1024
GIF_UPLOAD_HEADROOM_BYTES = 64 * 1024
GIF_MAX_ATTACHMENTS = 12
GIF_MAX_SOURCE_FRAMES = 120
GIF_MAX_PIXELS = 24_000_000
GIF_MAX_RENDERED_FRAME_PIXELS = 18_000_000
GIF_MAX_ENCODE_SECONDS = 90
GIF_ATTACHMENT_DOWNLOAD_SECONDS = 45
GIF_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
GIF_VIDEO_EXTENSIONS = {"mp4", "mov", "webm", "avi", "mkv"}
GIF_SAFE_PIL_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF"}
GIF_MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/x-msvideo": "avi",
    "video/x-matroska": "mkv",
}

# GIF encoding is CPU and disk heavy. One shared slot prevents a burst of users
# from exhausting the bot process while still allowing the event loop to work.
_gif_job_semaphore = asyncio.Semaphore(1)


def parse_gif_options_from_text(text: str) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if not text:
        return options
    
    text_lower = text.lower().strip()
    
    # 1. Search for FPS: fps=12, фпс=12, 12fps, 12фпс, 12 кадров в секунду, etc.
    fps_match = re.search(r'(?:fps|фпс|кадров\s*(?:в|\/)\s*сек(?:унду)?)\s*[:=]?\s*(\d+)|(\d+)\s*(?:fps|фпс)', text_lower)
    if fps_match:
        val = fps_match.group(1) or fps_match.group(2)
        try:
            val_i = int(val)
            if 1 <= val_i <= GIF_MAX_VIDEO_FPS:
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
        seconds_text = sec_match.group(1) or sec_match.group(2)
        try:
            val_f = float(seconds_text)
            if 0.1 <= val_f <= 15.0:
                options['max_video_seconds'] = val_f
                options['duration'] = int(val_f * 1000)
        except ValueError:
            pass
            
    # 4. Search for standalone numbers if no key-value matched
    if 'fps' not in options and 'duration' not in options:
        # find any float/integer in the text
        # e.g., "p.gif 0.5" or "p.gif 12"
        numbers = re.findall(r'\b\d+(?:\.\d+)?\b', text_lower)
        for num in numbers:
            try:
                numeric_value = float(num)
                # If it's a number like 0.1 to 5.0, it's likely a frame duration in seconds
                if 0.1 <= numeric_value < 5.0:
                    options['duration'] = int(numeric_value * 1000)
                    options['max_video_seconds'] = numeric_value
                    break
                # If it's an integer between 5 and 30, it's likely FPS
                elif 5 <= numeric_value <= GIF_MAX_VIDEO_FPS and numeric_value.is_integer():
                    options['fps'] = int(numeric_value)
                    break
                # If it's between 50 and 3000, it's likely milliseconds delay
                elif 50 <= numeric_value <= 3000:
                    options['duration'] = int(numeric_value)
                    break
            except ValueError:
                pass
                
    return options


def _normalize_attachment_extension(attachment: discord.Attachment) -> str:
    content_type = (attachment.content_type or "").lower().split(";", 1)[0].strip()
    if content_type in GIF_MIME_EXTENSIONS:
        return GIF_MIME_EXTENSIONS[content_type]

    filename = (attachment.filename or "").lower()
    ext = os.path.splitext(filename)[1].lower().strip(".")
    if ext:
        return ext
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


def _validate_pillow_image(source: Image.Image) -> None:
    if (source.format or "").upper() not in GIF_SAFE_PIL_FORMATS:
        raise ValueError("неподдерживаемый формат изображения")
    width, height = source.size
    if width <= 0 or height <= 0 or width * height > GIF_MAX_PIXELS:
        raise ValueError("изображение превышает допустимое разрешение")


def gif_output_limit_for_guild(guild: Any | None) -> int:
    """Return a conservative file limit without wasting boosted guild capacity."""
    raw_limit = getattr(guild, "filesize_limit", None)
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool):
        return GIF_DEFAULT_OUTPUT_BYTES
    guild_limit = raw_limit
    if guild_limit <= 0:
        return GIF_DEFAULT_OUTPUT_BYTES
    if guild_limit > GIF_UPLOAD_HEADROOM_BYTES * 2:
        guild_limit -= GIF_UPLOAD_HEADROOM_BYTES
    return min(guild_limit, GIF_MAX_OUTPUT_BYTES)


def _effective_output_limit(max_output_bytes: int | None) -> int:
    if max_output_bytes is None:
        return GIF_DEFAULT_OUTPUT_BYTES
    try:
        requested = int(max_output_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("неверный лимит размера GIF") from exc
    if requested <= 0:
        raise ValueError("лимит размера GIF должен быть больше нуля")
    return min(requested, GIF_MAX_OUTPUT_BYTES)


def format_gif_error_for_user(error: Exception) -> str:
    """Return actionable GIF errors without exposing FFmpeg paths or internals."""
    text = str(error).strip()
    public_prefixes = (
        "Не найдено вложений",
        "Слишком много вложений",
        "Вложение `",
        "Суммарный размер вложений",
        "Фактический суммарный размер вложений",
        "Готовый GIF превышает",
        "неподдерживаемый формат изображения",
        "изображение превышает допустимое разрешение",
        "неверный лимит размера GIF",
        "лимит размера GIF должен быть больше нуля",
        "в исходном GIF слишком много кадров",
        "не удалось уложить GIF в лимит",
    )
    public_runtime_messages = {
        "Не нашёл подходящих вложений для GIF (нужны изображения или короткое видео).",
        "GIF не был создан.",
        "FFmpeg не успел обработать видео в безопасный срок.",
        "FFmpeg не смог безопасно обработать видео.",
    }
    if isinstance(error, ValueError) and text.startswith(public_prefixes):
        safe_text = text
    elif isinstance(error, RuntimeError) and text in public_runtime_messages:
        safe_text = text
    elif isinstance(error, RuntimeError) and text.startswith("загрузка `"):
        safe_text = text
    else:
        safe_text = "Не удалось безопасно обработать этот файл. Проверь формат и попробуй снова."
    safe_text = "".join(char for char in safe_text if char >= " " or char in "\n\t")
    return discord.utils.escape_mentions(discord.utils.escape_markdown(safe_text))[:500]


def _fit_frame(frame: Image.Image, max_dim: int) -> Image.Image:
    result = frame.convert("RGBA")
    if max(result.size) > max_dim:
        result.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    return result


def _center_frames(frames: list[Image.Image]) -> list[Image.Image]:
    canvas_size = (
        max(frame.width for frame in frames),
        max(frame.height for frame in frames),
    )
    centered: list[Image.Image] = []
    for frame in frames:
        if frame.size == canvas_size:
            centered.append(frame.copy())
            continue
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        offset = ((canvas_size[0] - frame.width) // 2, (canvas_size[1] - frame.height) // 2)
        canvas.alpha_composite(frame, offset)
        centered.append(canvas)
    return centered


def _frame_budget_dimension(frame_count: int) -> int:
    per_frame_budget = GIF_MAX_RENDERED_FRAME_PIXELS / max(1, frame_count)
    return max(192, min(GIF_MAX_DIMENSION, int(sqrt(per_frame_budget))))


def _load_image_frames(
    image_paths: list[str],
    *,
    duration: int | None,
) -> tuple[list[Image.Image], list[int], bool]:
    requested_duration = max(20, min(int(duration), 5000)) if duration is not None else None

    if len(image_paths) == 1:
        with Image.open(image_paths[0]) as source:
            _validate_pillow_image(source)
            frame_count = int(getattr(source, "n_frames", 1))
            if frame_count > GIF_MAX_SOURCE_FRAMES:
                raise ValueError(
                    f"в исходном GIF слишком много кадров: {frame_count} "
                    f"(максимум {GIF_MAX_SOURCE_FRAMES})"
                )
            if frame_count > 1:
                max_dim = _frame_budget_dimension(frame_count)
                frames: list[Image.Image] = []
                durations: list[int] = []
                for frame_index in range(frame_count):
                    source.seek(frame_index)
                    frame_duration = requested_duration
                    if frame_duration is None:
                        frame_duration = max(
                            20,
                            min(int(source.info.get("duration", GIF_IMAGE_FRAME_MS)), 5000),
                        )
                    frames.append(_fit_frame(source.convert("RGBA"), max_dim))
                    durations.append(frame_duration)
                return _center_frames(frames), durations, True

    max_dim = _frame_budget_dimension(max(2, len(image_paths)))
    frames = []
    for path in image_paths:
        with Image.open(path) as source:
            _validate_pillow_image(source)
            source.seek(0)
            frame = ImageOps.exif_transpose(source).convert("RGBA")
            frames.append(_fit_frame(frame, max_dim))
    frames = _center_frames(frames)
    frame_duration = requested_duration or GIF_IMAGE_FRAME_MS
    return frames, [frame_duration] * len(frames), False


def _dimension_ladder(initial: int) -> list[int]:
    dimensions = [initial, 840, 720, 640, 560, 480, 400, 320, 256, 224, 192]
    result: list[int] = []
    for dimension in dimensions:
        candidate = min(initial, dimension)
        if candidate not in result:
            result.append(candidate)
    return result


def _resize_frame_set(frames: list[Image.Image], max_dim: int) -> list[Image.Image]:
    if max(max(frame.size) for frame in frames) <= max_dim:
        return frames
    return [_fit_frame(frame, max_dim) for frame in frames]


def _decimate_animation(
    frames: list[Image.Image],
    durations: list[int],
    stride: int,
) -> tuple[list[Image.Image], list[int]]:
    if stride <= 1:
        return frames, durations
    sampled_frames: list[Image.Image] = []
    sampled_durations: list[int] = []
    for start in range(0, len(frames), stride):
        sampled_frames.append(frames[start])
        sampled_durations.append(sum(durations[start:start + stride]))
    return sampled_frames, sampled_durations


def _save_gif_frames(
    frames: list[Image.Image],
    durations: list[int],
    output_path: str,
) -> None:
    has_transparency = _frames_have_transparency(frames)
    color_count = 255 if has_transparency else 256
    quantized_frames: list[Image.Image] = []
    for frame in frames:
        rgba = frame.convert("RGBA")
        alpha = rgba.getchannel("A")
        quantized = rgba.convert("RGB").quantize(
            colors=color_count,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        if _alpha_has_transparency(alpha):
            transparent_mask = alpha.point(lambda value: 255 if value <= 127 else 0)
            quantized.paste(255, mask=transparent_mask)
        quantized_frames.append(quantized)

    if has_transparency:
        for frame in quantized_frames:
            frame.info["transparency"] = 255

    quantized_frames[0].save(
        output_path,
        format="GIF",
        save_all=True,
        append_images=quantized_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def _alpha_has_transparency(alpha: Image.Image) -> bool:
    minimum, _maximum = cast(tuple[int, int], alpha.getextrema())
    return minimum < 255


def _frames_have_transparency(frames: list[Image.Image]) -> bool:
    return any(
        _alpha_has_transparency(frame.convert("RGBA").getchannel("A"))
        for frame in frames
    )


def _gif_palette_filter(max_dim: int, *, transparent: bool) -> str:
    max_colors = 255 if transparent else 256
    reserve_transparent = 1 if transparent else 0
    return (
        f"scale=w='min(iw,{max_dim})':h='min(ih,{max_dim})':"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        "split[s0][s1];"
        f"[s0]palettegen=max_colors={max_colors}:reserve_transparent={reserve_transparent}:"
        "stats_mode=full[p];"
        "[s1][p]paletteuse=dither=sierra2:diff_mode=rectangle:alpha_threshold=128"
    )


def _run_ffmpeg_image_gif_encode(
    source_path: str,
    output_path: str,
    *,
    max_dim: int,
    transparent: bool,
    timeout: float,
) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source_path,
        "-an",
        "-sn",
        "-dn",
        "-vf",
        _gif_palette_filter(max_dim, transparent=transparent),
        "-loop",
        "0",
        output_path,
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        detail = (result.stderr or "").strip()[-1200:]
        raise RuntimeError(detail or "FFmpeg не создал GIF из изображения")


def _build_gif_from_images(
    image_paths: list[str],
    output_path: str,
    *,
    duration: int | None = None,
    max_output_bytes: int = GIF_DEFAULT_OUTPUT_BYTES,
) -> None:
    frames, durations, animated_source = _load_image_frames(image_paths, duration=duration)
    initial_dimension = max(max(frame.size) for frame in frames)
    if len(image_paths) == 1:
        with Image.open(image_paths[0]) as source:
            initial_dimension = max(
                initial_dimension,
                min(max(source.size), GIF_MAX_DIMENSION),
            )
    profiles = [(dimension, 1) for dimension in _dimension_ladder(initial_dimension)]
    deadline = time.monotonic() + GIF_MAX_ENCODE_SECONDS

    # Один исходный файл FFmpeg кодирует заметно точнее Pillow: строит палитру по
    # полным цветовым данным и сохраняет тайминги исходной анимации. При явно
    # заданной задержке анимированного GIF остаёмся на покадровом пути ниже.
    if len(image_paths) == 1 and (not animated_source or duration is None):
        transparent = _frames_have_transparency(frames)
        for profile_index, (max_dim, _stride) in enumerate(profiles):
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            candidate_path = f"{output_path}.ffmpeg-candidate-{profile_index}.gif"
            try:
                _run_ffmpeg_image_gif_encode(
                    image_paths[0],
                    candidate_path,
                    max_dim=max_dim,
                    transparent=transparent,
                    timeout=min(30.0, remaining),
                )
                if 0 < os.path.getsize(candidate_path) <= max_output_bytes:
                    os.replace(candidate_path, output_path)
                    return
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
            finally:
                if os.path.exists(candidate_path):
                    os.remove(candidate_path)

    if animated_source:
        smallest_dimension = profiles[-1][0]
        profiles.extend((smallest_dimension, stride) for stride in (2, 3, 4))

    for profile_index, (max_dim, stride) in enumerate(profiles):
        if time.monotonic() >= deadline:
            raise RuntimeError("Обработка изображений превысила безопасный срок.")
        candidate_path = f"{output_path}.candidate-{profile_index}.gif"
        resized_frames = _resize_frame_set(frames, max_dim)
        selected_frames, selected_durations = _decimate_animation(
            resized_frames,
            durations,
            stride,
        )
        try:
            _save_gif_frames(selected_frames, selected_durations, candidate_path)
            if 0 < os.path.getsize(candidate_path) <= max_output_bytes:
                os.replace(candidate_path, output_path)
                return
        finally:
            if os.path.exists(candidate_path):
                os.remove(candidate_path)

    raise ValueError(
        f"не удалось уложить GIF в лимит {max_output_bytes / (1024 * 1024):.1f} МБ"
    )


def _run_ffmpeg_gif_encode(
    video_path: str,
    output_path: str,
    *,
    fps: int,
    max_duration: float,
    max_dim: int,
    timeout: float,
) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    filter_str = f"fps={fps}," + _gif_palette_filter(max_dim, transparent=False)
    cmd = [
        ffmpeg_exe,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "0",
        "-t",
        str(max_duration),
        "-i",
        video_path,
        "-an",
        "-sn",
        "-dn",
        "-vf",
        filter_str,
        "-loop",
        "0",
        output_path,
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        detail = (result.stderr or "").strip()[-1200:]
        raise RuntimeError(detail or "FFmpeg не создал выходной GIF")


def _video_quality_profiles(max_dim: int, fps: int) -> list[tuple[int, int]]:
    profiles: list[tuple[int, int]] = []
    for dimension in _dimension_ladder(max_dim)[:7]:
        if dimension >= 720:
            candidate_fps = fps
        elif dimension >= 560:
            candidate_fps = min(fps, 18)
        elif dimension >= 400:
            candidate_fps = min(fps, 15)
        elif dimension >= 320:
            candidate_fps = min(fps, 12)
        else:
            candidate_fps = GIF_MIN_VIDEO_FPS
        profile = (dimension, max(GIF_MIN_VIDEO_FPS, candidate_fps))
        if profile not in profiles:
            profiles.append(profile)
    return profiles


def _build_gif_from_video(
    video_path: str,
    output_path: str,
    *,
    fps: int | None = None,
    max_duration: float | None = None,
    max_dim: int = GIF_MAX_DIMENSION,
    max_output_bytes: int = GIF_DEFAULT_OUTPUT_BYTES,
) -> None:
    actual_duration = (
        max(0.25, min(float(max_duration), 15.0))
        if max_duration is not None
        else GIF_MAX_VIDEO_SECONDS
    )
    actual_fps = max(
        GIF_MIN_VIDEO_FPS,
        min(int(fps or GIF_DEFAULT_VIDEO_FPS), GIF_MAX_VIDEO_FPS),
    )
    profiles = _video_quality_profiles(max_dim, actual_fps)
    deadline = time.monotonic() + GIF_MAX_ENCODE_SECONDS
    last_error: Exception | None = None
    encoded_any = False

    for profile_index, (profile_dimension, profile_fps) in enumerate(profiles):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        candidate_path = f"{output_path}.candidate-{profile_index}.gif"
        try:
            _run_ffmpeg_gif_encode(
                video_path,
                candidate_path,
                fps=profile_fps,
                max_duration=actual_duration,
                max_dim=profile_dimension,
                timeout=min(30.0, remaining),
            )
            encoded_any = True
            if os.path.getsize(candidate_path) <= max_output_bytes:
                os.replace(candidate_path, output_path)
                return
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            last_error = exc
        finally:
            if os.path.exists(candidate_path):
                os.remove(candidate_path)

    if encoded_any:
        raise ValueError(
            f"не удалось уложить GIF в лимит {max_output_bytes / (1024 * 1024):.1f} МБ"
        )
    if isinstance(last_error, subprocess.TimeoutExpired) or time.monotonic() >= deadline:
        raise RuntimeError("FFmpeg не успел обработать видео в безопасный срок.") from last_error
    raise RuntimeError("FFmpeg не смог безопасно обработать видео.") from last_error


async def generate_gif_from_attachments(
    attachments: list[discord.Attachment],
    *,
    duration: int | None = None,
    fps: int | None = None,
    max_video_seconds: float | None = None,
    max_output_bytes: int | None = None,
) -> tuple[str, str]:
    if not attachments:
        raise ValueError("Не найдено вложений для GIF.")
    if len(attachments) > GIF_MAX_ATTACHMENTS:
        raise ValueError(f"Слишком много вложений: максимум {GIF_MAX_ATTACHMENTS}.")

    image_files: list[str] = []
    video_files: list[str] = []
    output_limit = _effective_output_limit(max_output_bytes)
    temp_dir = tempfile.mkdtemp(prefix="psc-gif-")

    try:
        async with _gif_job_semaphore:
            candidates: list[tuple[discord.Attachment, str]] = []
            for attachment in attachments:
                ext = _normalize_attachment_extension(attachment)
                if ext in GIF_IMAGE_EXTENSIONS | GIF_VIDEO_EXTENSIONS:
                    candidates.append((attachment, ext))

            first_video = next(
                ((attachment, ext) for attachment, ext in candidates if ext in GIF_VIDEO_EXTENSIONS),
                None,
            )
            selected = [first_video] if first_video else [
                (attachment, ext)
                for attachment, ext in candidates
                if ext in GIF_IMAGE_EXTENSIONS
            ]

            declared_total_bytes = 0
            actual_total_bytes = 0
            for attachment, ext in selected:
                if attachment.size and attachment.size > GIF_MAX_ATTACHMENT_BYTES:
                    raise ValueError(
                        f"Вложение `{attachment.filename}` слишком большое: максимум {GIF_MAX_ATTACHMENT_BYTES // (1024 * 1024)} МБ."
                    )
                declared_total_bytes += int(attachment.size or 0)
                if declared_total_bytes > GIF_MAX_TOTAL_BYTES:
                    raise ValueError(
                        f"Суммарный размер вложений превышает {GIF_MAX_TOTAL_BYTES // (1024 * 1024)} МБ."
                    )

                unique_name = f"{uuid.uuid4().hex}.{ext or 'bin'}"
                file_path = os.path.join(temp_dir, unique_name)
                try:
                    await asyncio.wait_for(
                        attachment.save(Path(file_path)),
                        timeout=GIF_ATTACHMENT_DOWNLOAD_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"загрузка `{attachment.filename}` из Discord превысила "
                        f"{GIF_ATTACHMENT_DOWNLOAD_SECONDS} секунд"
                    ) from exc
                actual_size = os.path.getsize(file_path)
                if actual_size > GIF_MAX_ATTACHMENT_BYTES:
                    raise ValueError(f"Вложение `{attachment.filename}` превышает допустимый размер.")
                actual_total_bytes += actual_size
                if actual_total_bytes > GIF_MAX_TOTAL_BYTES:
                    raise ValueError(
                        f"Фактический суммарный размер вложений превышает {GIF_MAX_TOTAL_BYTES // (1024 * 1024)} МБ."
                    )

                if ext in GIF_IMAGE_EXTENSIONS:
                    image_files.append(file_path)
                elif ext in GIF_VIDEO_EXTENSIONS:
                    video_files.append(file_path)

            output_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.gif")
            if video_files:
                await asyncio.to_thread(
                    _build_gif_from_video,
                    video_files[0],
                    output_path,
                    fps=fps,
                    max_duration=max_video_seconds,
                    max_output_bytes=output_limit,
                )
            elif image_files:
                await asyncio.to_thread(
                    _build_gif_from_images,
                    image_files,
                    output_path,
                    duration=duration,
                    max_output_bytes=output_limit,
                )
            else:
                raise RuntimeError("Не нашёл подходящих вложений для GIF (нужны изображения или короткое видео).")

            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                raise RuntimeError("GIF не был создан.")
            if os.path.getsize(output_path) > output_limit:
                raise ValueError(
                    f"Готовый GIF превышает {output_limit / (1024 * 1024):.1f} МБ."
                )
            return output_path, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
