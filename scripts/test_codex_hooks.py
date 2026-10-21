import importlib.util
import unittest
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[1] / ".codex/hooks/project_checks.py"
SPEC = importlib.util.spec_from_file_location("project_checks", HOOK_PATH)
hooks = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(hooks)


class HookPolicyTests(unittest.TestCase):
    def test_extracts_patch_file_paths(self):
        patch = "*** Update File: fe/src/App.tsx\n*** Add File: be/new_module.py\n"
        self.assertEqual(hooks.changed_paths(patch), ["fe/src/App.tsx", "be/new_module.py"])

    def test_normalizes_absolute_paths_inside_repository(self):
        patch = f"*** Update File: {hooks.ROOT}/fe/src/App.tsx\n"
        self.assertEqual(hooks.changed_paths(patch), ["fe/src/App.tsx"])

    def test_identifies_sensitive_paths(self):
        paths = [".env", "fe/.env.local", "be/backups/db.sql", "agent/keys/private.pem"]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(hooks.sensitive_path(path))

    def test_allows_regular_source_paths(self):
        paths = ["fe/src/App.tsx", "be/auth/service.py", "docs/README.md"]
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(hooks.sensitive_path(path))

    def test_detects_destructive_operations(self):
        commands = ["alembic downgrade -1", "docker compose down -v", "git clean -fdx", "psql -c 'DROP TABLE shipments'"]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(any(pattern.search(command) for pattern, _ in hooks.DESTRUCTIVE_COMMAND_PATTERNS))

    def test_allows_normal_development_commands(self):
        commands = ["make check-fe", "npm run dev", "git status --short"]
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(any(pattern.search(command) for pattern, _ in hooks.DESTRUCTIVE_COMMAND_PATTERNS))


if __name__ == "__main__":
    unittest.main()
