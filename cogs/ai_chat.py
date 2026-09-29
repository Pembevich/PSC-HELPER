import discord
from discord.ext import commands
import logging
import time
from collections import OrderedDict

from pos_ai import handle_pos_ai
from message_gate import wait_for_moderation
from vacation_mode import handle_owner_vacation_ping

logger = logging.getLogger(__name__)
_HANDLED_MESSAGE_TTL = 3600.0
_MAX_HANDLED_MESSAGES = 5000

class AIChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._in_flight: set[int] = set()
        self._handled: OrderedDict[int, float] = OrderedDict()

    def _claim_message(self, message_id: int) -> bool:
        # No await before claiming: duplicate gateway deliveries cannot enter
        # moderation, vacation replies or the agent concurrently.
        cutoff = time.monotonic() - _HANDLED_MESSAGE_TTL
        while self._handled:
            oldest = next(iter(self._handled))
            if self._handled[oldest] > cutoff:
                break
            self._handled.popitem(last=False)
        if message_id in self._in_flight or message_id in self._handled:
            return False
        self._in_flight.add(message_id)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not self._claim_message(message.id):
            return

        try:
            moderation_result = await wait_for_moderation(message.id)
            if moderation_result is not False:
                if moderation_result is None:
                    logger.error("Moderation gate timed out for message %s; AI reply suppressed.", message.id)
                return
            if await handle_owner_vacation_ping(message):
                return
            if await handle_pos_ai(message, self.bot):
                return
        except Exception as e:
            logger.error(f"Error in handle_pos_ai: {e}", exc_info=True)
        finally:
            # An exception/cancellation may follow a delivered reply or an
            # attempted mutation. Never replay that message automatically.
            self._in_flight.discard(message.id)
            self._handled[message.id] = time.monotonic()
            while len(self._handled) > _MAX_HANDLED_MESSAGES:
                self._handled.popitem(last=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(AIChatCog(bot))
