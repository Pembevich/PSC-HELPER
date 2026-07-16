import asyncio
import unittest

import message_gate


class MessageGateTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        message_gate._events.clear()
        message_gate._results.clear()

    async def test_waiter_started_before_moderation_receives_result(self):
        waiter = asyncio.create_task(message_gate.wait_for_moderation(1001, timeout=1.0))
        await asyncio.sleep(0)

        message_gate.begin_moderation(1001)
        message_gate.finish_moderation(1001, blocked=True)

        self.assertTrue(await waiter)

    async def test_completed_result_is_available_to_late_waiter(self):
        message_gate.begin_moderation(1002)
        message_gate.finish_moderation(1002, blocked=False)

        self.assertFalse(await message_gate.wait_for_moderation(1002, timeout=1.0))

    async def test_timeout_returns_unknown_and_removes_orphan_event(self):
        result = await message_gate.wait_for_moderation(1003, timeout=0.01)

        self.assertIsNone(result)
        self.assertNotIn(1003, message_gate._events)


if __name__ == "__main__":
    unittest.main()
