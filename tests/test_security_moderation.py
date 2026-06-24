import time
import unittest
from types import SimpleNamespace

import moderation


def _msg(content="", *, mentions=0, role_mentions=0, mention_everyone=False,
         can_mention_everyone=False, author_id=1, channel_id=10):
    perms = SimpleNamespace(mention_everyone=can_mention_everyone)
    author = SimpleNamespace(id=author_id, guild_permissions=perms)
    return SimpleNamespace(
        content=content,
        mentions=[SimpleNamespace(id=i) for i in range(mentions)],
        role_mentions=[SimpleNamespace(id=i) for i in range(role_mentions)],
        mention_everyone=mention_everyone,
        author=author,
        channel=SimpleNamespace(id=channel_id),
        attachments=[],
    )


class MentionSpamTests(unittest.TestCase):
    def test_too_many_mentions(self):
        m = _msg("спам", mentions=7)
        self.assertTrue(moderation.detect_mention_spam(m, 6))

    def test_under_limit_ok(self):
        m = _msg("привет", mentions=2)
        self.assertEqual(moderation.detect_mention_spam(m, 6), [])

    def test_everyone_without_perms_flagged(self):
        m = _msg("@everyone налетай", mention_everyone=True, can_mention_everyone=False)
        self.assertTrue(moderation.detect_mention_spam(m, 6))

    def test_everyone_with_perms_allowed(self):
        m = _msg("@everyone важное", mention_everyone=True, can_mention_everyone=True)
        self.assertEqual(moderation.detect_mention_spam(m, 6), [])


class CrossChannelTests(unittest.TestCase):
    def setUp(self):
        moderation.recent_messages.clear()

    def tearDown(self):
        moderation.recent_messages.clear()

    def test_same_text_across_channels(self):
        now = time.time()
        key = moderation.message_key_for_spam(_msg("купи дёшево", author_id=5, channel_id=1))
        # имитируем, что то же сообщение уже было в каналах 1 и 2
        moderation.recent_messages[5].append((key, now, 111, 1))
        moderation.recent_messages[5].append((key, now, 222, 2))
        m = _msg("купи дёшево", author_id=5, channel_id=3)
        reasons = moderation.detect_crosschannel_spam(m, 15, 3)
        self.assertTrue(reasons)

    def test_single_channel_ok(self):
        now = time.time()
        m = _msg("обычное сообщение", author_id=6, channel_id=1)
        key = moderation.message_key_for_spam(m)
        moderation.recent_messages[6].append((key, now, 111, 1))
        self.assertEqual(moderation.detect_crosschannel_spam(m, 15, 3), [])


class AiTextTriggerTests(unittest.TestCase):
    def test_link_triggers_review(self):
        self.assertTrue(moderation._text_warrants_ai_review("зайди на https://x.ru тут круто"))

    def test_invite_triggers_review(self):
        self.assertTrue(moderation._text_warrants_ai_review("залетай discord.gg/abcde"))

    def test_plain_chat_no_review(self):
        self.assertFalse(moderation._text_warrants_ai_review("да норм, согласен"))

    def test_short_text_no_review(self):
        self.assertFalse(moderation._text_warrants_ai_review("ок"))

    def test_raid_forces_review(self):
        self.assertTrue(moderation._text_warrants_ai_review("привет всем", in_raid=True))


if __name__ == "__main__":
    unittest.main()
