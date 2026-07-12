import discord
from discord import Embed, Color
from discord.ext import commands
import logging

from moderation import (
    check_and_handle_urls,
    handle_spam_if_needed,
    detect_advertising_or_scam_text,
    detect_attachment_violations,
    detect_mention_spam,
    detect_crosschannel_spam,
    classify_text_with_ai,
    apply_max_timeout,
    log_violation_with_evidence,
)
from guild_config import get_settings as get_guild_settings
from logging_utils import is_log_channel, send_log_embed
from config import FORM_CHANNEL_ID, COMPLAINT_INPUT_CHANNEL, form_channel_id, POS_OWNER_USER_IDS
from message_gate import begin_moderation, finish_moderation
import antiraid

logger = logging.getLogger(__name__)

# Функциональные каналы со структурированным вводом (заявки/жалобы/наказания):
# здесь контент обрабатывают коги форм, автомодерация их трогать НЕ должна,
# иначе она удаляет/таймаутит легитимные заявки. Логи тоже не модерируем.
NO_MODERATION_CHANNEL_IDS = {
    FORM_CHANNEL_ID,            # заявки Arbaiter (1510201947024789665)
    COMPLAINT_INPUT_CHANNEL,    # жалобы
    form_channel_id,            # форма наказаний
}


def _partition_attachment_findings(reasons: list[str]) -> tuple[list[str], list[str]]:
    confirmed = [
        reason
        for reason in reasons
        if reason.startswith(("Подтверждено-медиа:", "Опасный-файл:"))
    ]
    review_only = [reason for reason in reasons if reason not in confirmed]
    return confirmed, review_only


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _punish(
        self,
        message: discord.Message,
        title: str,
        reasons: list[str],
        dm_text: str,
        *,
        apply_timeout: bool = True,
    ) -> None:
        """Удалить сообщение, выдать тайм-аут, залогировать и уведомить автора."""
        try:
            await message.delete()
        except Exception:
            pass
        timed_out = False
        if apply_timeout and isinstance(message.author, discord.Member):
            try:
                timed_out = bool(await apply_max_timeout(message.author, f"Автомодерация: {title}"))
            except Exception:
                pass
        try:
            await log_violation_with_evidence(message, f"🚨 {title}", reasons)
        except Exception:
            pass
        try:
            # Пишем про ограничение только если тайм-аут реально применился.
            if not timed_out:
                dm_text = dm_text.replace(" Выдано ограничение.", "").strip()
            dm = Embed(title="🚫 Сообщение удалено", description=dm_text, color=Color.red())
            dm.add_field(name="Причины", value="\n".join(f"• {r}" for r in reasons)[:1024], inline=False)
            await message.author.send(embed=dm)
        except Exception:
            pass

    @staticmethod
    def _is_staff(message: discord.Message) -> bool:
        if message.author.id in POS_OWNER_USER_IDS:
            return True
        perms = getattr(message.author, "guild_permissions", None)
        return bool(perms and (perms.administrator or perms.manage_messages))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        begin_moderation(message.id)
        blocked = False
        try:
            blocked = await self._moderate_message(message)
        except Exception as exc:
            blocked = True
            logger.error("Unhandled moderation error for message %s: %s", message.id, exc, exc_info=True)
        finally:
            finish_moderation(message.id, blocked)

    async def _moderate_message(self, message: discord.Message) -> bool:
        guild = message.guild
        if guild is None:
            return False
        if is_log_channel(message.channel):
            return False

        try:
            settings = await get_guild_settings(guild.id)
        except Exception:
            settings = {}
        if not settings.get("enabled", True):
            return False

        is_staff = self._is_staff(message)
        functional_channel = message.channel.id in NO_MODERATION_CHANNEL_IDS
        in_raid = antiraid.is_raid_mode(guild.id)

        # Malware/phishing checks also apply to staff and structured form channels.
        if settings.get("filter_scam", True) or settings.get("filter_ads", True):
            try:
                if await check_and_handle_urls(
                    message,
                    apply_timeout=not is_staff,
                    check_ads=bool(settings.get("filter_ads", True)),
                    check_scam=bool(settings.get("filter_scam", True)),
                ):
                    return True
            except Exception as exc:
                logger.error("Ошибка проверки ссылок: %s", exc, exc_info=True)

        if is_staff or functional_channel:
            if settings.get("filter_nsfw", True) or settings.get("filter_ads", True):
                try:
                    attachment_reasons = await detect_attachment_violations(
                        message.attachments,
                        message.content or "",
                        message.author.id,
                        check_nsfw=bool(settings.get("filter_nsfw", True)),
                        check_ads=bool(settings.get("filter_ads", True)),
                    )
                    deterministic, ai_only = _partition_attachment_findings(attachment_reasons)
                    if deterministic:
                        await self._punish(
                            message,
                            "опасное вложение",
                            deterministic + ai_only,
                            "Вложение содержит признаки запрещённого или вредоносного контента. Выдано ограничение.",
                            apply_timeout=not is_staff,
                        )
                        return True
                    if ai_only:
                        await send_log_embed(
                            message.guild,
                            "security",
                            "⚠️ AI-флаг медиа требует проверки",
                            f"Сообщение `{message.id}` от {message.author.mention} не удалено автоматически.",
                            color=Color.orange(),
                            fields=[("Наблюдения", "\n".join(ai_only)[:1024], False)],
                        )
                except Exception as exc:
                    logger.error("Ошибка проверки вложений в служебном канале: %s", exc, exc_info=True)
            return False

        # 1. Mass mentions and behavioral spam apply only to ordinary members.
        if settings.get("filter_mention_spam", True):
            try:
                mention_reasons = detect_mention_spam(message, int(settings.get("mention_limit", 6) or 6))
                if mention_reasons:
                    await self._punish(
                        message, "масс-пинг / спам упоминаний", mention_reasons,
                        "Слишком много упоминаний / несанкционированный @everyone. Выдано ограничение.",
                    )
                    return True
            except Exception as e:
                logger.error(f"Ошибка проверки упоминаний: {e}", exc_info=True)

        # 2. Cross-channel spam.
        if settings.get("filter_crosschannel", True):
            try:
                cross_reasons = detect_crosschannel_spam(
                    message,
                    int(settings.get("crosschannel_window_seconds", 15) or 15),
                    int(settings.get("crosschannel_channels_threshold", 3) or 3),
                )
                if cross_reasons:
                    await self._punish(
                        message, "кросс-канальный спам", cross_reasons,
                        "Одинаковое сообщение разослано по нескольким каналам. Выдано ограничение.",
                    )
                    return True
            except Exception as e:
                logger.error(f"Ошибка проверки кросс-канального спама: {e}", exc_info=True)

        # 3. Duplicate spam / flood.
        try:
            if await handle_spam_if_needed(message):
                return True
        except Exception as e:
            logger.error(f"Ошибка проверки на спам: {e}", exc_info=True)

        # 4. Content. AI-only findings are review signals, never sole punishment.
        try:
            text_reasons = detect_advertising_or_scam_text(message.content or "") if settings.get("filter_ads", True) else []
            attachment_reasons = (
                await detect_attachment_violations(
                    message.attachments,
                    message.content or "",
                    message.author.id,
                    check_nsfw=bool(settings.get("filter_nsfw", True)),
                    check_ads=bool(settings.get("filter_ads", True)),
                )
                if settings.get("filter_nsfw", True) or settings.get("filter_ads", True)
                else []
            )
            ai_reasons = []
            if settings.get("ai_moderation", True):
                ai_reasons = await classify_text_with_ai(
                    message.content or "",
                    message.author.id,
                    guild.id,
                    in_raid=in_raid,
                )

            deterministic_attachment, ai_media_reasons = _partition_attachment_findings(attachment_reasons)
            deterministic_reasons = text_reasons + deterministic_attachment
            ai_findings = ai_reasons + ai_media_reasons
            if deterministic_reasons:
                await self._punish(
                    message, "нарушение контента", deterministic_reasons + ai_findings,
                    "Обнаружены признаки рекламы/скама/спама или нерелевантного медиа. Выдано ограничение.",
                )
                return True
            if ai_findings:
                await send_log_embed(
                    message.guild,
                    "security",
                    "⚠️ AI-флаг требует ручной проверки",
                    f"Сообщение `{message.id}` от {message.author.mention} не удалено автоматически.",
                    color=Color.orange(),
                    fields=[("Наблюдения", "\n".join(ai_findings)[:1024], False)],
                )
        except Exception as e:
            logger.error(f"Ошибка автомодерации медиа/текста: {e}", exc_info=True)
        return False


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
