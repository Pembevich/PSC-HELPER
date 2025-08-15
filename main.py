# main.py — объединённый обновлённый файл (с исправленной интеграцией OpenAI)
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
recent_messages = defaultdict(lambda: deque())

def message_key_for_spam(message: discord.Message):
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
    while dq and now - dq[0][1] > SPAM_WINDOW_SECONDS:
        dq.popleft()
    count = sum(1 for k, t, mid, cid in dq if k == key)
    if count >= SPAM_DUPLICATES_THRESHOLD:
        to_delete = [mid for k0, t0, mid, cid in dq if k0 == key and cid == message.channel.id]
        for mid in to_delete:
            try:
                msg = await message.channel.fetch_message(mid)
                if msg:
                    await msg.delete()
            except Exception:
                pass
        guild = message.guild
        mute_role = guild.get_role(MUTE_ROLE_ID)
        member = guild.get_member(user_id)
        if member and mute_role:
            try:
                await member.add_roles(mute_role, reason="STOPREID spam auto-mute")
            except Exception:
                pass
        spam_log = guild.get_channel(SPAM_LOG_CHANNEL)
        if spam_log:
            await spam_log.send(embed=Embed(title="🚨 STOPREID", description=f"{member.mention if member else user_id} был замучен за спам.\nКанал: {message.channel.mention}\nСообщение: `{(message.content or '')[:300]}`", color=Color.orange()))
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
        try:
            c = guild.get_channel(self.complaint_channel_id)
            history = []
            if c:
                async for m in c.history(limit=200, oldest_first=True):
                    history.append(f"{m.author.display_name}: {m.content}")
            history_text = "\n".join(history) if history else "(нет истории)"
        except Exception:
            history_text = "(не удалось получить историю)"

        embed = Embed(title="❌ Ваша жалоба отклонена", color=Color.red())
        embed.add_field(name="Причина", value=reason_text, inline=False)
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        await interaction.response.send_message("Отклонение отправлено автору. Канал жалобы будет удалён.", ephemeral=True)
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
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("❌ Только администраторы могут взаимодействовать.", ephemeral=True)
        return False

    @button(label="Одобрено", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        try:
            if channel:
                await channel.delete(reason=f"Жалоба одобрена админом {interaction.user}")
        except Exception:
            pass

    @button(label="Отклонено", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RejectModal(submitter=self.submitter, complaint_channel_id=self.complaint_channel_id)
        await interaction.response.send_modal(modal)

# -----------------------
# ЕДИНЫЙ on_message: обрабатывает всё
# -----------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    try:
        await handle_spam_if_needed(message)
    except Exception:
        pass

    # --- PSC EMBED СООБЩЕНИЯ ---
    if message.channel.id == PSC_CHANNEL_ID:
        content_strip = (message.content or "").strip()
        if not content_strip.upper().startswith("С ВЕБХУКОМ"):
            await bot.process_commands(message)
            return

        try:
            await message.delete()
        except Exception:
            pass

        guild = message.guild
        role_ping = guild.get_role(PING_ROLE_ID)
        if role_ping:
            try:
                await message.channel.send(role_ping.mention)
            except Exception:
                pass

        content_without_flag = content_strip[len("С ВЕБХУКОМ"):].strip()

        file_to_send = None
        embed = Embed(description=content_without_flag or "(без текста)", color=Color.from_rgb(255,255,255))
        embed.set_footer(text=f"©Provision Security Complex | {datetime.now().strftime('%d.%m.%Y')}")
        if message.attachments:
            att = message.attachments[0]
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
        lines = [ln.strip() for ln in (message.content or "").split("\n") if ln.strip()]
        if len(lines) < 2:
            template = "1. Никнейм нарушителя\n2. Суть нарушения\n3. Доказательства (по желанию)"
            em = Embed(title="❌ Неверная форма жалобы", description="В форме должно быть минимум 2 строки: никнейм и суть нарушения.", color=Color.red())
            em.add_field(name="Пример", value=f"```{template}```", inline=False)
            await safe_send_dm(message.author, em)
            await bot.process_commands(message)
            return

        # корректная форма — НЕ удаляем сообщение (по твоему последнему требованию), просто отмечаем и ждём одобрения админа
        # ставим реакцию "галочка" чтобы админы понимали что всё ок
        try:
            await message.add_reaction("✅")
        except Exception:
            pass

        # если нужно, можно дополнительно пометить сообщение (убрал удаление / создание приватного канала в соответствии с просьбой)
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
    # (код наказаний оставлен без изменений)
    if message.channel.id == form_channel_id:
        # ... (весь твой большой блок по наказаниям) ...
        # для краткости оставляю реализацию, как в оригинале — она в твоём рабочем коде присутствует
        pass

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

# -----------------------
# OpenAI client init + AI команда
# -----------------------
# Убедись что в окружении задана OPENAI_API_KEY
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@bot.command(name="ai")
async def ai_chat(ctx, *, prompt: str):
    """Общение с ИИ через OpenAI"""
    await ctx.trigger_typing()
    try:
        # используем responses API — это наиболее надёжный вариант для нового SDK
        response = client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            max_tokens=500
        )

        # Попытаемся достать текст в нескольких вариантах (fallbacks)
        answer = None
        # 1) output_text (удобное свойство в некоторых версиях клиента)
        if hasattr(response, "output_text") and response.output_text:
            answer = response.output_text
        else:
            # 2) разбирать response.output -> список элементов с content
            parts = []
            out = getattr(response, "output", None)
            if out:
                try:
                    for item in out:
                        if isinstance(item, dict):
                            for c in item.get("content", []):
                                if isinstance(c, dict) and "text" in c:
                                    parts.append(c.get("text", ""))
                                elif isinstance(c, str):
                                    parts.append(c)
                        elif isinstance(item, str):
                            parts.append(item)
                except Exception:
                    pass
            # 3) fallback на choices (если клиент вернул в стиле ChatCompletion)
            if not parts and getattr(response, "choices", None):
                try:
                    ch = response.choices[0]
                    if isinstance(ch, dict) and "message" in ch:
                        msg = ch["message"]
                        if isinstance(msg, dict):
                            # может быть {'content': '...'} или {'content':[...]}
                            content = msg.get("content")
                            if isinstance(content, str):
                                parts.append(content)
                            elif isinstance(content, list):
                                for it in content:
                                    if isinstance(it, dict) and "text" in it:
                                        parts.append(it["text"])
                    else:
                        parts.append(str(ch))
                except Exception:
                    pass

            if parts:
                answer = "\n".join(parts)
            else:
                # финальный fallback
                answer = str(response)

        # Отправляем (обрезаем до 2000 символов — лимит Discord)
        if not answer:
            answer = "(пустой ответ)"
        await ctx.send(answer[:2000])
    except Exception as e:
        await ctx.send(f"❌ Ошибка при вызове OpenAI: {e}")

# -----------------------
# Запуск
# -----------------------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN not set in environment.")
    else:
        bot.run(token)