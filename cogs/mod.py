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
import antiraid

logger = logging.getLogger(__name__)


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _punish(self, message: discord.Message, title: str, reasons: list[str], dm_text: str) -> None:
        """Удалить сообщение, выдать тайм-аут, залогировать и уведомить автора."""
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await apply_max_timeout(message.author, f"Автомодерация: {title}")
        except Exception:
            pass
        try:
            await log_violation_with_evidence(message, f"🚨 {title}", reasons)
        except Exception:
            pass
        try:
            dm = Embed(title="🚫 Сообщение удалено", description=dm_text, color=Color.red())
            dm.add_field(name="Причины", value="\n".join(f"• {r}" for r in reasons)[:1024], inline=False)
            await message.author.send(embed=dm)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        # Настройки модерации на сервер (с дефолтами). Общий выключатель и
        # отдельные тумблеры фильтров; править может только владелец через P.OS.
        try:
            settings = await get_guild_settings(message.guild.id)
        except Exception:
            settings = {}
        if not settings.get("enabled", True):
            return

        in_raid = antiraid.is_raid_mode(message.guild.id)

        # 1. Масс-пинг / @everyone-спам — дёшево и важно против рейдов.
        if settings.get("filter_mention_spam", True):
            try:
                mention_reasons = detect_mention_spam(message, int(settings.get("mention_limit", 6) or 6))
                if mention_reasons:
                    await self._punish(
                        message, "масс-пинг / спам упоминаний", mention_reasons,
                        "Слишком много упоминаний / несанкционированный @everyone. Выдано ограничение.",
                    )
                    return
            except Exception as e:
                logger.error(f"Ошибка проверки упоминаний: {e}", exc_info=True)

        # 2. Ссылки = скам/фишинг/реклама.
        if settings.get("filter_scam", True) or settings.get("filter_ads", True):
            try:
                if await check_and_handle_urls(message):
                    return
            except Exception as e:
                logger.error(f"Ошибка проверки ссылок: {e}", exc_info=True)

        # 3. Кросс-канальный спам (одно сообщение веером по каналам).
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
                    return
            except Exception as e:
                logger.error(f"Ошибка проверки кросс-канального спама: {e}", exc_info=True)

        # 4. Спам (дубли) / флуд (темп) — handle_spam_if_needed сам читает настройки.
        try:
            if await handle_spam_if_needed(message):
                return
        except Exception as e:
            logger.error(f"Ошибка проверки на спам: {e}", exc_info=True)

        # 5. Текст/вложения: ключевые слова + ИИ-второе мнение (Gemini).
        try:
            text_reasons = detect_advertising_or_scam_text(message.content or "") if settings.get("filter_ads", True) else []
            attachment_reasons = (
                await detect_attachment_violations(message.attachments, message.content or "", message.author.id)
                if settings.get("filter_nsfw", True) or settings.get("filter_ads", True)
                else []
            )
            ai_reasons = []
            if settings.get("ai_moderation", True) and not text_reasons:
                ai_reasons = await classify_text_with_ai(message.content or "", message.author.id, in_raid=in_raid)

            moderation_reasons = text_reasons + attachment_reasons + ai_reasons
            if moderation_reasons:
                await self._punish(
                    message, "нарушение контента", moderation_reasons,
                    "Обнаружены признаки рекламы/скама/спама или нерелевантного медиа. Выдано ограничение.",
                )
                return
        except Exception as e:
            logger.error(f"Ошибка автомодерации медиа/текста: {e}", exc_info=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
