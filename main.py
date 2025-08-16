# main.py — полный рабочий код с фиксом on_message
import discord
from discord.ext import commands
import sqlite3
import os
import io
from PIL import Image
from moviepy.editor import VideoFileClip, ImageSequenceClip
import uuid
import re
import asyncio
from discord import Embed, Color
from datetime import datetime
from discord.ui import View, button, Modal, TextInput
from collections import defaultdict, deque
import time
from openai import OpenAI

# --- Константы / настройки ---
allowed_role_ids = [1340596390614532127, 1341204231419461695]
allowed_guild_ids = [1340594372596469872]
sbor_channels = {}

FORM_CHANNEL_ID = 1340996239113850971
GOT_CHANNEL_ID = 1394635110665556009
GOT_ROLE_ID = 1341041194733670401
GOT_ROLE_REWARDS = [1341040784723411017, 1341040871562285066]

CESU_CHANNEL_ID = 1394635216986964038
CESU_ROLE_ID = 1341040607728107591
CESU_ROLE_REWARDS = [1341100562783014965, 1341039967555551333]

# PSC
PSC_CHANNEL_ID = 1340596250809991228
PING_ROLE_ID = 1341168051269275718

# STOPREID
SPAM_WINDOW_SECONDS = 10
SPAM_DUPLICATES_THRESHOLD = 4
SPAM_LOG_CHANNEL = 1347412933986226256
MUTE_ROLE_ID = 1402793648005058805

# Жалобы
COMPLAINT_INPUT_CHANNEL = 1404977876184334407
COMPLAINT_NOTIFY_ROLE = 1341203508606533763

# Лог наказаний
log_channel_id = 1392125177399218186

# Наказания (роли)
punishment_roles = {
    "1 выговор": 1341379345322610698,
    "2 выговора": 1341379426314620992,
    "1 страйк": 1341379475681841163,
    "2 страйка": 1341379529997815828
}

squad_roles = {
    "got_base": 1341040784723411017,
    "got_notify": 1341041194733670401,
    "cesu_base": 1341100562783014965,
    "cesu_notify": 1341040607728107591
}

# --- Инициализация бота ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- DB ---
conn = sqlite3.connect("bot_data.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS private_chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user1_id INTEGER, user2_id INTEGER, password TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, message TEXT, file BLOB, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

# -----------------------
# Утилиты
# -----------------------
def extract_clean_keyword(text: str):
    return re.sub(r"[^a-z]", "", text.lower())

async def safe_send_dm(user: discord.User, embed: Embed, file: discord.File = None):
    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
    except:
        pass

# -----------------------
# Команды
# -----------------------
@bot.command(name='gif')
async def gif(ctx):
    if not ctx.message.attachments:
        await ctx.send("Пожалуйста, прикрепи изображение или видео к команде.")
        return

    image_files, video_files = [], []
    os.makedirs("temp", exist_ok=True)

    for attachment in ctx.message.attachments:
        filename = attachment.filename
        ext = os.path.splitext(filename)[1].lower().strip(".")
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
            clip = VideoFileClip(video_files[0]).subclip(0, 5)
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

# --- Сбор ---
@bot.tree.command(name="sbor", description="Начать сбор: создаёт голосовой канал и пингует роль")
async def sbor(interaction: discord.Interaction, role: discord.Role):
    if interaction.guild.id not in allowed_guild_ids:
        return await interaction.response.send_message("❌ Команда недоступна", ephemeral=True)

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not any(r.id in allowed_role_ids for r in member.roles):
        return await interaction.response.send_message("❌ Нет прав", ephemeral=True)

    existing = discord.utils.get(interaction.guild.voice_channels, name="сбор")
    if existing:
        return await interaction.response.send_message("❗ Канал уже существует")

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(connect=False),
        role: discord.PermissionOverwrite(connect=True, view_channel=True)
    }
    category = interaction.channel.category
    voice_channel = await interaction.guild.create_voice_channel("сбор", overwrites=overwrites, category=category)
    sbor_channels[interaction.guild.id] = voice_channel.id

    webhook = await interaction.channel.create_webhook(name="Сбор")
    await webhook.send(content=f"**Сбор! {role.mention} заходите в <#{voice_channel.id}>!**")
    await webhook.delete()
    await interaction.response.send_message("✅ Сбор создан!")

