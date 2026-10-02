"""V6 facts, caches and releases survive V7; no schedule is fabricated."""
import os
import unittest
from sqlalchemy import text
import test_v2_migration as helpers

@unittest.skipUnless(os.getenv('RUN_V2_MIGRATION_TESTS') == '1', 'opt-in migration rehearsal')
class V7MigrationTests(unittest.TestCase):
    setUp = helpers.V2MigrationTests.setUp
    _drop_database = helpers.V2MigrationTests._drop_database
    run_migration = helpers.V2MigrationTests.run_migration
    seed_history = helpers.V2MigrationTests.seed_history
    snapshot = helpers.V2MigrationTests.snapshot

    def test_v6_history_and_unique_execution_occupancy_preserved(self):
        self.seed_history(); self.run_migration('e61a7c93b204')
        before = self.snapshot(); self.run_migration('head'); after = self.snapshot()
        for table in before:
            self.assertEqual(len(before[table]), len(after[table]))
            for old, new in zip(before[table], after[table]):
                self.assertEqual(dict(old), {key: new[key] for key in old})
        self.assertTrue(all(row['scheduling_mode'] == 'LEGACY' and row['schedule_version'] == 0 for row in after['shipments']))
        for row in after['task_shipments']:
            self.assertEqual(row['association_state'], 'ACTIVE' if row['released_at'] is None else 'RELEASED')
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT count(*) FROM shipment_schedule_versions')).scalar_one(), 0)
            indexes = {row.indexname: row.indexdef for row in conn.execute(text("SELECT indexname,indexdef FROM pg_indexes WHERE tablename='task_shipments'"))}
            self.assertIn('association_state', indexes['uq_task_shipments_active'])
            self.assertIn('PLANNED', indexes['uq_task_shipments_active_leg'])
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), 'c84e2b19a607')
