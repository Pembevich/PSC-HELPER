import discord
from discord.ext import commands
import logging

from pos_ai import handle_pos_ai

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

async def setup(bot: commands.Bot):
    await bot.add_cog(AIChatCog(bot))
