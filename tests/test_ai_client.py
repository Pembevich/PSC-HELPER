import unittest

from ai_client import (
    _bounded_float,
    _bounded_int,
    _is_safe_provider_url,
    _parse_retry_after,
    extract_json_block,
)


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


class AIClientBoundaryTests(unittest.TestCase):
    def test_provider_urls_require_https_except_loopback(self):
        self.assertTrue(_is_safe_provider_url("https://models.example.com/v1/chat"))
        self.assertTrue(_is_safe_provider_url("http://127.0.0.1:8080/v1/chat"))
        self.assertTrue(_is_safe_provider_url("http://localhost:8080/v1/chat"))
        self.assertFalse(_is_safe_provider_url("http://models.example.com/v1/chat"))
        self.assertFalse(_is_safe_provider_url("https://token@example.com/v1/chat"))
        self.assertFalse(_is_safe_provider_url("https://example.com/v1/chat#redirect"))
        self.assertFalse(_is_safe_provider_url("https://example.com:not-a-port/v1/chat"))

    def test_numeric_request_parameters_are_bounded(self):
        self.assertEqual(_bounded_int(-1, 10, 1, 100), 1)
        self.assertEqual(_bounded_int(1000, 10, 1, 100), 100)
        self.assertEqual(_bounded_int("invalid", 10, 1, 100), 10)
        self.assertEqual(_bounded_float(float("nan"), 0.5, 0.0, 1.0), 0.5)
        self.assertEqual(_bounded_float(float("inf"), 0.5, 0.0, 1.0), 0.5)
        self.assertEqual(_bounded_float(-4.0, 0.5, 0.0, 1.0), 0.0)

    def test_retry_after_is_finite_and_capped(self):
        self.assertEqual(_parse_retry_after({"Retry-After": "nan"}), None)
        self.assertEqual(_parse_retry_after({"Retry-After": "999999"}), 3600.0)


if __name__ == "__main__":
    unittest.main()
