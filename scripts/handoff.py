import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / 'docs' / 'handoffs' / 'sessions'


def git_value(*arguments):
    result = subprocess.run(
        ['git', *arguments], cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else '未提供'


def main():
    parser = argparse.ArgumentParser(description='Generate a Codex session handoff from explicitly supplied session text.')
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--input', required=True, type=Path,
                        help='Markdown/text export of this session; it is read locally and not copied to the repository')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', args.session_id) or args.session_id in {'.', '..'}:
        parser.error('session ID may contain only letters, numbers, dots, underscores and hyphens')
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        parser.error('input file does not exist or is not a regular file')
    session_text = input_path.read_text(encoding='utf-8')
    if not session_text.strip():
        parser.error('input file is empty')
    try:
        session_text.encode('utf-8')
    except UnicodeEncodeError:
        parser.error('input must be valid UTF-8 text')

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f'{args.session_id}.md'
    prompt = (ROOT / 'docs' / 'handoffs' / 'PROMPT.md').read_text(encoding='utf-8')
    template = (ROOT / 'docs' / 'handoffs' / 'TEMPLATE.md').read_text(encoding='utf-8')
    metadata = '\n'.join([
        f'- Session ID: {args.session_id}',
        f'- 整理时间（UTC）: {datetime.now(timezone.utc).isoformat(timespec="seconds")}',
        f'- 当前分支: {git_value("branch", "--show-current")}',
        f'- 整理时提交: {git_value("rev-parse", "HEAD")}',
        f'- 工作区状态: {git_value("status", "--short") or "干净"}',
    ])
    request = f'{prompt}\n\n交接模板：\n{template}\n\n仓库元数据（只用于对应字段，工作区状态不能证明改动归属）：\n{metadata}'
    temporary_output = None
    replacement = None
    try:
        with tempfile.NamedTemporaryFile(prefix='codex-handoff-', suffix='.md', delete=False) as output:
            temporary_output = Path(output.name)
        result = subprocess.run(
            ['codex', 'exec', '--ephemeral', '--sandbox', 'read-only',
             '--output-last-message', str(temporary_output), request],
            cwd=ROOT, input=session_text, text=True, check=False)
        if result.returncode != 0:
            raise SystemExit(result.returncode)
        document = temporary_output.read_text(encoding='utf-8').strip()
        if not document.startswith('# Session 交接：'):
            raise RuntimeError('Codex output does not match the handoff template; existing file was preserved')
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', prefix=f'.{target.name}.', suffix='.tmp',
                dir=OUTPUT_DIR, delete=False) as output:
            replacement = Path(output.name)
            output.write(document + '\n')
        os.replace(replacement, target)
        replacement = None
        print(f'交接记录已更新：{target.relative_to(ROOT)}')
    finally:
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)
        if replacement is not None:
            replacement.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
