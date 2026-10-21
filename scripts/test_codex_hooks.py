import importlib.util
import subprocess
import unittest
from unittest.mock import patch
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

    @patch.object(hooks, "ROOT", Path("/repo"))
    @patch.object(hooks.subprocess, "run")
    def test_reads_changed_paths_from_git_status(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout=b" M fe/src/App.tsx\0?? agent/new_module.py\0",
            stderr=b"",
        )
        self.assertEqual(hooks.git_changed_paths(), ["fe/src/App.tsx", "agent/new_module.py"])

    @patch.object(hooks, "git_changed_paths", return_value=["fe/src/App.tsx", "be/auth/service.py"])
    @patch.object(hooks, "changed_paths", return_value=[])
    @patch.object(hooks, "subprocess")
    def test_bash_post_includes_worktree_changes(self, subprocess_mock, changed_paths_mock, git_paths_mock):
        # The changed files are discovered through git; Python syntax checking
        # runs directly and does not need to spawn a subprocess in this test.
        with patch.object(hooks, "ROOT", Path("/path/that/does/not/exist")):
            hooks.post_tool({"tool_name": "Bash", "tool_input": {"command": "python generate.py"}})
        git_paths_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
