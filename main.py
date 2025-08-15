# main.py — объединённый обновлённый файл
import discord
from discord.ext import commands
import sqlite3
import os
import io
from PIL import Image
import moviepy.editor as mp
import uuid
import re
import asyncio
from discord import Embed, Color
from datetime import datetime
from moviepy.editor import VideoFileClip, ImageSequenceClip
from discord.ui import View, button, Modal, TextInput
import aiohttp
import requests
from collections import defaultdict, deque
import time
from openai import OpenAI

# Инструкция: положи токен в переменную окружения DISCORD_TOKEN

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

# PSC (embed с логотипом) канал и роль для пинга
PSC_CHANNEL_ID = 1340596250809991228
PING_ROLE_ID = 1341168051269275718

# STOPREID (анти-спам)
SPAM_WINDOW_SECONDS = 10     # окно времени в секундах для проверки повторов
SPAM_DUPLICATES_THRESHOLD = 4  # сколько одинаковых сообщений -> действие
SPAM_LOG_CHANNEL = 1347412933986226256
MUTE_ROLE_ID = 1402793648005058805

# Жалобы
COMPLAINT_INPUT_CHANNEL = 1404977876184334407
COMPLAINT_NOTIFY_ROLE = 1341203508606533763
COMPLAINT_PREFIX = None  # префикс в сообщении не требуется — проверяется формой внутри
# Лог канал (для наказаний и т.д.)
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

# Отключаем встроенный voice client, если окружение не поддерживает audioop (render)
try:
    import audioop  # noqa
except Exception:
    discord.VoiceClient = None

