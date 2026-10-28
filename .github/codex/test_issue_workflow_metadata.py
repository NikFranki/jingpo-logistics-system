import json
from argparse import Namespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from issue_workflow_metadata import _collect, _summary_sections, build_pr_body, collect_usage


class CollectUsageTests(unittest.TestCase):
    def test_uses_latest_cumulative_usage_snapshot_per_rollout(self):
        with tempfile.TemporaryDirectory() as temporary:
            rollout = Path(temporary) / "sessions/2026/10/28/rollout-one.jsonl"
            rollout.parent.mkdir(parents=True)
            events = [
                {"payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}}},
                {"payload": {"type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 8},
                    "output_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 4},
                    "total_tokens": 40,
                }}}},
            ]
            rollout.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

            self.assertEqual(
                collect_usage(Path(temporary)),
                {
                    "input_tokens": 30,
                    "cached_input_tokens": 8,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 4,
                    "total_tokens": 40,
                },
            )

    def test_returns_unknown_if_no_usage_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(collect_usage(Path(temporary)))

    def test_ignores_symlinked_rollouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir()
            external = root / "external.jsonl"
            external.write_text(
                json.dumps({"payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 4}}}}),
                encoding="utf-8",
            )
            (sessions / "rollout-linked.jsonl").symlink_to(external)
            self.assertIsNone(collect_usage(root))

    def test_writes_usage_into_an_existing_non_symlink_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "usage.json"
            output.touch()
            _collect(Namespace(codex_home=str(root / "missing"), output=str(output)))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"available": False, "usage": None})


class PullRequestBodyTests(unittest.TestCase):
    def test_extracts_cause_and_changes_without_model_checks(self):
        summary = """## Cause
The form adds a colon. See [external details](https://example.invalid).

## Changes
- Disabled it. @reviewer

## Checks
- Tests passed.
"""
        cause, changes = _summary_sections(summary)
        self.assertEqual(cause, "The form adds a colon. See external details.")
        self.assertEqual(changes, "- Disabled it. ＠reviewer")

    def test_body_records_validation_and_marks_billing_cost_unknown(self):
        body = build_pr_body(
            summary="## Cause\nDefault form labels show punctuation.\n## Changes\nDisabled it.",
            usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            files=["fe/Page.tsx"],
            diff_stat="1 file changed, 1 insertion(+)",
            repository="owner/repo",
            issue_number="7",
            run_id="1234",
            server_url="https://github.com",
        )
        self.assertIn("`make check` passed", body)
        self.assertIn("[issue-fix Actions run](https://github.com/owner/repo/actions/runs/1234)", body)
        self.assertIn("Input tokens: 12", body)
        self.assertIn("Output tokens: 3", body)
        self.assertIn("billed cost", body)
        self.assertIn("`fe/Page.tsx`", body)

    def test_body_says_unknown_when_usage_file_has_no_counts(self):
        body = build_pr_body(
            summary="", usage=None, files=[], diff_stat="", repository="owner/repo",
            issue_number="7", run_id="1234", server_url="https://github.com",
        )
        self.assertIn("Token usage was not available", body)
        self.assertIn("Review the linked issue", body)

    def test_build_command_lists_tracked_and_new_files(self):
        helper = Path(__file__).with_name("issue_workflow_metadata.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            (root / "tracked.txt").write_text("after\n", encoding="utf-8")
            (root / "new.txt").write_text("new\n", encoding="utf-8")
            summary = root / "summary.md"
            summary.write_text("## Cause\nA cause.\n## Changes\nA change.\n## Checks\nUntrusted claim.\n", encoding="utf-8")
            usage = root / "usage.json"
            usage.write_text(json.dumps({"available": True, "usage": {"total_tokens": 5}}), encoding="utf-8")
            body_file = root / "body.md"
            subprocess.run(
                [sys.executable, str(helper), "build", "--summary", str(summary),
                 "--usage", str(usage), "--output", str(body_file), "--repository", "owner/repo",
                 "--issue-number", "5", "--run-id", "42", "--server-url", "https://github.com"],
                cwd=root,
                check=True,
            )
            body = body_file.read_text(encoding="utf-8")
            self.assertIn("`tracked.txt`", body)
            self.assertIn("`new.txt`", body)
            self.assertNotIn("Untrusted claim", body)
            self.assertIn("Total tokens: 5", body)


if __name__ == "__main__":
    unittest.main()
