import os
import tempfile
import unittest

from storage import (
    clear_raid_state,
    close_all_connections,
    get_active_raid_states,
    init_db,
    set_raid_state,
)


class SecurityStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_all_connections()

    async def test_active_raid_state_survives_database_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "security.db")
            await init_db(db_path)
            await set_raid_state(123, 200.0, db_path=db_path)

            self.assertEqual(
                await get_active_raid_states(now=100.0, db_path=db_path),
                {123: 200.0},
            )

            await clear_raid_state(123, db_path=db_path)
            self.assertEqual(
                await get_active_raid_states(now=100.0, db_path=db_path),
                {},
            )

    async def test_expired_raid_state_is_pruned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "security.db")
            await init_db(db_path)
            await set_raid_state(123, 99.0, db_path=db_path)

            self.assertEqual(
                await get_active_raid_states(now=100.0, db_path=db_path),
                {},
            )
