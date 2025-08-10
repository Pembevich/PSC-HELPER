# --- Импорты ---
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
from discord.ui import View, button
import os
import aiohttp
import requests

discord.VoiceClient = None

# --- Константы ---
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

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- База данных ---
conn = sqlite3.connect("bot_data.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS private_chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user1_id INTEGER, user2_id INTEGER, password TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, message TEXT, file BLOB, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

# --- Команда !gif ---
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

# --- /sbor ---
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

# --- /sbor_end ---
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

# --- on_ready ---
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    for guild_id in allowed_guild_ids:
        try:
            guild = discord.Object(id=guild_id)
            await bot.tree.sync(guild=guild)
            print(f"✅ Команды синхронизированы с сервером {guild_id}")
        except Exception as e:
            print(f"❌ Ошибка при синхронизации: {e}")

# --- Confirm View ---
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

# --- Обработка формы ---
def extract_clean_keyword(text: str):
    return re.sub(r"[^a-z]", "", text.lower())

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id == FORM_CHANNEL_ID:
        template = "1. Ваш никнейм\n2. Ваш Дискорд Ник\n3. got или cesu"
        lines = [line.strip() for line in message.content.strip().split("\n") if line.strip()]
        if len(lines) != 3:
            await message.reply(embed=Embed(title="❌ Неверный шаблон", description="Форма должна содержать 3 строки.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            return

        user_line, id_line, choice_line = lines
        keyword = extract_clean_keyword(choice_line)

        if keyword == "got":
            role_id, channel_id, rewards, squad = GOT_ROLE_ID, GOT_CHANNEL_ID, GOT_ROLE_REWARDS, "G.o.T"
        elif keyword == "cesu":
            role_id, channel_id, rewards, squad = CESU_ROLE_ID, CESU_CHANNEL_ID, CESU_ROLE_REWARDS, "C.E.S.U"
        else:
            await message.reply(embed=Embed(title="❌ Неизвестный отряд", description="Третий пункт должен содержать **только** got или cesu.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            return

        target_channel = message.guild.get_channel(channel_id)
        role_ping = message.guild.get_role(role_id)

        if not target_channel or not role_ping:
            await message.reply("❌ Ошибка конфигурации.")
            return

        embed = Embed(
            title=f"📋 Подтверждение вступления в {squad}",
            description=f"{message.author.mention} хочет вступить в отряд **{squad}**\nНикнейм: `{user_line}`\nID: `{id_line}`",
            color=Color.blue()
        )
        embed.set_footer(text=f"ID пользователя: {message.author.id} | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        view = ConfirmView(user_id=role_id, target_message=message, squad_name=squad, role_ids=rewards, target_user_id=message.author.id)

        await target_channel.send(content=role_ping.mention, embed=embed, view=view)

    await bot.process_commands(message)
# --- Конфигурация наказаний ---
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

log_channel_id = 1392125177399218186
form_channel_id = 1349725568371003392

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    # --- PSC EMBED СООБЩЕНИЯ ---
    PSC_CHANNEL_ID = 1340998084238381137
    PSC_LOGO_PATH = "photo-output.jpeg"  # Убедись, что файл уже сохранён рядом с main.py
    PING_ROLE_ID = 1341168051269275718  # ID роли для пинга

    if message.channel.id == PSC_CHANNEL_ID:
        if message.content.strip().upper().startswith("БЕЗ ВЕБХУКА"):
            return
        
        await message.delete()

        role_ping = message.guild.get_role(PING_ROLE_ID)
        if role_ping:
            await message.channel.send(role_ping.mention)

        embed = Embed(
            description=message.content,
            color=Color.from_rgb(255, 255, 255)
        )
        embed.set_image(url="attachment://photo-output.jpeg")
        embed.set_footer(text=f"©Provision Security Complex | {datetime.now().strftime('%d.%m.%Y')}")

        file = discord.File("photo-output.jpeg", filename="photo-output.jpeg")
        await message.channel.send(embed=embed, file=file)
        return
    # --- Обработка формы вступления ---
    if message.channel.id == FORM_CHANNEL_ID:
        template = "1. Ваш никнейм\n2. Ваш Дискорд Ник\n3. got или cesu"
        lines = [line.strip() for line in message.content.strip().split("\n") if line.strip()]
        if len(lines) != 3:
            await message.reply(embed=Embed(title="❌ Неверный шаблон", description="Форма должна содержать 3 строки.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            return

        user_line, id_line, choice_line = lines
        keyword = extract_clean_keyword(choice_line)

        if keyword == "got":
            role_id, channel_id, rewards, squad = GOT_ROLE_ID, GOT_CHANNEL_ID, GOT_ROLE_REWARDS, "G.o.T"
        elif keyword == "cesu":
            role_id, channel_id, rewards, squad = CESU_ROLE_ID, CESU_CHANNEL_ID, CESU_ROLE_REWARDS, "C.E.S.U"
        else:
            await message.reply(embed=Embed(title="❌ Неизвестный отряд", description="Третий пункт должен содержать **только** got или cesu.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            return

        target_channel = message.guild.get_channel(channel_id)
        role_ping = message.guild.get_role(role_id)

        if not target_channel or not role_ping:
            await message.reply("❌ Ошибка конфигурации.")
            return

        embed = Embed(
            title=f"📋 Подтверждение вступления в {squad}",
            description=f"{message.author.mention} хочет вступить в отряд **{squad}**\nНикнейм: `{user_line}`\nID: `{id_line}`",
            color=Color.blue()
        )
        embed.set_footer(text=f"ID пользователя: {message.author.id} | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        view = ConfirmView(user_id=role_id, target_message=message, squad_name=squad, role_ids=rewards, target_user_id=message.author.id)

        await target_channel.send(content=role_ping.mention, embed=embed, view=view)
        return

    # --- Обработка наказаний ---
    if message.channel.id == form_channel_id:
        template = (
            "Никнейм: Robloxer228\n"
            "Дискорд айди: 1234567890\n"
            "Наказание: 1 выговор / 2 выговора / 1 страйк / 2 страйка\n"
            "Причина: причина наказания\n"
            "Док-ва: (по желанию)"
        )

        lines = [line.strip() for line in message.content.strip().split("\n") if line.strip()]
        if len(lines) < 4 or len(lines) > 5:
            await message.reply(embed=Embed(
                title="❌ Ошибка",
                description="Форма должна содержать 4 или 5 строк.",
                color=Color.red()
            ).add_field(name="Пример", value=f"```{template}```"))
            return

        try:
            nickname = lines[0].split(":", 1)[1].strip()
            user_id = int(lines[1].split(":", 1)[1].strip())
            punishment = lines[2].split(":", 1)[1].strip().lower()
            reason = lines[3].split(":", 1)[1].strip()
        except:
            await message.reply(embed=Embed(
                title="❌ Ошибка в шаблоне",
                description="Проверь правильность полей (особенно Discord ID)",
                color=Color.red()
            ).add_field(name="Пример", value=f"```{template}```"))
            return

        member = message.guild.get_member(user_id)
        if not member:
            await message.reply("❌ Пользователь с таким ID не найден на сервере.")
            return

        roles = member.roles
        log = message.guild.get_channel(log_channel_id)

        async def log_action(text):
            if log:
                await log.send(embed=Embed(title="📋 Лог наказаний", description=text, color=Color.orange()))

        async def apply_roles(to_add, to_remove):
            for r in to_remove:
                if r in roles:
                    await member.remove_roles(r)
            for r in to_add:
                if r not in roles:
                    await member.add_roles(r)

        punish_1 = message.guild.get_role(punishment_roles["1 выговор"])
        punish_2 = message.guild.get_role(punishment_roles["2 выговора"])
        strike_1 = message.guild.get_role(punishment_roles["1 страйк"])
        strike_2 = message.guild.get_role(punishment_roles["2 страйка"])

        if all(r in roles for r in [punish_1, punish_2, strike_1, strike_2]):
            if squad_roles["got_base"] in [r.id for r in roles]:
                notify = message.guild.get_role(squad_roles["got_notify"])
            elif squad_roles["cesu_base"] in [r.id for r in roles]:
                notify = message.guild.get_role(squad_roles["cesu_notify"])
            else:
                notify = None
            if notify:
                await log_action(f"{notify.mention}\nСотрудник {member.mention} получил **максимальное количество наказаний** и подлежит **увольнению**.")
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
            await message.reply(embed=Embed(
                title="❌ Неизвестное наказание",
                description="Допустимые значения: `1 выговор`, `2 выговора`, `1 страйк`, `2 страйка`.",
                color=Color.red()
            ).add_field(name="Пример", value=f"```{template}```"))

bot.run(os.getenv("DISCORD_TOKEN"))
