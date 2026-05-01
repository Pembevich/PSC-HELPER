import discord
from discord import Embed, Color
from discord.ext import commands
import logging

from moderation import (
    check_and_handle_urls,
    handle_spam_if_needed,
    detect_advertising_or_scam_text,
    detect_attachment_violations,
    apply_max_timeout,
    log_violation_with_evidence,
)

logger = logging.getLogger(__name__)

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        try:
            if await check_and_handle_urls(message):
                return
        except Exception as e:
            logger.error(f"Ошибка проверки ссылок: {e}", exc_info=True)
            return

        try:
            if await handle_spam_if_needed(message):
                return
        except Exception as e:
            logger.error(f"Ошибка проверки на спам: {e}", exc_info=True)

        try:
            text_reasons = detect_advertising_or_scam_text(message.content or "")
            attachment_reasons = await detect_attachment_violations(message.attachments, message.content or "")
            moderation_reasons = text_reasons + attachment_reasons
            
            if moderation_reasons:
                try:
                    await message.delete()
                except Exception:
                    pass
                await apply_max_timeout(message.author, "Автомодерация: реклама/NSFW/вложения")
                await log_violation_with_evidence(message, "🚨 Автомодерация: нарушение контента", moderation_reasons)
                try:
                    dm = Embed(
                        title="🚫 Сообщение удалено",
                        description="Обнаружены признаки рекламы/скама или нерелевантного медиа. Выдано ограничение голоса на 24 часа.",
                        color=Color.red()
                    )
                    dm.add_field(name="Причины", value="\n".join(f"• {r}" for r in moderation_reasons)[:1024], inline=False)
                    await message.author.send(embed=dm)
                except Exception:
                    pass
                return
        except Exception as e:
            logger.error(f"Ошибка автомодерации медиа/текста: {e}", exc_info=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
