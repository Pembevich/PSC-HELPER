import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import discord

import join_gate
from cogs.logging_events import LoggingCog
from cogs.security import SecurityCog


class JoinGateTests(IsolatedAsyncioTestCase):
    def tearDown(self):
        join_gate._events.clear()
        join_gate._results.clear()

    async def test_waiter_started_first_receives_security_result(self):
        waiter = asyncio.create_task(join_gate.wait_for_join_security(101, 202, join_token="first", timeout=1.0))
        await asyncio.sleep(0)

        join_gate.begin_join_security(101, 202, join_token="first")
        join_gate.finish_join_security(101, 202, join_token="first", suppress_roles=True)

        self.assertTrue(await waiter)

    async def test_missing_security_result_fails_closed(self):
        result = await join_gate.wait_for_join_security(303, 404, join_token="first", timeout=0.01)

        self.assertIsNone(result)

    async def test_rejoin_does_not_reuse_or_receive_an_old_result(self):
        for old_result in (False, True):
            with self.subTest(old_result=old_result):
                join_gate._results.clear()
                join_gate.finish_join_security(101, 202, join_token="old", suppress_roles=old_result)
                waiter = asyncio.create_task(join_gate.wait_for_join_security(101, 202, join_token="new", timeout=1.0))
                await asyncio.sleep(0)
                join_gate.begin_join_security(101, 202, join_token="new")
                join_gate.finish_join_security(101, 202, join_token="old", suppress_roles=old_result)
                await asyncio.sleep(0)
                self.assertFalse(waiter.done())
                join_gate.finish_join_security(101, 202, join_token="new", suppress_roles=not old_result)
                self.assertIs(await waiter, not old_result)

    async def test_expired_result_is_not_returned_to_late_waiter(self):
        key = (101, 202, "expired")
        join_gate._results[key] = (False, time.monotonic() - join_gate._RESULT_TTL_SECONDS - 1)
        result = await join_gate.wait_for_join_security(101, 202, join_token="expired", timeout=0.01)
        self.assertIsNone(result)
        self.assertNotIn(key, join_gate._results)

    async def test_welcome_listener_waits_for_the_current_join_before_giving_roles(self):
        joined_at = discord.utils.utcnow()
        guild = SimpleNamespace(id=101, get_role=lambda role_id: SimpleNamespace(id=role_id))
        member = SimpleNamespace(
            id=202, guild=guild, joined_at=joined_at, bot=False,
            mention="<@202>", add_roles=AsyncMock(),
        )
        old_member = SimpleNamespace(joined_at=joined_at - timedelta(seconds=5))
        join_gate.finish_join_security(
            guild.id, member.id, join_token=join_gate.join_token_for_member(old_member), suppress_roles=False,
        )
        security = SecurityCog(SimpleNamespace())
        decision = asyncio.get_running_loop().create_future()

        async def process_join(_member):
            return await decision

        security._process_member_join = process_join
        welcome = AsyncMock()
        with patch.dict(LoggingCog.on_member_join.__globals__, {
            "_broadcast_embed": welcome,
            "_build_welcome_embed": lambda _member: None,
            "send_log_embed": AsyncMock(),
        }):
            logging_task = asyncio.create_task(LoggingCog(SimpleNamespace()).on_member_join(member))
            await asyncio.sleep(0)
            security_task = asyncio.create_task(security.on_member_join(member))
            await asyncio.sleep(0)
            member.add_roles.assert_not_awaited()
            welcome.assert_not_awaited()
            decision.set_result(True)
            await asyncio.wait_for(asyncio.gather(logging_task, security_task), timeout=1.0)
            member.add_roles.assert_not_awaited()
            welcome.assert_not_awaited()
