import asyncio
import os
import tempfile
import unittest

from storage import claim_form_decision, close_all_connections, init_db


class FormDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_all_connections()

    async def test_claim_is_atomic_and_survives_connection_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "forms.db")
            await init_db(db_path)

            claims = await asyncio.gather(
                *(claim_form_decision(123456789, db_path=db_path) for _ in range(20))
            )
            self.assertEqual(claims.count(True), 1)
            self.assertEqual(claims.count(False), 19)

            await close_all_connections()
            await init_db(db_path)
            self.assertFalse(await claim_form_decision(123456789, db_path=db_path))


if __name__ == "__main__":
    unittest.main()
