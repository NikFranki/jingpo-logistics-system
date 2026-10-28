"""Collect minimal Codex usage metadata and build an issue-fix PR description."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def collect_usage(codex_home: Path) -> dict[str, int] | None:
    """Return the latest cumulative token snapshot from each Codex rollout."""
    sessions = codex_home / "sessions"
    if sessions.is_symlink() or not sessions.is_dir():
        return None

    snapshots: list[dict[str, int]] = []
    for path in sessions.rglob("rollout-*.jsonl"):
        directory = path.parent
        unsafe_parent = False
        while directory != sessions:
            if directory.is_symlink():
                unsafe_parent = True
                break
            if sessions not in directory.parents:
                unsafe_parent = True
                break
            directory = directory.parent
        if path.is_symlink() or unsafe_parent:
            continue
        latest: dict[str, int] | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload", {})
                    if payload.get("type") != "token_count":
                        continue
                    info = payload.get("info", {})
                    totals = info.get("total_token_usage")
                    if not isinstance(totals, dict):
                        continue
                    snapshot: dict[str, int] = {}
                    for field in USAGE_FIELDS:
                        value = _integer(totals.get(field))
                        if value is not None:
                            snapshot[field] = value
                    details = totals.get("input_tokens_details", {})
                    if "cached_input_tokens" not in snapshot and isinstance(details, dict):
                        cached = _integer(details.get("cached_tokens"))
                        if cached is not None:
                            snapshot["cached_input_tokens"] = cached
                    if "reasoning_output_tokens" not in snapshot:
                        output_details = totals.get("output_tokens_details", {})
                        if isinstance(output_details, dict):
                            reasoning = _integer(output_details.get("reasoning_tokens"))
                            if reasoning is not None:
                                snapshot["reasoning_output_tokens"] = reasoning
                    if "total_tokens" not in snapshot and {
                        "input_tokens", "output_tokens"
                    } <= snapshot.keys():
                        snapshot["total_tokens"] = (
                            snapshot["input_tokens"] + snapshot["output_tokens"]
                        )
                    if snapshot:
                        latest = snapshot
        except OSError:
            continue
        if latest is not None:
            snapshots.append(latest)

    if not snapshots:
        return None

    totals = {
        field: sum(snapshot.get(field, 0) for snapshot in snapshots)
        for field in USAGE_FIELDS
        if any(field in snapshot for snapshot in snapshots)
    }
    if "total_tokens" not in totals and {
        "input_tokens", "output_tokens"
    } <= totals.keys():
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


def _summary_sections(text: str) -> tuple[str, str]:
    """Extract only the requested Cause and Changes sections."""
    headings = list(
        re.finditer(
            r"(?im)^\s*(?:#{1,6}\s*|\*\*)(cause|changes)\b\s*(?:\*\*)?\s*$",
            text,
        )
    )
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        key = heading.group(1).lower()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        # Ignore other sections (notably model-written validation claims).
        body_end = re.search(r"(?im)^\s*(?:#{1,6}\s+|\*\*)", text[heading.end():end])
        content_end = heading.end() + body_end.start() if body_end else end
        content = text[heading.end():content_end].strip()
        content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        content = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", content)
        content = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", content)
        content = re.sub(r"<[^>]*>", "", content)
        content = re.sub(r"https?://\S+", "[link omitted]", content)
        content = re.sub(r"(?<!\w)@([A-Za-z0-9_-]+)", r"＠\1", content)
        sections[key] = content[:6000]
    return sections.get("cause", ""), sections.get("changes", "")


def build_pr_body(
    *,
    summary: str,
    usage: dict[str, int] | None,
    files: list[str],
    diff_stat: str,
    repository: str,
    issue_number: str,
    run_id: str,
    server_url: str,
) -> str:
    cause, changes = _summary_sections(summary)
    if not cause:
        cause = "The automated summary was unavailable. Review the linked issue and proposed diff."
    if not changes:
        changes = "Review the file list and diff below for the proposed changes."

    body = [
        f"Closes #{issue_number}",
        "",
        "## Cause",
        cause,
        "",
        "## Changes",
        changes,
        "",
        "## Files changed",
    ]
    for name in files:
        safe_name = name.replace("`", "\\`").replace("\r", " ").replace("\n", " ")
        body.append(f"- `{safe_name}`")
    if not files:
        body.append("- File list unavailable; review the diff.")
    if diff_stat:
        body.extend(["", "```text", diff_stat[:3000], "```"])

    body.extend(
        [
            "",
            "## Validation",
            f"- `make check` passed in the [issue-fix Actions run]({server_url}/{repository}/actions/runs/{run_id}).",
            "",
            "## Model usage",
        ]
    )
    if usage:
        labels = (
            ("input_tokens", "Input tokens"),
            ("cached_input_tokens", "Cached input tokens"),
            ("output_tokens", "Output tokens"),
            ("reasoning_output_tokens", "Reasoning tokens (included in output)"),
            ("total_tokens", "Total tokens"),
        )
        body.extend(
            f"- {label}: {usage[field]:,}" for field, label in labels if field in usage
        )
        body.append(
            "- These are the usage totals reported in Codex session records. Confirm billed cost in the DeepSeek account usage page; this workflow does not estimate price."
        )
    else:
        body.append(
            "- Token usage was not available in the Codex session record for this run; check the DeepSeek account usage page."
        )
    body.extend(["", "Please review the diff before merging."])
    return "\n".join(body) + "\n"


def _collect(args: argparse.Namespace) -> None:
    usage = collect_usage(Path(args.codex_home))
    output = Path(args.output)
    data = json.dumps({"available": usage is not None, "usage": usage}, indent=2) + "\n"
    descriptor = os.open(output, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(data)
    if usage is None:
        print("Codex token usage unavailable; the PR will say so explicitly.")
    else:
        compact = ", ".join(f"{key}={value}" for key, value in usage.items())
        print(f"Codex token usage collected: {compact}")


def _build(args: argparse.Namespace) -> None:
    summary_path = Path(args.summary)
    summary = summary_path.read_text(encoding="utf-8", errors="replace") if summary_path.exists() else ""
    usage_data = json.loads(Path(args.usage).read_text(encoding="utf-8"))
    usage = usage_data.get("usage") if usage_data.get("available") else None
    if not isinstance(usage, dict) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in usage.values()
    ):
        usage = None
    subprocess.run(["git", "add", "-N", "--all"], check=True)
    files_result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD", "--"], check=True, capture_output=True
    )
    files = [part.decode("utf-8", errors="replace") for part in files_result.stdout.split(b"\0") if part]
    stats = subprocess.run(
        ["git", "diff", "--stat", "HEAD", "--"], check=True, capture_output=True, text=True
    ).stdout.strip()
    body = build_pr_body(
        summary=summary,
        usage=usage,
        files=files,
        diff_stat=stats,
        repository=args.repository,
        issue_number=args.issue_number,
        run_id=args.run_id,
        server_url=args.server_url,
    )
    Path(args.output).write_text(body, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--codex-home", required=True)
    collect.add_argument("--output", required=True)
    collect.set_defaults(function=_collect)
    build = commands.add_parser("build")
    for argument in (
        "summary", "usage", "output", "repository", "issue-number", "run-id", "server-url"
    ):
        build.add_argument(f"--{argument}", required=True)
    build.set_defaults(function=_build)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
