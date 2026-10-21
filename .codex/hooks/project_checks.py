#!/usr/bin/env python3
"""Repository-local Codex safety checks and targeted post-edit validation."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PATCH_PATH_PATTERNS = (
    re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$", re.MULTILINE),
    re.compile(r"^(?:\+\+\+|---) [ab]/(.+?)\s*$", re.MULTILINE),
)
SENSITIVE_PATH_PARTS = {"backups", "backup", "exports", "logs"}
SENSITIVE_FILE_PATTERNS = (
    re.compile(r"(^|/)\.env(?:\.[^/]*)?$", re.IGNORECASE),
    re.compile(r"\.(?:pem|key|p12|pfx)$", re.IGNORECASE),
)
DESTRUCTIVE_COMMAND_PATTERNS = (
    (re.compile(r"\balembic\s+downgrade\b", re.IGNORECASE), "Alembic downgrade can alter database state."),
    (re.compile(r"\bdocker\s+compose\s+down\b[^\n;&|]*\s-v(?:\s|$)", re.IGNORECASE), "Removing Compose volumes can delete local database data."),
    (re.compile(r"\bgit\s+clean\s+-[^\n;&|]*[fd][^\n;&|]*x", re.IGNORECASE), "git clean can permanently remove untracked files."),
    (re.compile(r"(?:^|[;&|]\s*)rm\s+(?:-[^\n;&|]*r[^\n;&|]*f|-[^\n;&|]*f[^\n;&|]*r)\s+(?:/|\$HOME|~)", re.IGNORECASE), "Recursive deletion of a broad path is blocked."),
    (re.compile(r"\b(?:psql|alembic|mysql)\b[^\n]*\b(?:DROP\s+(?:TABLE|SCHEMA|DATABASE)|TRUNCATE(?:\s+TABLE)?)\b", re.IGNORECASE), "Destructive database SQL requires a deliberate manual review."),
)


def changed_paths(patch: str) -> list[str]:
    found: list[str] = []
    for pattern in PATCH_PATH_PATTERNS:
        found.extend(match.group(1).strip() for match in pattern.finditer(patch))
    normalized = []
    for item in found:
        item = item.removeprefix("./").replace("\\", "/")
        candidate = Path(item)
        if candidate.is_absolute():
            try:
                item = candidate.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                pass
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def sensitive_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = {part.lower() for part in normalized.split("/")}
    return bool(parts & SENSITIVE_PATH_PARTS) or any(pattern.search(normalized) for pattern in SENSITIVE_FILE_PATTERNS)


def emit_pre_deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def emit_context(message: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": message,
    }}))


def pre_tool(event: dict[str, Any]) -> int:
    name = event.get("tool_name")
    inputs = event.get("tool_input") or {}
    if name == "apply_patch" and isinstance(inputs, dict):
        protected = [path for path in changed_paths(str(inputs.get("command", ""))) if sensitive_path(path)]
        if protected:
            emit_pre_deny("Patch targets sensitive paths (credentials, backups, exports or logs); review and handle manually.")
            return 0
    if name == "Bash" and isinstance(inputs, dict):
        command = str(inputs.get("command", ""))
        for pattern, reason in DESTRUCTIVE_COMMAND_PATTERNS:
            if pattern.search(command):
                emit_pre_deny(f"{reason} Confirm the target and recovery plan before performing this operation.")
                return 0
        env_write = re.search(r"(?:>|>>|\btee\s+)\s*['\"]?[^\s;&|]*\.env(?:\.[\w.-]+)?(?:['\"])?(?:\s|$)", command)
        if env_write:
            emit_pre_deny("Writing .env files through shell commands is blocked to protect local credentials.")
    return 0


def post_tool(event: dict[str, Any]) -> int:
    inputs = event.get("tool_input") or {}
    patch = str(inputs.get("command", "")) if isinstance(inputs, dict) else ""
    paths = changed_paths(patch)
    fe_files = [path for path in paths if path.startswith("fe/") and path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))]
    python_files = [path for path in paths if path.startswith(("be/", "agent/")) and path.endswith(".py")]
    feedback = []

    if fe_files:
        eslint = ROOT / "fe/node_modules/.bin/eslint"
        actual_files = [path for path in fe_files if (ROOT / path).is_file()]
        if not eslint.exists():
            feedback.append("FE lint skipped: fe/node_modules is missing; install dependencies with npm ci --prefix fe.")
        elif actual_files:
            args = ["npm", "exec", "--", "eslint", "--no-warn-ignored", *[str(Path(path).relative_to("fe")) for path in actual_files]]
            try:
                result = subprocess.run(args, cwd=ROOT / "fe", text=True, capture_output=True, timeout=70, check=False)
                if result.returncode:
                    feedback.append("Targeted FE ESLint failed; review and fix:\n" + (result.stdout + result.stderr).strip()[-6000:])
                else:
                    feedback.append("Targeted FE ESLint passed for changed files.")
            except (OSError, subprocess.TimeoutExpired) as error:
                feedback.append(f"Targeted FE ESLint could not complete: {error}")

    syntax_errors = []
    for relative in python_files:
        file_path = ROOT / relative
        if file_path.is_file():
            try:
                compile(file_path.read_text(encoding="utf-8"), str(file_path), "exec")
            except (SyntaxError, UnicodeDecodeError) as error:
                syntax_errors.append(f"{relative}: {error}")
    if syntax_errors:
        feedback.append("Python syntax check failed:\n" + "\n".join(syntax_errors))
    elif python_files:
        feedback.append("Python syntax check passed for changed files (not a substitute for tests).")

    if feedback:
        emit_context("\n".join(feedback))
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"pre", "post"}:
        print("Usage: project_checks.py pre|post", file=sys.stderr)
        return 2
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    return pre_tool(event) if sys.argv[1] == "pre" else post_tool(event)


if __name__ == "__main__":
    raise SystemExit(main())
