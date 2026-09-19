"""Discord event adapters for persistent P.OS workflows."""
from discord.ext import commands
import discord

from automation_rules import AutomationRuntime


class AutomationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.runtime = AutomationRuntime(bot)

    async def cog_unload(self) -> None:
        await self.runtime.close()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.runtime.handle_event("member_join", member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.runtime.handle_event("member_remove", member)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None and isinstance(message.author, discord.Member):
            await self.runtime.handle_event("message_create", message.author, message=message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationsCog(bot))
