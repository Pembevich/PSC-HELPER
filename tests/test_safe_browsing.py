import json
import unittest

import safe_browsing


class _Content:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def read(self, _limit: int) -> bytes:
        return self.payload


class _Response:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self.content = _Content(json.dumps(payload).encode("utf-8"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Session:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload)


class SafeBrowsingV5Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        safe_browsing.clear_cache()

    async def test_v5_get_uses_header_key_and_protocol_cache_duration(self):
        session = _Session(
            {
                "threats": [
                    {
                        "url": "https://bad.example/",
                        "threatTypes": ["SOCIAL_ENGINEERING"],
                    }
                ],
                "cacheDuration": "123.5s",
            }
        )

        verdict = await safe_browsing.lookup_url(
            session,
            "https://bad.example/",
            api_key="secret-key",
        )

        self.assertTrue(verdict.checked)
        self.assertTrue(verdict.matched)
        self.assertEqual(verdict.threat_types, ("SOCIAL_ENGINEERING",))
        self.assertEqual(verdict.cache_seconds, 123.5)
        self.assertEqual(len(session.calls), 1)
        endpoint, kwargs = session.calls[0]
        self.assertEqual(endpoint, safe_browsing.SAFE_BROWSING_V5_URL)
        self.assertNotIn("secret-key", endpoint)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "secret-key")
        self.assertEqual(kwargs["params"], [("urls", "https://bad.example/")])

    async def test_safe_result_is_cached_without_a_second_request(self):
        first = _Session({"threats": [], "cacheDuration": "600s"})
        second = _Session(
            {
                "threats": [
                    {
                        "url": "https://example.com/",
                        "threatTypes": ["MALWARE"],
                    }
                ],
                "cacheDuration": "600s",
            }
        )

        initial = await safe_browsing.lookup_url(
            first,
            "https://example.com/",
            api_key="key",
        )
        cached = await safe_browsing.lookup_url(
            second,
            "https://example.com/",
            api_key="key",
        )

        self.assertTrue(initial.checked)
        self.assertFalse(initial.matched)
        self.assertTrue(cached.checked)
        self.assertFalse(cached.matched)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(second.calls, [])

    async def test_batch_cache_keeps_safe_and_unsafe_urls_separate(self):
        session = _Session(
            {
                "threats": [
                    {
                        "url": "https://bad.example/",
                        "threatTypes": ["MALWARE"],
                    }
                ],
                "cacheDuration": "600s",
            }
        )

        combined = await safe_browsing.lookup_urls(
            session,
            ["https://bad.example/", "https://safe.example/"],
            api_key="key",
        )
        cached_bad = await safe_browsing.lookup_url(
            _Session({}),
            "https://bad.example/",
            api_key="key",
        )
        cached_safe = await safe_browsing.lookup_url(
            _Session({}),
            "https://safe.example/",
            api_key="key",
        )

        self.assertTrue(combined.matched)
        self.assertTrue(cached_bad.matched)
        self.assertEqual(cached_bad.threat_types, ("MALWARE",))
        self.assertFalse(cached_safe.matched)
        self.assertEqual(cached_safe.threat_types, ())

    async def test_malformed_threat_entry_never_becomes_cached_safe(self):
        session = _Session(
            {
                "threats": [{"url": "https://bad.example/"}],
                "cacheDuration": "600s",
            }
        )

        verdict = await safe_browsing.lookup_url(
            session,
            "https://bad.example/",
            api_key="key",
        )

        self.assertTrue(verdict.checked)
        self.assertTrue(verdict.matched)
        self.assertEqual(verdict.threat_types, ())

    def test_cache_duration_is_bounded_and_malformed_values_get_defaults(self):
        self.assertEqual(
            safe_browsing._parse_cache_duration("999999s", matched=False),
            safe_browsing.MAX_CACHE_SECONDS,
        )
        self.assertEqual(
            safe_browsing._parse_cache_duration("invalid", matched=False),
            safe_browsing.DEFAULT_SAFE_CACHE_SECONDS,
        )
        self.assertEqual(
            safe_browsing._parse_cache_duration("invalid", matched=True),
            safe_browsing.DEFAULT_THREAT_CACHE_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
