import copy
import json
import unittest

from ai_client import _compact_messages_for_oversize


def _tool_step(step: int, count: int = 2) -> list[dict]:
    calls = [
        {
            "id": f"call-{step}-{index}",
            "type": "function",
            "function": {"name": "read_messages", "arguments": '{"limit":"2"}'},
            "extra_content": {"google": {"thought_signature": f"signature-{step}"}},
        }
        for index in range(count)
    ]
    return [
        {"role": "assistant", "content": None, "tool_calls": calls},
        *[
            {"role": "tool", "tool_call_id": call["id"],
             "content": json.dumps({"status": "success", "result": "Сообщения найдены."})}
            for call in calls
        ],
    ]


class OversizeToolConversationTests(unittest.TestCase):
    def assert_complete_tool_groups(self, messages: list[dict]) -> None:
        pending = set()
        for message in messages:
            if message.get("tool_calls"):
                self.assertFalse(pending, "A new model step precedes pending tool results")
                pending.update(call["id"] for call in message["tool_calls"])
            elif message["role"] == "tool":
                self.assertIn(message["tool_call_id"], pending, "Orphaned tool result")
                pending.remove(message["tool_call_id"])
        self.assertFalse(pending, "A retained call has lost its result")

    def test_long_current_turn_survives_history_limit_with_request_and_signatures(self):
        request = {"role": "user", "content": "Сначала найди сообщения. " * 1400 + "Ничего не удаляй."}
        current_turn = [request]
        for step in range(11):
            current_turn.extend(_tool_step(step))
        messages = [
            {"role": "system", "content": "Проверяй каждый результат."},
            *[{"role": "user", "content": f"Старое сообщение {index}"} for index in range(40)],
            *current_turn,
        ]
        original = copy.deepcopy(messages)

        compacted, _, _ = _compact_messages_for_oversize(messages)

        self.assertEqual(compacted[1:], current_turn)
        self.assertEqual(compacted[1]["content"], request["content"])
        self.assert_complete_tool_groups(compacted)
        self.assertEqual(messages, original)

    def test_old_history_is_removed_only_in_complete_user_turns(self):
        old_turns = [
            [{"role": "user", "content": f"Поручение {step}"}, *_tool_step(step, 3)]
            for step in range(12)
        ]
        current_turn = [{"role": "user", "content": "Покажи итог предыдущих проверок."}]
        messages = [
            {"role": "system", "content": "Правила"},
            *[message for turn in old_turns for message in turn],
            *current_turn,
        ]

        compacted, _, _ = _compact_messages_for_oversize(messages)

        self.assertLess(len(compacted), len(messages))
        self.assertEqual(compacted[-1:], current_turn)
        self.assert_complete_tool_groups(compacted)
        for turn in old_turns:
            present = [message in compacted for message in turn]
            self.assertTrue(all(present) or not any(present), "A historical turn was split")

    def test_large_json_receipt_stays_valid_and_preserves_outcome(self):
        payload = {
            "call_id": "write-1", "name": "send_message", "mutating": True,
            "status": "unknown", "result": 'Сбой; "доставлено" не подтверждено.\\\n' * 1600,
        }
        messages = [
            {"role": "user", "content": "Отправь сообщение."},
            {"role": "tool", "tool_call_id": "write-1", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        original = copy.deepcopy(messages)

        compacted, _, _ = _compact_messages_for_oversize(messages)
        receipt = json.loads(compacted[-1]["content"])

        self.assertLess(len(compacted[-1]["content"]), len(messages[-1]["content"]))
        self.assertIn("результат сокращён", receipt["result"])
        self.assertEqual({key: value for key, value in receipt.items() if key != "result"},
                         {key: value for key, value in payload.items() if key != "result"})
        self.assertEqual(messages, original)

    def test_unknown_json_observation_is_not_sliced_into_invalid_json(self):
        payload = {"messages": [{"id": str(index), "text": "x" * 1000} for index in range(50)]}
        content = json.dumps(payload)
        messages = [{"role": "user", "content": "Прочитай сообщения."},
                    {"role": "tool", "tool_call_id": "read-1", "content": content}]

        compacted, _, _ = _compact_messages_for_oversize(messages)

        self.assertEqual(json.loads(compacted[-1]["content"]), payload)

    def test_visual_retry_preserves_complete_request_and_authority_text(self):
        request_text = "Подробное поручение. " * 1400 + "Не выдавай роли."
        developer_text = "Проверяй права. " * 2000
        messages = [
            {"role": "developer", "content": [{"type": "text", "text": developer_text}]},
            {"role": "user", "content": [
                {"type": "text", "text": request_text},
                *[{"type": "image_url", "image_url": {"url": f"https://example.com/{i}.png"}} for i in range(8)],
            ]},
            *_tool_step(0),
        ]
        original = copy.deepcopy(messages)

        compacted, before, after = _compact_messages_for_oversize(messages)

        self.assertEqual((before, after), (8, 4))
        self.assertEqual(compacted[0], messages[0])
        self.assertEqual(compacted[1]["content"][0]["text"], request_text)
        self.assertEqual(messages, original)
        self.assert_complete_tool_groups(compacted)

    def test_missing_user_boundary_does_not_split_tool_history(self):
        messages = [{"role": "system", "content": "Правила"}]
        for step in range(13):
            messages.extend(_tool_step(step))

        compacted, _, _ = _compact_messages_for_oversize(messages)

        self.assertEqual(compacted, messages)
        self.assert_complete_tool_groups(compacted)

    def test_plain_text_tool_result_reports_truncation(self):
        messages = [
            {"role": "user", "content": "Прочитай журнал."},
            {"role": "function", "name": "read_messages", "content": "Строка журнала. " * 2000},
        ]

        compacted, _, _ = _compact_messages_for_oversize(messages)

        self.assertLessEqual(len(compacted[-1]["content"]), 24000)
        self.assertIn("результат сокращён", compacted[-1]["content"])


if __name__ == "__main__":
    unittest.main()
