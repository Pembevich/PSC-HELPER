import asyncio
import os
import tempfile
import unittest

from storage import (
    add_ai_event,
    close_all_connections,
    init_db,
    mark_ai_message_deleted,
    search_ai_events,
)


class AiEventLogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_all_connections()

    async def test_records_searches_and_marks_deleted_ping(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "events.db")
            await init_db(db_path)

            event_id = await add_ai_event(
                guild_id=123,
                event_type="message_mention",
                actor_id=10,
                actor_name="sender",
                target_user_id=20,
                channel_id=30,
                message_id=40,
                summary="sender упомянул user",
                details={"content": "@user hello"},
                db_path=db_path,
            )

            rows = await search_ai_events(
                guild_id=123,
                event_type="message_mention",
                target_user_id=20,
                db_path=db_path,
            )
            self.assertEqual(rows[0]["id"], event_id)
            self.assertEqual(rows[0]["deleted"], 0)
            self.assertIn("@user hello", rows[0]["details"])

            await mark_ai_message_deleted(123, 40, db_path=db_path)
            rows = await search_ai_events(guild_id=123, message_id=40, db_path=db_path)
            self.assertEqual(rows[0]["deleted"], 1)

    async def test_role_ping_recipient_snapshot_is_searchable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "recipients.db")
            await init_db(db_path)
            event_id = await add_ai_event(
                guild_id=123,
                event_type="message_mention",
                target_role_id=777,
                message_id=999,
                summary="role ping",
                recipient_user_ids=[20, 30],
                recipient_role_id=777,
                db_path=db_path,
            )

            rows = await search_ai_events(
                guild_id=123,
                event_type="message_mention",
                recipient_user_id=20,
                db_path=db_path,
            )
            self.assertEqual([row["id"] for row in rows], [event_id])
            missing = await search_ai_events(
                guild_id=123,
                event_type="message_mention",
                recipient_user_id=40,
                db_path=db_path,
            )
            self.assertEqual(missing, [])

    async def test_concurrent_event_transactions_keep_recipients_attached(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "concurrent-events.db")
            await init_db(db_path)

            async def record(index: int) -> int:
                return await add_ai_event(
                    guild_id=123,
                    event_type="message_mention",
                    message_id=10_000 + index,
                    summary=f"ping {index}",
                    recipient_user_ids=[20_000 + index],
                    db_path=db_path,
                )

            event_ids = await asyncio.gather(*(record(index) for index in range(50)))
            self.assertEqual(len(set(event_ids)), 50)
            for index, event_id in enumerate(event_ids):
                rows = await search_ai_events(
                    guild_id=123,
                    recipient_user_id=20_000 + index,
                    db_path=db_path,
                )
                self.assertEqual([row["id"] for row in rows], [event_id])


if __name__ == "__main__":
    unittest.main()
