import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.engine import make_url


SPEC = importlib.util.spec_from_file_location(
    'project_checks', Path(__file__).resolve().parents[2] / 'scripts' / 'check.py')
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class CheckRunnerTests(unittest.TestCase):
    def test_business_database_is_rejected_before_connecting(self):
        with patch.dict(os.environ, {'TEST_DATABASE_URL': 'postgresql+psycopg:///jingpo_logistics'}), \
             patch('sqlalchemy.create_engine') as create_engine:
            with self.assertRaisesRegex(ValueError, 'isolated'):
                checks.check_backend({})
            create_engine.assert_not_called()

    def test_only_new_database_is_migrated_and_removed_on_failure(self):
        control = MagicMock()
        environment = {}
        failure = subprocess.CalledProcessError(1, ['alembic', 'check'])
        with patch.dict(os.environ, {'TEST_DATABASE_URL': 'postgresql+psycopg:///postgres'}), \
             patch('sqlalchemy.create_engine', return_value=control), \
             patch.object(checks, 'run', side_effect=[None, failure]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                checks.check_backend(environment)
        database = make_url(environment['DATABASE_URL']).database
        self.assertTrue(database.startswith('jingpo_checks_'))
        self.assertTrue(database.endswith('_test'))
        self.assertEqual(run.call_count, 2)
        statements = [str(call.args[0]) for call in
                      control.connect.return_value.__enter__.return_value.execute.call_args_list]
        self.assertEqual(statements, [f'CREATE DATABASE "{database}"',
                                     f'DROP DATABASE "{database}" WITH (FORCE)'])
        control.dispose.assert_called_once()

    def test_failed_creation_never_drops_database(self):
        control = MagicMock()
        execute = control.connect.return_value.__enter__.return_value.execute
        execute.side_effect = RuntimeError('creation failed')
        with patch.dict(os.environ, {'TEST_DATABASE_URL': 'postgresql+psycopg:///postgres'}), \
             patch('sqlalchemy.create_engine', return_value=control):
            with self.assertRaisesRegex(RuntimeError, 'creation failed'):
                checks.check_backend({})
        self.assertEqual(execute.call_count, 1)
        control.dispose.assert_called_once()
