import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import discord

# We will mock modules that pos_ai.py imports to avoid side effects or database connection attempts
with patch("storage.add_entry"), \
     patch("storage.get_ai_context"), \
     patch("storage.update_ai_context"):
    from pos_ai import _build_messages


class BuildMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_messages_formatting(self):
        # Mock bot
        bot = MagicMock(spec=discord.Client)
        bot.user = MagicMock(spec=discord.ClientUser)
        bot.user.id = 999999
        bot.user.display_name = "P.OS"
        bot.user.name = "pos_bot"

        # Mock authors
        pumba = MagicMock(spec=discord.Member)
        pumba.id = 968698192411652176
        pumba.display_name = "Pumba"
        pumba.name = "pumba"
        pumba.bot = False
        pumba.roles = []

        vasya = MagicMock(spec=discord.Member)
        vasya.id = 123456
        vasya.display_name = "Vasya"
        vasya.name = "vasya"
        vasya.bot = False
        vasya.roles = []

        # Mock messages in history
        # M1: Pumba says Hello
        m1 = MagicMock(spec=discord.Message)
        m1.id = 100001
        m1.author = pumba
        m1.content = "Привет, P.OS"
        m1.reference = None

        # M2: Bot replies to M1
        m2 = MagicMock(spec=discord.Message)
        m2.id = 100002
        m2.author = bot.user
        m2.content = "Привет, создатель."
        m2.reference = MagicMock(spec=discord.MessageReference)
        m2.reference.message_id = 100001
        m2.reference.resolved = m1

        # M3: Vasya says How are you?
        m3 = MagicMock(spec=discord.Message)
        m3.id = 100003
        m3.author = vasya
        m3.content = "Как дела?"
        m3.reference = None

        # Current message: Pumba asking another question
        msg = MagicMock(spec=discord.Message)
        msg.id = 100004
        msg.author = pumba
        msg.content = "Тест"
        msg.guild = MagicMock(spec=discord.Guild)
        msg.guild.id = 8888
        msg.guild.name = "Test Server"

        # Mock channel history
        # Note: history returns messages from newest to oldest (excluding current msg)
        async def mock_history(*args, **kwargs):
            for m in [m3, m2, m1]:
                yield m

        msg.channel = MagicMock()
        msg.channel.history = mock_history

        # Mock context formatting to keep tests simple
        with patch("pos_ai._format_guild_snapshot", return_value=""), \
             patch("pos_ai._format_author_profile", return_value=""), \
             patch("pos_ai._format_server_memory", return_value=""):
            
            result = await _build_messages(
                message=msg,
                bot=bot,
                ref_msg=None,
                use_system=True,
                include_others=True,
                max_context=10
            )

        # Let's inspect the constructed messages payload
        # Result should be:
        # 0: system instructions
        # 1..n: history messages in chronological order
        # last: current user message
        
        # Filter for non-system messages to check history & current msg
        chat_msgs = [m for m in result if m["role"] in ("user", "assistant")]
        
        self.assertEqual(len(chat_msgs), 4)
        
        # M1 (User)
        self.assertEqual(chat_msgs[0]["role"], "user")
        self.assertEqual(chat_msgs[0]["name"], "user_968698192411652176")
        self.assertIn("Pumba (@pumba, ID: 968698192411652176): Привет, P.OS", chat_msgs[0]["content"])
        
        # M2 (Assistant)
        self.assertEqual(chat_msgs[1]["role"], "assistant")
        self.assertEqual(chat_msgs[1]["name"], "bot_999999")
        self.assertIn("[Ответ пользователю Pumba (@pumba, ID: 968698192411652176)]", chat_msgs[1]["content"])
        self.assertIn("Привет, создатель.", chat_msgs[1]["content"])
        
        # M3 (User)
        self.assertEqual(chat_msgs[2]["role"], "user")
        self.assertEqual(chat_msgs[2]["name"], "user_123456")
        self.assertIn("Vasya (@vasya, ID: 123456): Как дела?", chat_msgs[2]["content"])
        
        # Current message (User)
        self.assertEqual(chat_msgs[3]["role"], "user")
        self.assertEqual(chat_msgs[3]["name"], "user_968698192411652176")
        self.assertIn("Pumba (@pumba, ID: 968698192411652176): Тест", chat_msgs[3]["content"])


if __name__ == "__main__":
    unittest.main()
