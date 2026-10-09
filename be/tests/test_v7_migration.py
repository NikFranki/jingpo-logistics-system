"""Execution facts survive removal of legacy shipment-path metadata."""
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
        retired_fields = {'path_version', 'scheduling_mode', 'path_leg_id'}
        for table in before:
            self.assertEqual(len(before[table]), len(after[table]))
            for old, new in zip(before[table], after[table]):
                preserved = {key: value for key, value in old.items() if key not in retired_fields}
                self.assertEqual(preserved, {key: new[key] for key in preserved})
        self.assertTrue(all(row['schedule_version'] == 0 for row in after['shipments']))
        for row in after['task_shipments']:
            self.assertEqual(row['association_state'], 'ACTIVE' if row['released_at'] is None else 'RELEASED')
        with self.engine.connect() as conn:
            tables = set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())
            self.assertNotIn('shipment_path_versions', tables)
            self.assertIn('shipment_schedule_legs', tables)
            columns = {row.column_name for row in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='shipments'"))}
            self.assertNotIn('path_version', columns)
            self.assertNotIn('scheduling_mode', columns)
            self.assertEqual(conn.execute(text('SELECT count(*) FROM shipment_schedule_versions')).scalar_one(), 0)
            indexes = {row.indexname: row.indexdef for row in conn.execute(text("SELECT indexname,indexdef FROM pg_indexes WHERE tablename='task_shipments'"))}
            self.assertIn('association_state', indexes['uq_task_shipments_active'])
            self.assertIn('PLANNED', indexes['uq_task_shipments_active_schedule_leg'])
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), helpers.HEAD)
