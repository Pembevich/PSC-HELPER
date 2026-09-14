import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

import discord

import logging_utils
from logging_utils import _build_log_embed


class LogEmbedBoundaryTests(unittest.TestCase):
    def test_embed_is_truncated_to_discord_limits(self):
        embed = _build_log_embed(
            "security",
            "T" * 1000,
            "D" * 10_000,
            color=discord.Color.red(),
            fields=[("N" * 1000, "V" * 5000, False) for _ in range(40)],
            footer="F" * 5000,
        )

        self.assertLessEqual(len(embed.title or ""), 256)
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertLessEqual(len(embed.fields), 25)
        self.assertLessEqual(len(embed.footer.text or ""), 2048)
        self.assertLessEqual(len(embed), 6000)
        for field in embed.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1024)

    def test_empty_parts_receive_valid_fallbacks(self):
        embed = _build_log_embed(
            "server",
            "",
            "",
            color=discord.Color.blue(),
            fields=[("", "", False)],
            footer=None,
        )
        self.assertEqual(embed.title, "Журнал P.OS")
        self.assertEqual(embed.fields[0].name, "—")
        self.assertEqual(embed.fields[0].value, "—")


class LogDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guild = types.SimpleNamespace(id=1)
        self.channel = types.SimpleNamespace(id=42, send=AsyncMock())
        self.events = AsyncMock()
        events_patch = patch("storage.add_ai_event", self.events)
        channel_patch = patch.object(logging_utils, "get_log_channel", return_value=self.channel)
        events_patch.start()
        channel_patch.start()
        self.addCleanup(events_patch.stop)
        self.addCleanup(channel_patch.stop)

    async def test_burst_to_one_channel_preserves_all_events_without_overlapping_sends(self):
        active = 0
        peak = 0
        delivered = []

        async def send(*, embed, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0)
                delivered.append(embed.title)
            finally:
                active -= 1

        self.channel.send.side_effect = send
        results = await asyncio.gather(*(
            logging_utils.send_log_embed(
                self.guild, "channels" if index % 2 else "security", f"Event {index}", "Change",
            )
            for index in range(50)
        ))
        self.assertTrue(all(results))
        self.assertEqual(peak, 1)
        self.assertCountEqual(delivered, [f"Event {index}" for index in range(50)])
        self.assertEqual(self.events.await_count, 50)

    async def test_different_channels_can_deliver_in_parallel(self):
        entered = set()
        both_started = asyncio.Event()

        async def send(*, embed, **_kwargs):
            entered.add(embed.title)
            if len(entered) == 2:
                both_started.set()
            await both_started.wait()

        other = types.SimpleNamespace(id=43, send=AsyncMock(side_effect=send))
        self.channel.send.side_effect = send
        with patch.object(logging_utils, "get_log_channel", side_effect=[self.channel, other]):
            results = await asyncio.wait_for(asyncio.gather(
                logging_utils.send_log_embed(self.guild, "channels", "First", "Change"),
                logging_utils.send_log_embed(self.guild, "channels", "Second", "Change"),
            ), timeout=1)
        self.assertEqual(results, [True, True])

    async def test_failed_delivery_releases_channel_for_next_event_without_retrying(self):
        self.channel.send.side_effect = [
            discord.HTTPException(types.SimpleNamespace(status=500, reason="Server error"), "Failure"),
            None,
        ]
        results = await asyncio.gather(
            logging_utils.send_log_embed(self.guild, "channels", "First", "Change"),
            logging_utils.send_log_embed(self.guild, "channels", "Second", "Change"),
        )
        self.assertEqual(results, [False, True])
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_cancelled_sender_and_waiter_do_not_block_later_event(self):
        first_started = asyncio.Event()
        titles = []

        async def send(*, embed, **_kwargs):
            titles.append(embed.title)
            if embed.title == "First":
                first_started.set()
                await asyncio.Event().wait()

        self.channel.send.side_effect = send
        first = asyncio.create_task(logging_utils.send_log_embed(self.guild, "channels", "First", "Change"))
        tasks = [first]
        try:
            await asyncio.wait_for(first_started.wait(), timeout=1)
            waiter = asyncio.create_task(logging_utils.send_log_embed(self.guild, "channels", "Cancelled", "Change"))
            last = asyncio.create_task(logging_utils.send_log_embed(self.guild, "channels", "Last", "Change"))
            tasks.extend([waiter, last])
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertTrue(await asyncio.wait_for(last, timeout=1))
            self.assertEqual(titles, ["First", "Last"])
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_finished_channels_do_not_accumulate_idle_send_locks(self):
        for channel_id in range(100):
            self.channel.id = channel_id
            self.assertTrue(await logging_utils.send_log_embed(self.guild, "channels", "Event", "Change"))
        locks = logging_utils._LOG_SEND_LOCKS.get(asyncio.get_running_loop())
        self.assertEqual(len(locks or {}), 0)


if __name__ == "__main__":
    unittest.main()
