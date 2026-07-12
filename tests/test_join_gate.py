import asyncio
from unittest import IsolatedAsyncioTestCase

import join_gate


class JoinGateTests(IsolatedAsyncioTestCase):
    async def test_waiter_started_first_receives_security_result(self):
        waiter = asyncio.create_task(join_gate.wait_for_join_security(101, 202, timeout=1.0))
        await asyncio.sleep(0)

        join_gate.begin_join_security(101, 202)
        join_gate.finish_join_security(101, 202, suppress_roles=True)

        self.assertTrue(await waiter)

    async def test_missing_security_result_fails_closed(self):
        result = await join_gate.wait_for_join_security(303, 404, timeout=0.01)

        self.assertIsNone(result)
