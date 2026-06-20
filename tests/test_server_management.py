import unittest
from unittest.mock import patch

with patch("storage.add_entry"), \
     patch("storage.get_ai_context"), \
     patch("storage.update_ai_context"):
    import pos_ai
    from pos_ai import (
        _parse_bool, _summarize_tool_call,
        _OWNER_ONLY_TOOLS, _OWNER_INFO_TOOLS, _TOOL_ACTION_LABELS,
    )

from cogs.ai_tools import POS_AI_TOOLS


class ToolSchemaTests(unittest.TestCase):
    def test_all_new_tools_registered(self):
        names = {t["function"]["name"] for t in POS_AI_TOOLS}
        for expected in [
            "edit_role", "create_channel", "delete_channel", "edit_channel",
            "set_channel_permission", "kick_user", "set_nickname", "create_invite",
        ]:
            self.assertIn(expected, names, f"tool {expected} отсутствует в схеме")

    def test_management_tools_are_owner_only(self):
        # Все новые управляющие инструменты должны требовать прав владельца.
        for name in ["edit_role", "create_channel", "delete_channel", "edit_channel",
                     "set_channel_permission", "kick_user", "set_nickname", "create_invite"]:
            self.assertIn(name, _OWNER_ONLY_TOOLS)

    def test_every_owner_tool_has_label(self):
        for name in _OWNER_ONLY_TOOLS:
            self.assertIn(name, _TOOL_ACTION_LABELS, f"нет человекочитаемой метки для {name}")

    def test_schema_shapes_valid(self):
        for t in POS_AI_TOOLS:
            self.assertEqual(t["type"], "function")
            fn = t["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            self.assertIn("parameters", fn)
            self.assertEqual(fn["parameters"]["type"], "object")

    def test_list_servers_registered_and_owner_only(self):
        names = {t["function"]["name"] for t in POS_AI_TOOLS}
        self.assertIn("list_servers", names)
        self.assertIn("list_servers", _OWNER_ONLY_TOOLS)
        # list_servers — read-only owner info: для не-владельца отказ без подтверждения.
        self.assertIn("list_servers", _OWNER_INFO_TOOLS)

    def test_create_invite_supports_cross_server(self):
        ci = next(t for t in POS_AI_TOOLS if t["function"]["name"] == "create_invite")
        props = ci["function"]["parameters"]["properties"]
        self.assertIn("server_id_or_name", props)


class InviteBugRegressionTests(unittest.TestCase):
    """Баг: 'создай роль X' уходил в ветку инвайта, т.к. INVITE_PATTERN ловил 'создай'."""

    def test_conflicting_patterns_removed(self):
        # Эти паттерны были источником конфликта и должны быть удалены.
        for attr in ["INVITE_PATTERN", "ADD_ROLE_PATTERN", "REMOVE_ROLE_PATTERN"]:
            self.assertFalse(hasattr(pos_ai, attr), f"{attr} должен быть удалён, чтобы не перехватывать 'создай'")

    def test_critical_ban_patterns_kept(self):
        # Критичные бан/разбан остаются детерминированными ради надёжности.
        self.assertTrue(hasattr(pos_ai, "BAN_PATTERN"))
        self.assertTrue(hasattr(pos_ai, "UNBAN_PATTERN"))


class ParseBoolTests(unittest.TestCase):
    def test_truthy(self):
        for v in ["true", "1", "да", "yes", "on", "вкл", "True", "ДА"]:
            self.assertTrue(_parse_bool(v))

    def test_falsy(self):
        for v in ["false", "0", "нет", "no", ""]:
            self.assertFalse(_parse_bool(v))

    def test_default(self):
        self.assertTrue(_parse_bool(None, default=True))
        self.assertFalse(_parse_bool("", default=False))


class SummaryTests(unittest.TestCase):
    def test_summary_includes_label_and_details(self):
        s = _summarize_tool_call("create_role", {"name": "Ветеран", "color": "ff0000"}, None)
        self.assertIn("создание роли", s)
        self.assertIn("Ветеран", s)

    def test_summary_with_user(self):
        s = _summarize_tool_call("kick_user", {"reason": "спам"}, 123456789012345678)
        self.assertIn("кик", s)
        self.assertIn("123456789012345678", s)

    def test_summary_unknown_tool_falls_back_to_name(self):
        s = _summarize_tool_call("some_future_tool", {}, None)
        self.assertIn("some_future_tool", s)


if __name__ == "__main__":
    unittest.main()