@bot.tree.command(name="sbor_end", description="Завершить сбор и удалить канал")
async def sbor_end(interaction: discord.Interaction):
    if interaction.guild.id not in allowed_guild_ids:
        return await interaction.response.send_message("❌ Команда недоступна", ephemeral=True)

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not any(r.id in allowed_role_ids for r in member.roles):
        return await interaction.response.send_message("❌ Нет прав", ephemeral=True)

    channel_id = sbor_channels.get(interaction.guild.id)
    if not channel_id:
        return await interaction.response.send_message("❗ Канал не найден")

    channel = interaction.guild.get_channel(channel_id)
    if channel:
        await channel.delete()

    webhook = await interaction.channel.create_webhook(name="Сбор")
    await webhook.send(content="*Сбор окончен!*")
    await webhook.delete()
    sbor_channels.pop(interaction.guild.id, None)
    await interaction.response.send_message("✅ Сбор завершён")

# --- AI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@bot.command(name="ai")
async def ai_chat(ctx, *, prompt: str):
    await ctx.trigger_typing()
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )
        answer = response.choices[0].message.content
        await ctx.send(answer)
    except Exception as e:
        await ctx.send(f"❌ Ошибка: {e}")

# -----------------------
# on_message (фиксированный)
# -----------------------
recent_messages = defaultdict(lambda: deque())

def message_key_for_spam(message: discord.Message):
    text = (message.content or "").strip()
    att_summary = [a.filename for a in message.attachments]
    return f"{text}||{'|'.join(att_summary)}"

async def handle_spam_if_needed(message: discord.Message):
    user_id = message.author.id
    key = message_key_for_spam(message)
    now = time.time()
    dq = recent_messages[user_id]
    dq.append((key, now, message.id, message.channel.id))
    while dq and now - dq[0][1] > SPAM_WINDOW_SECONDS:
        dq.popleft()
    count = sum(1 for k, t, mid, cid in dq if k == key)
    if count >= SPAM_DUPLICATES_THRESHOLD:
        guild = message.guild
        mute_role = guild.get_role(MUTE_ROLE_ID)
        member = guild.get_member(user_id)
        if member and mute_role:
            await member.add_roles(mute_role, reason="STOPREID spam auto-mute")
        recent_messages[user_id].clear()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    try:
        await handle_spam_if_needed(message)
    except Exception as e:
        print(f"Spam check error: {e}")

    # PSC
    if message.channel.id == PSC_CHANNEL_ID:
        content = (message.content or "").strip()
        if content.upper().startswith("С ВЕБХУКОМ"):
            try:
                await message.delete()
            except:
                pass
            role = message.guild.get_role(PING_ROLE_ID)
            if role:
                await message.channel.send(role.mention)

    # Жалобы
    if message.channel.id == COMPLAINT_INPUT_CHANNEL:
        lines = [ln.strip() for ln in (message.content or "").split("\n") if ln.strip()]
        if len(lines) < 2:
            try:
                await message.delete()
            except:
                pass
            em = Embed(title="❌ Неверная форма", description="Минимум 2 строки", color=Color.red())
            await safe_send_dm(message.author, em)

    # Формы вступления
    if message.channel.id == FORM_CHANNEL_ID:
        lines = [line.strip() for line in message.content.strip().split("\n") if line.strip()]
        if len(lines) == 3:
            keyword = extract_clean_keyword(lines[2])
            if keyword not in ("got", "cesu"):
                await message.reply("❌ Ошибка: третий пункт должен быть got или cesu")

    await bot.process_commands(message)

# -----------------------
# on_ready
# -----------------------
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    for guild_id in allowed_guild_ids:
        try:
            await bot.tree.sync(guild=discord.Object(id=guild_id))
            print(f"Команды синхронизированы для {guild_id}")
        except Exception as e:
            print(f"Ошибка sync: {e}")

# -----------------------
# Запуск
# -----------------------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("❌ DISCORD_TOKEN не найден в переменных окружения")