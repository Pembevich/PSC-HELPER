import unittest

from ai_client import extract_json_block


class ExtractJsonBlockTests(unittest.TestCase):
    def test_parses_plain_json(self):
        payload = extract_json_block('{"status":"ok","score":0.99}')
        self.assertEqual(payload, {"status": "ok", "score": 0.99})

    def test_parses_json_in_code_fence(self):
        payload = extract_json_block(
            """```json
            {"results":[{"url":"https://example.com","label":"allow"}]}
            ```"""
        )
        self.assertEqual(
            payload,
            {"results": [{"url": "https://example.com", "label": "allow"}]},
        )

    def test_returns_none_for_invalid_payload(self):
        self.assertIsNone(extract_json_block("not-json-at-all"))


if __name__ == "__main__":
    unittest.main()