# --- DB (остается, если нужно) ---
conn = sqlite3.connect("bot_data.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS private_chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user1_id INTEGER, user2_id INTEGER, password TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, message TEXT, file BLOB, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

# -----------------------
# Утилитарные функции
# -----------------------
def extract_clean_keyword(text: str):
    return re.sub(r"[^a-z]", "", text.lower())

async def safe_send_dm(user: discord.User, embed: Embed, file: discord.File = None):
    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
    except Exception:
        # Нельзя отправить ЛС — игнорируем
        pass

# -----------------------
# КОМАНДЫ: gif + sbor (как у тебя были)
# -----------------------
@bot.command(name='gif')
async def gif(ctx):
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
    await interaction.followup.send("✅ Сбор завершён.")

# -----------------------
# ConfirmView для выдачи ролей (оставляем как было)
# -----------------------
class ConfirmView(View):
    def __init__(self, user_id, target_message, squad_name, role_ids, target_user_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.message = target_message
        self.squad_name = squad_name
        self.role_ids = role_ids
        self.target_user_id = target_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if any(role.id == self.user_id for role in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ У тебя нет прав нажимать эту кнопку.", ephemeral=True)
        return False

    @button(label="Принять", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.guild.get_member(self.target_user_id)
        if member:
            for role_id in self.role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    await member.add_roles(role)
            await self.message.reply(embed=Embed(title="✅ Принято", description=f"Вы зачислены в отряд **{self.squad_name}**!", color=Color.green()))
        await interaction.response.send_message("Принято.", ephemeral=True)
        self.stop()

    @button(label="Отказать", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.message.add_reaction("❌")
        await interaction.response.send_message("Отказ зарегистрирован.", ephemeral=True)
        self.stop()

# -----------------------
# STOPREID (анти-спам)
# -----------------------
# Для каждого пользователя храним deque с элементов (key, timestamp), key = текст + список attachment urls/types
recent_messages = defaultdict(lambda: deque())

def message_key_for_spam(message: discord.Message):
    # Включаем в ключ: текст + имена/кол-во вложений + типы вложений
    text = (message.content or "").strip()
    att_summary = []
    for a in message.attachments:
        att_summary.append(f"{a.filename}")
    att_part = "|".join(att_summary)
    return f"{text}||{att_part}"

async def handle_spam_if_needed(message: discord.Message):
    user_id = message.author.id
    key = message_key_for_spam(message)
    now = time.time()
    dq = recent_messages[user_id]
    dq.append((key, now, message.id, message.channel.id))
    # удалить старые
    while dq and now - dq[0][1] > SPAM_WINDOW_SECONDS:
        dq.popleft()
    # подсчитать количество одинаковых ключей
    count = sum(1 for k, t, mid, cid in dq if k == key)
    if count >= SPAM_DUPLICATES_THRESHOLD:
        # действуем: удаляем последние совпадающие сообщения в канале и мутим
        # Соберём id сообщений для удаления (в том же канале)
        to_delete = [mid for k0, t0, mid, cid in dq if k0 == key and cid == message.channel.id]
        # удаляем сообщения (по одному)
        for mid in to_delete:
            try:
                msg = await message.channel.fetch_message(mid)
                if msg:
                    await msg.delete()
            except Exception:
                pass
        # выдаём роль мута
        guild = message.guild
        mute_role = guild.get_role(MUTE_ROLE_ID)
        member = guild.get_member(user_id)
        if member and mute_role:
            try:
                await member.add_roles(mute_role, reason="STOPREID spam auto-mute")
            except Exception:
                pass
        # логируем
        spam_log = guild.get_channel(SPAM_LOG_CHANNEL)
        if spam_log:
            await spam_log.send(embed=Embed(title="🚨 STOPREID", description=f"{member.mention if member else user_id} был замучен за спам.\nКанал: {message.channel.mention}\nСообщение: `{message.content[:300]}`", color=Color.orange()))
        # очистим deque для пользователя, чтобы не повторять
        recent_messages[user_id].clear()

# -----------------------
# Жалобы: view + modal
# -----------------------
class RejectModal(Modal):
    def __init__(self, submitter: discord.Member, complaint_channel_id: int):
        super().__init__(title="Причина отклонения")
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id
        self.reason = TextInput(label="Причина отклонения", style=discord.TextStyle.long, required=True, placeholder="Объясните почему отклоняете жалобу")
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value
        guild = interaction.guild
        # найдем канал жалобы и соберём историю
        try:
            c = guild.get_channel(self.complaint_channel_id)
            history = []
            if c:
                async for m in c.history(limit=200, oldest_first=True):
                    # формируем историю
                    history.append(f"{m.author.display_name}: {m.content}")
            history_text = "\n".join(history) if history else "(нет истории)"
        except Exception:
            history_text = "(не удалось получить историю)"

        # Сообщение автору (submitter)
        embed = Embed(title="❌ Ваша жалоба отклонена", color=Color.red())
        embed.add_field(name="Причина", value=reason_text, inline=False)
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        # Ответ админу
        await interaction.response.send_message("Отклонение отправлено автору. Канал жалобы будет удалён.", ephemeral=True)
        # удалим канал жалобы
        try:
            channel = guild.get_channel(self.complaint_channel_id)
            if channel:
                await channel.delete(reason=f"Жалоба отклонена админом {interaction.user}")
        except Exception:
            pass

class ComplaintView(View):
    def __init__(self, submitter: discord.Member, complaint_channel_id: int):
        super().__init__(timeout=None)
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Только админы (или пользователи с правом manage_guild / administrator)
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("❌ Только администраторы могут взаимодействовать.", ephemeral=True)
        return False

    @button(label="Одобрено", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Собираем историю жалобы
        guild = interaction.guild
        history = []
        channel = guild.get_channel(self.complaint_channel_id)
        if channel:
            async for m in channel.history(limit=200, oldest_first=True):
                history.append(f"{m.author.display_name}: {m.content}")
        history_text = "\n".join(history) if history else "(нет истории)"

        embed = Embed(title="✅ Ваша жалоба одобрена", color=Color.green())
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        await interaction.response.send_message("Жалоба одобрена, автор уведомлён.", ephemeral=True)
        # удалить канал жалобы
        try:
            if channel:
                await channel.delete(reason=f"Жалоба одобрена админом {interaction.user}")
        except Exception:
            pass

    @button(label="Отклонено", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Открываем modal, чтобы админ ввёл причину
        modal = RejectModal(submitter=self.submitter, complaint_channel_id=self.complaint_channel_id)
        await interaction.response.send_modal(modal)

# -----------------------
# ЕДИНЫЙ on_message: обрабатывает всё
# -----------------------
@bot.event
async def on_message(message: discord.Message):
    # не реагируем на ботов
    if message.author.bot:
        return

    # сначала — STOPREID (анти-спам), независимо от канала
    try:
        await handle_spam_if_needed(message)
    except Exception:
        pass

    # --- PSC EMBED СООБЩЕНИЯ ---
    if message.channel.id == PSC_CHANNEL_ID:
        # Условие: сообщение должно начинаться с "С ВЕБХУКОМ"
        content_strip = (message.content or "").strip()
        if not content_strip.upper().startswith("С ВЕБХУКОМ"):
            # не действуем
            await bot.process_commands(message)
            return

        # Удаляем исходное сообщение
        try:
            await message.delete()
        except Exception:
            pass

        # Пингуем роль в канале (вне эмбеда)
        guild = message.guild
        role_ping = guild.get_role(PING_ROLE_ID)
        if role_ping:
            try:
                await message.channel.send(role_ping.mention)
            except Exception:
                pass

        # Уберём префикс из текста для эмбеда
        content_without_flag = content_strip[len("С ВЕБХУКОМ"):].strip()

        # Если есть вложение (картинка/гифка), используем её; иначе отправим эмбед без картинки
        file_to_send = None
        embed = Embed(description=content_without_flag or "(без текста)", color=Color.from_rgb(255,255,255))
        embed.set_footer(text=f"©Provision Security Complex | {datetime.now().strftime('%d.%m.%Y')}")
        if message.attachments:
            # берем первую вложенную картинку
            att = message.attachments[0]
            # Сохраним временно файл и затем отправим как discord.File с attachment://filename
            try:
                os.makedirs("temp", exist_ok=True)
                fname = f"temp/{uuid.uuid4().hex}-{att.filename}"
                await att.save(fname)
                file_to_send = discord.File(fname, filename=att.filename)
                embed.set_image(url=f"attachment://{att.filename}")
            except Exception:
                file_to_send = None

        try:
            if file_to_send:
                await message.channel.send(embed=embed, file=file_to_send)
                # удалить временный файл
                try:
                    os.remove(file_to_send.fp.name)
                except Exception:
                    pass
            else:
                await message.channel.send(embed=embed)
        except Exception:
            pass

        await bot.process_commands(message)
        return

    # --- Жалобы: приём формы ---
    if message.channel.id == COMPLAINT_INPUT_CHANNEL:
        # Ожидаем форму: минимум 2 строки (1 и 2 обязательны), 3 строка необязательна
        lines = [ln.strip() for ln in (message.content or "").split("\n") if ln.strip()]
        if len(lines) < 2:
            # удаляем и отправляем автору в ЛС шаблон
            try:
                await message.delete()
            except Exception:
                pass
            template = "1. Никнейм нарушителя\n2. Суть нарушения\n3. Доказательства (по желанию)"
            em = Embed(title="❌ Неверная форма жалобы", description="В форме должно быть минимум 2 строки: никнейм и суть нарушения.", color=Color.red())
            em.add_field(name="Пример", value=f"```{template}```", inline=False)
            await safe_send_dm(message.author, em)
            await bot.process_commands(message)
            return

        # корректная форма — удаляем сообщение и создаём приватный канал
        try:
            await message.delete()
        except Exception:
            pass

        guild = message.guild
        # определяем категорию — используем категорию, где было сообщение
        category = message.channel.category

        # получаем порядковый номер жалобы в этой категории (в названии) — посчитаем существующие канал с префиксом "жалоба-"
        existing = [ch for ch in (category.channels if category else guild.channels) if ch.name.startswith("жалоба-")]
        index = len(existing) + 1
        channel_name = f"жалоба-{index}"

        # настройки overwrites: скрываем для @everyone, показываем автору и всем admin ролям
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            message.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        # даём доступ ролям, у которых есть админские права
        for role in guild.roles:
            try:
                if role.permissions.administrator or role.permissions.manage_guild:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            except Exception:
                pass

        # создаём канал
        try:
            complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, category=category, reason=f"Новая жалоба от {message.author}")
        except Exception:
            # попытка создать канал в другом месте (если нет категории)
            try:
                complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, reason=f"Новая жалоба от {message.author}")
            except Exception:
                complaint_chan = None

        # формируем эмбед с жалобой и кнопками
        embed = Embed(title="📢 Новая жалоба", color=Color.blue())
        embed.add_field(name="Отправитель", value=f"{message.author.mention} (ID: {message.author.id})", inline=False)
        # вставим всё отправленное (в виде кода, чтобы не ломать)
        full_text = "\n".join(lines)
        embed.add_field(name="Жалоба", value=f"```{full_text[:1900]}```", inline=False)
        embed.set_footer(text=f"ID жалобы: {index} | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        # упоминание роли уведомления
        notify_role = guild.get_role(COMPLAINT_NOTIFY_ROLE)
        try:
            # отправляем сообщение в канал жалобы: пингуем роль отдельно чтобы она уведомилась
            if complaint_chan:
                if notify_role:
                    await complaint_chan.send(notify_role.mention)
                view = ComplaintView(submitter=message.author, complaint_channel_id=complaint_chan.id)
                await complaint_chan.send(embed=embed, view=view)
        except Exception:
            pass

        await bot.process_commands(message)
        return

    # --- Обработка формы вступления (got/cesu) ---
    if message.channel.id == FORM_CHANNEL_ID:
        template = "1. Ваш никнейм\n2. Ваш Дискорд Ник\n3. got или cesu"
        lines = [line.strip() for line in message.content.strip().split("\n") if line.strip()]
        if len(lines) != 3:
            try:
                await message.reply(embed=Embed(title="❌ Неверный шаблон", description="Форма должна содержать 3 строки.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        user_line, id_line, choice_line = lines
        keyword = extract_clean_keyword(choice_line)

        if keyword == "got":
            role_id, channel_id, rewards, squad = GOT_ROLE_ID, GOT_CHANNEL_ID, GOT_ROLE_REWARDS, "G.o.T"
        elif keyword == "cesu":
            role_id, channel_id, rewards, squad = CESU_ROLE_ID, CESU_CHANNEL_ID, CESU_ROLE_REWARDS, "C.E.S.U"
        else:
            try:
                await message.reply(embed=Embed(title="❌ Неизвестный отряд", description="Третий пункт должен содержать **только** got или cesu.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        target_channel = message.guild.get_channel(channel_id)
        role_ping = message.guild.get_role(role_id)
        if not target_channel or not role_ping:
            try:
                await message.reply("❌ Ошибка конфигурации.")
            except Exception:
                pass
            await bot.process_commands(message)
            return

        embed = Embed(title=f"📋 Подтверждение вступления в {squad}", description=f"{message.author.mention} хочет вступить в отряд **{squad}**\nНикнейм: `{user_line}`\nID: `{id_line}`", color=Color.blue())
        embed.set_footer(text=f"ID пользователя: {message.author.id} | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        view = ConfirmView(user_id=role_id, target_message=message, squad_name=squad, role_ids=rewards, target_user_id=message.author.id)

        try:
            await target_channel.send(content=role_ping.mention, embed=embed, view=view)
        except Exception:
            pass

        await bot.process_commands(message)
        return

    # --- Обработка наказаний (форма по id, выговоры/страйки) ---
    if message.channel.id == form_channel_id:
        template = (
            "Никнейм: Robloxer228\n"
            "Дискорд айди: 1234567890\n"
            "Наказание: 1 выговор / 2 выговора / 1 страйк / 2 страйка\n"
            "Причина: причина наказания\n"
            "Док-ва: (по желанию)"
        )

        lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
        if len(lines) < 4 or len(lines) > 5:
            try:
                await message.reply(embed=Embed(title="❌ Ошибка", description="Форма должна содержать 4 или 5 строк.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        try:
            nickname = lines[0].split(":", 1)[1].strip()
            user_id = int(lines[1].split(":", 1)[1].strip())
            punishment = lines[2].split(":", 1)[1].strip().lower()
            reason = lines[3].split(":", 1)[1].strip()
        except Exception:
            try:
                await message.reply(embed=Embed(title="❌ Ошибка в шаблоне", description="Проверь правильность полей (особенно Discord ID)", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        member = message.guild.get_member(user_id)
        if not member:
            try:
                await message.reply("❌ Пользователь с таким ID не найден на сервере.")
            except Exception:
                pass
            await bot.process_commands(message)
            return

        roles = member.roles
        log = message.guild.get_channel(log_channel_id)

        async def log_action(text):
            if log:
                await log.send(embed=Embed(title="📋 Лог наказаний", description=text, color=Color.orange()))

        async def apply_roles(to_add, to_remove):
            for r in to_remove:
                if r in roles:
                    try:
                        await member.remove_roles(r)
                    except Exception:
                        pass
            for r in to_add:
                if r not in roles:
                    try:
                        await member.add_roles(r)
                    except Exception:
                        pass

        punish_1 = message.guild.get_role(punishment_roles["1 выговор"])
        punish_2 = message.guild.get_role(punishment_roles["2 выговора"])
        strike_1 = message.guild.get_role(punishment_roles["1 страйк"])
        strike_2 = message.guild.get_role(punishment_roles["2 страйка"])

        # Логика наказаний — как ты прописал ранее
        if all(r in roles for r in [punish_1, punish_2, strike_1, strike_2]):
            if squad_roles["got_base"] in [r.id for r in roles]:
                notify = message.guild.get_role(squad_roles["got_notify"])
            elif squad_roles["cesu_base"] in [r.id for r in roles]:
                notify = message.guild.get_role(squad_roles["cesu_notify"])
            else:
                notify = None
            if notify:
                await log_action(f"{notify.mention}\nСотрудник {member.mention} получил **максимальное количество наказаний** и подлежит **увольнению**.")
            await bot.process_commands(message)
            return

        if punishment == "1 выговор":
            if punish_1 in roles and punish_2 in roles:
                await apply_roles([strike_1], [punish_1, punish_2])
                await log_action(f"{member.mention} получил 1 страйк (2 выговора заменены).")
            elif punish_1 in roles:
                await apply_roles([punish_2], [])
                await log_action(f"{member.mention} получил второй выговор.")
            else:
                await apply_roles([punish_1], [])
                await log_action(f"{member.mention} получил первый выговор.")
        elif punishment == "2 выговора":
            if punish_1 in roles and punish_2 in roles:
                await apply_roles([strike_1], [punish_1, punish_2])
                await log_action(f"{member.mention} получил 1 страйк (2 выговора заменены).")
            elif punish_1 in roles:
                await apply_roles([strike_1], [punish_1])
                await log_action(f"{member.mention} получил 1 страйк (1 выговор заменён).")
            else:
                await apply_roles([punish_1, punish_2], [])
                await log_action(f"{member.mention} получил 2 выговора.")
        elif punishment == "1 страйк":
            if strike_1 in roles and strike_2 in roles:
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
                else:
                    notify = None
                if notify:
                    await log_action(f"{notify.mention}\nСотрудник {member.mention} получил 3-й страйк. Подлежит увольнению.")
            elif strike_1 in roles:
                await apply_roles([strike_2], [])
                await log_action(f"{member.mention} получил второй страйк.")
            else:
                await apply_roles([strike_1], [])
                await log_action(f"{member.mention} получил первый страйк.")
        elif punishment == "2 страйка":
            if strike_1 in roles or strike_2 in roles:
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
                else:
                    notify = None
                if notify:
                    await log_action(f"{notify.mention}\nСотрудник {member.mention} уже имеет страйки и получил ещё. Подлежит увольнению.")
            else:
                await apply_roles([strike_1, strike_2], [])
                await log_action(f"{member.mention} получил 2 страйка.")
        else:
            try:
                await message.reply(embed=Embed(title="❌ Неизвестное наказание", description="Допустимые значения: `1 выговор`, `2 выговора`, `1 страйк`, `2 страйка`.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass

        await bot.process_commands(message)
        return

    # Если ни одно условие не сработало — пропускаем к прочим командам
    await bot.process_commands(message)

# -----------------------
# on_ready: синхронизация команд
# -----------------------
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user} (id: {bot.user.id})")
    for guild_id in allowed_guild_ids:
        try:
            guild = discord.Object(id=guild_id)
            await bot.tree.sync(guild=guild)
            print(f"✅ Команды синхронизированы с сервером {guild_id}")
        except Exception as e:
            print(f"❌ Ошибка при синхронизации: {e}")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@bot.command(name="ai")
async def ai_chat(ctx, *, prompt: str):
    """Общение с ИИ через OpenAI"""
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
# Запуск
# -----------------------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN not set in environment.")
    else:
        bot.run(token)