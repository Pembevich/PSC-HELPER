import discord
from discord.ext import commands
import logging

from pos_ai import handle_pos_ai, ask_pos
from commands import _is_image_attachment

logger = logging.getLogger(__name__)

class AIChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        try:
            if await handle_pos_ai(message, self.bot):
                return
        except Exception as e:
            logger.error(f"Error in handle_pos_ai: {e}", exc_info=True)

    @commands.command()
    async def ai(self, ctx: commands.Context, *, question: str):
        image_urls = [attachment.url for attachment in ctx.message.attachments if _is_image_attachment(attachment)]
        async with ctx.typing():
            reply = await ask_pos(question, image_urls=image_urls, author_name=ctx.author.display_name, bot=self.bot)

        if not reply:
            await ctx.send("P.OS сейчас молчит как сервер в понедельник утром. Проверь AI-ключ и модель в Railway.")
            return

        chunks = [reply[i:i + 1900] for i in range(0, len(reply), 1900)]
        for chunk in chunks:
            await ctx.send(chunk, allowed_mentions=discord.AllowedMentions.none())

async def setup(bot: commands.Bot):
    await bot.add_cog(AIChatCog(bot))
