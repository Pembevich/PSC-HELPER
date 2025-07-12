import discord
from discord.ext import commands
import sqlite3
import os
import io
from PIL import Image
import moviepy.editor as mp
import uuid
from discord import app_commands
import re
import asyncio
from discord import Embed, Color
from datetime import datetime
import requests
from moviepy.editor import VideoFileClip, ImageSequenceClip

allowed_guild_ids = [1392735009957347419]  # Укажи нужные ID серверов
sbor_channels = {}  # guild_id -> channel_id

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- База данных ---
conn = sqlite3.connect("bot_data.db")
c = conn.cursor()

c.execute('''
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT
)
''')

c.execute('''
CREATE TABLE IF NOT EXISTS private_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER,
    user2_id INTEGER,
    password TEXT
)
''')

c.execute('''
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    sender_id INTEGER,
    message TEXT,
    file BLOB,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()

# --- Приватные чаты ---
@bot.command()
async def open_chat(ctx, chat_id: int):
    user_id = ctx.author.id
    c.execute("SELECT * FROM private_chats WHERE id = ?", (chat_id,))
    chat = c.fetchone()
    if not chat or (user_id != chat[1] and user_id != chat[2]):
        await ctx.send("❌ Чат не найден или у вас нет доступа.")
        return

    embed = discord.Embed(title="Мини-мессенджер", description=f"ID: {chat_id}", color=discord.Color.blurple())
    c.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 5", (chat_id,))
    messages = c.fetchall()
    for msg in reversed(messages):
        sender = await bot.fetch_user(msg[2])
        embed.add_field(name=sender.display_name, value=msg[3] or "[вложение]", inline=False)

    await ctx.send(embed=embed)

# --- Стандартные команды ---
@bot.command()
async def message(ctx, member: discord.Member, *, msg: str = None):
    files = [await a.to_file() for a in ctx.message.attachments]
    if not msg and not files:
        await ctx.send("Введите сообщение или приложите файл.")
        return
    try:
        await member.send(f"Сообщение от **{ctx.author.display_name}**:\n{msg or ''}", files=files)
        await ctx.send("✅ Сообщение отправлено.")
    except:
        await ctx.send("❌ Не удалось отправить сообщение.")

@bot.command()
async def dm(ctx, member: discord.Member, *, msg: str = None):
    if ctx.author.id != 968698192411652176:
        await ctx.send("Нет доступа.")
        return
    files = [await a.to_file() for a in ctx.message.attachments]
    if not msg and not files:
        await ctx.send("Введите сообщение или приложите файл.")
        return
    try:
        await member.send(msg or "", files=files)
        await ctx.send("✅ Анонимное сообщение отправлено.")
    except:
        await ctx.send("❌ Не удалось отправить сообщение.")

@bot.command()
async def add(ctx, title, *, description):
    c.execute("INSERT INTO entries (title, description) VALUES (?, ?)", (title, description))
    conn.commit()
    await ctx.send("Информация добавлена.")

@bot.command()
async def info(ctx):
    c.execute("SELECT title, description FROM entries")
    entries = c.fetchall()
    if not entries:
        await ctx.send("База данных пуста.")
        return

    embed = discord.Embed(title="Информация", color=discord.Color.blue())
    for title, description in entries:
        embed.add_field(name=title, value=description, inline=False)
    await ctx.send(embed=embed)

# --- Команда !gif ---
@bot.command(name='gif')
async def gif(ctx):
    if not ctx.message.attachments:
        await ctx.send("❌ Пожалуйста, прикрепи изображение или видео к команде.")
        return

    image_files = []
    video_files = []

    os.makedirs("temp", exist_ok=True)

    for attachment in ctx.message.attachments:
        filename = attachment.filename
        ext = os.path.splitext(filename)[1].lower().strip(".")

        # Генерируем уникальное имя файла
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        file_path = os.path.join("temp", unique_name)
        await attachment.save(file_path)

        if ext in ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'heic']:
            image_files.append(file_path)
        elif ext in ['mp4', 'mov', 'webm', 'avi', 'mkv']:
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
            clip = clip.subclip(0, min(5, clip.duration))  # максимум 5 сек
            clip.write_gif(output_path)
        else:
            await ctx.send("❌ Не удалось обработать вложения.")
            return

        await ctx.send(file=discord.File(output_path))

    except Exception as e:
        await ctx.send(f"❌ Ошибка при создании GIF: {e}")

    finally:
        # Удаляем все временные файлы
        for f in image_files + video_files:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(output_path):
            os.remove(output_path)


# --- /sbor ---
@bot.tree.command(name="sbor", description="Начать сбор: создаёт голосовой канал и пингует роль")
@app_commands.describe(role="Роль, которую нужно пинговать")
async def sbor(interaction: discord.Interaction, role: discord.Role):
    if interaction.guild.id not in allowed_guild_ids:
        await interaction.response.send_message("❌ Команда недоступна на этом сервере.", ephemeral=True)
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

    voice_channel = await interaction.guild.create_voice_channel(
        "Сбор",
        overwrites=overwrites,
        category=category
    )

    sbor_channels[interaction.guild.id] = voice_channel.id

    webhook = await interaction.channel.create_webhook(name="Сбор")
    await webhook.send(
        content=f"**Сбор! {role.mention}. Заходите в <#{voice_channel.id}>!**",
        username="Сбор",
        avatar_url=bot.user.avatar.url if bot.user.avatar else None
    )
    await webhook.delete()

    await interaction.followup.send("✅ Сбор создан!")

# --- /sbor_end ---
@bot.tree.command(name="sbor_end", description="Завершить сбор и удалить голосовой канал")
async def sbor_end(interaction: discord.Interaction):
    if interaction.guild.id not in allowed_guild_ids:
        await interaction.response.send_message("❌ Команда недоступна на этом сервере.", ephemeral=True)
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
    await webhook.send(
        content="*Сбор окончен!*",
        username="Сбор",
        avatar_url=bot.user.avatar.url if bot.user.avatar else None
    )
    await webhook.delete()

    sbor_channels.pop(interaction.guild.id, None)
    await interaction.followup.send("✅ Сбор завершён.")

# --- on_ready ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Бот запущен как {bot.user}")

# --- Автоматическая выдача роли при входе ---
@bot.event
async def on_member_join(member):
    print(f"👋 Новый участник: {member.name} ({member.id})")
    guild_roles_map = {
        1392735009957347419: 1392735552054366321  # Замените на нужный ID роли
    }

    role_id = guild_roles_map.get(member.guild.id)
    if role_id:
        role = member.guild.get_role(role_id)
        if role:
            try:
                await member.add_roles(role, reason="Автоматическая выдача роли при входе")
                print(f"✅ Роль {role.name} выдана {member.name}")
            except Exception as e:
                print(f"❌ Не удалось выдать роль: {e}")

# --- Проверка шаблона и бан ---
target_channel_id = 1393342266503987270

async def send_error_embed(channel, author, error_text, example_template):
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S МСК")

    embed = Embed(
        title="❌ Ошибка отправки отчёта",
        description=error_text,
        color=Color.red()
    )
    embed.add_field(name="📝 Как оформить правильно", value=f"```{example_template}```", inline=False)
    embed.set_footer(text=f"Вызвал: {author.name} | ID: {author.id} | {now}")

    await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id == target_channel_id:
        template = (
            "Никнейм: TSergey2008\n"
            "Дс айди: 123456789012345678\n"
            "Время: 1h 30min\n"
            "Причина: причина выдачи черного списка\n"
            "Док-ва: Скрин/ссылка"
        )

        lines = [line.strip() for line in message.content.strip().split("\n") if line.strip()]
        if len(lines) != 5:
            await send_error_embed(message.channel, message.author, "Неверное количество строк.", template)
            await bot.process_commands(message)
            return

        nickname_line, id_line, time_line, reason_line, evidence_line = lines

        if not nickname_line.lower().startswith("никнейм:") \
            or not id_line.lower().startswith("дс айди:") \
            or not time_line.lower().startswith("время:") \
            or not reason_line.lower().startswith("причина:") \
            or not evidence_line.lower().startswith("док-ва:"):
            await send_error_embed(message.channel, message.author, "Некорректный шаблон.", template)
            await bot.process_commands(message)
            return

        try:
            user_id = int(id_line.split(":", 1)[1].strip())
        except ValueError:
            await send_error_embed(message.channel, message.author, "`Дс айди` должен быть числом.", template)
            await bot.process_commands(message)
            return

        time_text = time_line.split(":", 1)[1].strip()
        h_match = re.search(r"(\d+)\s*h", time_text)
        m_match = re.search(r"(\d+)\s*min", time_text)

        total_seconds = 0
        if h_match:
            total_seconds += int(h_match.group(1)) * 3600
        if m_match:
            total_seconds += int(m_match.group(1)) * 60

        if total_seconds == 0:
            await send_error_embed(message.channel, message.author, "Некорректное время.", template)
            await bot.process_commands(message)
            return

        try:
            member = await message.guild.fetch_member(user_id)
            reason = reason_line.split(":", 1)[1].strip()
            await message.guild.ban(member, reason=reason)
            await message.add_reaction("✅")

            async def unban_later():
                await asyncio.sleep(total_seconds)
                await message.guild.unban(discord.Object(id=user_id), reason="Время бана истекло")

            bot.loop.create_task(unban_later())

        except Exception as e:
            await send_error_embed(message.channel, message.author, f"Не удалось забанить пользователя: {e}", template)

    await bot.process_commands(message)

bot.run(os.getenv("DISCORD_TOKEN"))
