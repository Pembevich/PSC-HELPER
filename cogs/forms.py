import os
import uuid
import discord
from discord import Embed, Color
from discord.ext import commands
import logging

from config import (
    FORM_CHANNEL_ID,
    TAC_CHANNEL_ID,
    TAC_REVIEWER_ROLE_IDS,
    TAC_ROLE_REWARDS,
    PSC_CHANNEL_ID,
    PING_ROLE_ID,
    COMPLAINT_INPUT_CHANNEL,
    COMPLAINT_NOTIFY_ROLE,
    form_channel_id,
    punishment_roles,
    squad_roles,
    TARGET_CHANNELS,
    TARGET_OUTPUT_CHANNEL,
)
from forms import ConfirmView, ComplaintView
from utils import extract_clean_keyword, assess_applicant_risk, safe_send_dm
from logging_utils import send_log_embed

logger = logging.getLogger(__name__)

class FormsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        # Авто-отписки
        try:
            if message.channel.id in TARGET_CHANNELS and "ОТПИСКИ" in (message.content or "").upper():
                today = discord.utils.utcnow().strftime("%d.%m.%Y")
                group = TARGET_CHANNELS[message.channel.id]
                title = f"Общее мероприятие [P.S.C] ({today}) - Отписки." if group == "P.S.C" else f"Мероприятие [{group}] ({today}) - Отписки."
                embed = Embed(title=title, color=Color.from_rgb(255, 255, 255))
                target_channel = self.bot.get_channel(TARGET_OUTPUT_CHANNEL)
                if target_channel:
                    try:
                        sent = await target_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                        try:
                            await sent.create_thread(name=f"Отписки • {group}")
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error(f"Ошибка при отправке эмбеда/создании ветки: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Ошибка авто-отписок: {e}", exc_info=True)

        # PSC EMBED СООБЩЕНИЯ
        if message.channel.id == PSC_CHANNEL_ID:
            content_strip = (message.content or "").strip()
            if content_strip.upper().startswith("С ВЕБХУКОМ"):
                try:
                    await message.delete()
                except Exception:
                    pass

                role_ping = message.guild.get_role(PING_ROLE_ID)
                if role_ping:
                    try:
                        await message.channel.send(role_ping.mention)
                    except Exception:
                        pass

                content_without_flag = content_strip[len("С ВЕБХУКОМ"):].strip()
                file_to_send = None
                embed = Embed(description=content_without_flag or "(без текста)", color=Color.from_rgb(255, 255, 255))
                embed.set_footer(text=f"©Provision Security Complex | {discord.utils.utcnow().strftime('%d.%m.%Y')}")
                if message.attachments:
                    att = message.attachments[0]
                    try:
                        os.makedirs("temp", exist_ok=True)
                        fname = f"temp/{uuid.uuid4().hex}-{att.filename}"
                        await att.save(fname)
                        file_to_send = discord.File(fname, filename=att.filename)
                        embed.set_image(url=f"attachment://{att.filename}")
                    except Exception as e:
                        logger.error(f"Failed to save attachment for PSC embed: {e}", exc_info=True)
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
                except Exception as e:
                    logger.error(f"Failed to send PSC embed: {e}", exc_info=True)
                return

        # Жалобы
        if message.channel.id == COMPLAINT_INPUT_CHANNEL:
            lines = [ln.strip() for ln in (message.content or "").split("\n") if ln.strip()]
            if len(lines) < 2:
                try:
                    await message.delete()
                except Exception:
                    pass
                template = "1. Никнейм нарушителя\n2. Суть нарушения\n3. Доказательства (по желанию)"
                em = Embed(title="❌ Неверная форма жалобы", description="В форме должно быть минимум 2 строки: никнейм и суть нарушения.", color=Color.red())
                em.add_field(name="Пример", value=f"```{template}```", inline=False)
                await safe_send_dm(message.author, em)
                return

            try:
                await message.delete()
            except Exception:
                pass

            guild = message.guild
            category = message.channel.category
            existing = [ch for ch in (category.channels if category else guild.channels) if ch.name.startswith("жалоба-")]
            index = len(existing) + 1
            channel_name = f"жалоба-{index}"

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                message.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            }
            for role in guild.roles:
                try:
                    if role.permissions.administrator or role.permissions.manage_guild:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                except Exception:
                    pass

            try:
                complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, category=category, reason=f"Новая жалоба от {message.author}")
            except Exception:
                try:
                    complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, reason=f"Новая жалоба от {message.author}")
                except Exception as e:
                    logger.error(f"Failed to create complaint channel: {e}", exc_info=True)
                    complaint_chan = None

            try:
                await send_log_embed(
                    guild,
                    "forms",
                    "📢 Новая жалоба",
                    f"Создан канал жалобы для {message.author.mention}.",
                    color=Color.blue(),
                    fields=[
                        ("Канал", complaint_chan.mention if complaint_chan else "не создан", False),
                        ("Категория", category.name if category else "—", False)
                    ]
                )
            except Exception:
                pass

            embed = Embed(title="📢 Новая жалоба", color=Color.blue())
            embed.add_field(name="Отправитель", value=f"{message.author.mention} (ID: {message.author.id})", inline=False)
            full_text = "\n".join(lines)
            embed.add_field(name="Жалоба", value=f"```{full_text[:1900]}```", inline=False)
            embed.set_footer(text=f"ID жалобы: {index} | {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}")
            notify_role = guild.get_role(COMPLAINT_NOTIFY_ROLE)
            
            if complaint_chan:
                try:
                    if notify_role:
                        await complaint_chan.send(notify_role.mention)
                    view = ComplaintView(submitter=message.author, complaint_channel_id=complaint_chan.id)
                    await complaint_chan.send(embed=embed, view=view)

                    if message.attachments:
                        files = []
                        for att in message.attachments[:5]:
                            try:
                                files.append(await att.to_file())
                            except Exception:
                                pass
                        if files:
                            await complaint_chan.send(content="📎 Приложенные доказательства от автора жалобы:", files=files)
                except Exception as e:
                    logger.error(f"Failed to send complaint embed/files: {e}", exc_info=True)
            return

        # Форма TAC
        if message.channel.id == FORM_CHANNEL_ID:
            template = "1. Ваш roblox никнейм (НЕ ДИСПЛЕЙ)\n2. Ваш Discord ник\n3. tac"
            lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
            if len(lines) != 3:
                try:
                    await message.reply(embed=Embed(title="❌ Неверный шаблон", description="Форма должна содержать 3 строки.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass
                return

            user_line, discord_nick_line, choice_line = lines
            keyword = extract_clean_keyword(choice_line)

            if keyword != "tac":
                try:
                    await message.reply(embed=Embed(title="❌ Неизвестный отряд", description="Третий пункт должен содержать только **tac**.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass
                return

            target_channel = message.guild.get_channel(TAC_CHANNEL_ID)
            if not target_channel:
                try:
                    await message.reply("❌ Ошибка конфигурации: канал TAC не найден.")
                except Exception:
                    pass
                return

            risk_flags = assess_applicant_risk(user_line, discord_nick_line, message.author)
            risk_text = "\n".join(f"⚠️ {flag}" for flag in risk_flags) if risk_flags else "✅ Явных рисков не обнаружено"

            embed = Embed(
                title="📋 Подтверждение вступления в TAC",
                description=(
                    f"{message.author.mention} хочет вступить в отряд **TAC**\n"
                    f"Roblox ник: `{user_line}`\n"
                    f"Discord ник: `{discord_nick_line}`"
                ),
                color=Color.blue()
            )
            embed.add_field(name="Автопроверка кандидата", value=risk_text[:1024], inline=False)
            embed.set_footer(text=f"ID пользователя: {message.author.id} | {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}")

            view = ConfirmView(
                TAC_REVIEWER_ROLE_IDS,
                target_message=message,
                squad_name="TAC",
                role_ids=TAC_ROLE_REWARDS,
                target_user_id=message.author.id
            )

            mentions = []
            for rid in TAC_REVIEWER_ROLE_IDS:
                role = message.guild.get_role(rid)
                if role:
                    mentions.append(role.mention)

            try:
                if mentions:
                    await target_channel.send(content=" ".join(mentions), embed=embed, view=view)
                else:
                    await target_channel.send(embed=embed, view=view)
            except Exception as e:
                logger.error(f"Failed to send TAC form embed: {e}", exc_info=True)
            return

        # Форма наказаний
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
                return

            member = message.guild.get_member(user_id)
            if not member:
                try:
                    await message.reply("❌ Пользователь с таким ID не найден на сервере.")
                except Exception:
                    pass
                return

            roles = member.roles

            async def log_action(text, color=Color.orange()):
                await send_log_embed(
                    message.guild,
                    "moderation",
                    "📋 Лог наказаний",
                    text,
                    color=color
                )

            async def apply_roles(to_add, to_remove):
                for r in to_remove:
                    if r in roles:
                        try:
                            await member.remove_roles(r)
                        except Exception as e:
                            logger.error(f"Failed to remove role: {e}")
                for r in to_add:
                    if r not in roles:
                        try:
                            await member.add_roles(r)
                        except Exception as e:
                            logger.error(f"Failed to add role: {e}")

            punish_1 = message.guild.get_role(punishment_roles["1 выговор"])
            punish_2 = message.guild.get_role(punishment_roles["2 выговора"])
            strike_1 = message.guild.get_role(punishment_roles["1 страйк"])
            strike_2 = message.guild.get_role(punishment_roles["2 страйка"])

            if all(r in roles for r in [punish_1, punish_2, strike_1, strike_2]):
                notify = None
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
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
                    notify = None
                    if squad_roles["got_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["got_notify"])
                    elif squad_roles["cesu_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["cesu_notify"])
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
                    notify = None
                    if squad_roles["got_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["got_notify"])
                    elif squad_roles["cesu_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["cesu_notify"])
                    if notify:
                        await log_action(f"{notify.mention}\nСотрудник {member.mention} получил 3-й страйк. Подлежит увольнению.")
                else:
                    await apply_roles([strike_1, strike_2], [])
                    await log_action(f"{member.mention} получил 2 страйка.")
            return

async def setup(bot: commands.Bot):
    await bot.add_cog(FormsCog(bot))
