from unittest import IsolatedAsyncioTestCase

from config import BOT_COMMAND_PREFIX
from main import COGS, create_bot


class ExtensionLoadingTests(IsolatedAsyncioTestCase):
    async def test_all_required_cogs_load_with_only_gif_command(self):
        bot = create_bot()
        loaded: list[str] = []
        try:
            for extension in COGS:
                await bot.load_extension(extension)
                loaded.append(extension)

            self.assertEqual(bot.tree.get_commands(), [])
            self.assertEqual({command.name for command in bot.commands}, {"gif"})
            self.assertEqual(bot.command_prefix, BOT_COMMAND_PREFIX)
            self.assertEqual(BOT_COMMAND_PREFIX, "p.")
            self.assertEqual(set(COGS), set(bot.extensions))
        finally:
            for extension in reversed(loaded):
                await bot.unload_extension(extension)
            await bot.close()
