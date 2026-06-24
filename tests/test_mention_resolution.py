import unittest
from types import SimpleNamespace

import pos_ai


def _member(uid, display, name=None):
    return SimpleNamespace(id=uid, display_name=display, name=name or display)


def _role(rid, name):
    return SimpleNamespace(id=rid, name=name)


def _channel(cid, name):
    return SimpleNamespace(id=cid, name=name)


class _FakeGuild:
    def __init__(self, members=None, roles=None, channels=None):
        self._members = {m.id: m for m in (members or [])}
        self._roles = {r.id: r for r in (roles or [])}
        self._channels = {c.id: c for c in (channels or [])}

    def get_member(self, uid):
        return self._members.get(uid)

    def get_role(self, rid):
        return self._roles.get(rid)

    def get_channel(self, cid):
        return self._channels.get(cid)


def _msg(content, *, guild=None, mentions=None, role_mentions=None, channel_mentions=None):
    return SimpleNamespace(
        content=content,
        guild=guild,
        mentions=mentions or [],
        role_mentions=role_mentions or [],
        channel_mentions=channel_mentions or [],
    )


class MentionResolutionTests(unittest.TestCase):
    def test_resolves_user_mention_from_message_entities(self):
        u = _member(111111111111111111, "Вася", "vasya")
        m = _msg("привет <@111111111111111111> как дела", mentions=[u])
        out = pos_ai._resolve_mentions_text(m.content, m)
        self.assertIn("@Вася(ID:111111111111111111)", out)
        self.assertNotIn("<@111111111111111111>", out)

    def test_resolves_bang_mention(self):
        u = _member(222222222222222222, "Петя", "petya")
        m = _msg("<@!222222222222222222> здесь?", mentions=[u])
        out = pos_ai._resolve_mentions_text(m.content, m)
        self.assertIn("@Петя(ID:222222222222222222)", out)

    def test_resolves_role_and_channel(self):
        r = _role(333333333333333333, "Админы")
        c = _channel(444444444444444444, "общий")
        m = _msg("эй <@&333333333333333333> в <#444444444444444444>",
                 role_mentions=[r], channel_mentions=[c])
        out = pos_ai._resolve_mentions_text(m.content, m)
        self.assertIn("@Админы", out)
        self.assertIn("#общий", out)

    def test_leftover_resolved_via_guild(self):
        # Меншен есть в тексте, но НЕ в message.mentions (промах кэша) — добор по гильдии.
        u = _member(555555555555555555, "Гость", "guest")
        g = _FakeGuild(members=[u])
        m = _msg("кто это <@555555555555555555>?", guild=g, mentions=[])
        out = pos_ai._resolve_mentions_text(m.content, m)
        self.assertIn("@Гость(ID:555555555555555555)", out)

    def test_unknown_user_marked_not_invented(self):
        g = _FakeGuild()
        m = _msg("<@999999999999999999> ?", guild=g, mentions=[])
        out = pos_ai._resolve_mentions_text(m.content, m)
        self.assertIn("неизвестный_участник(ID:999999999999999999)", out)

    def test_bot_mention_left_for_stripping(self):
        bot_id = 777777777777777777
        m = _msg("<@777777777777777777> привет", mentions=[_member(bot_id, "P.OS")])
        out = pos_ai._resolve_mentions_text(m.content, m, bot_id=bot_id)
        # Меншен бота не разрешаем — его срежет _strip_bot_mention отдельно.
        self.assertIn("<@777777777777777777>", out)
        stripped = pos_ai._strip_bot_mention(out, bot_id)
        self.assertEqual(stripped, "привет")

    def test_plain_text_untouched(self):
        m = _msg("обычный текст без упоминаний")
        self.assertEqual(pos_ai._resolve_mentions_text(m.content, m), "обычный текст без упоминаний")


if __name__ == "__main__":
    unittest.main()
