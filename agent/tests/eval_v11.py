"""V11 real-model evaluation using a synthetic, read-only BE fixture.

Run --preflight for a no-model contract check. --run makes provider API calls.
No business mutation endpoint is exposed by the fixture or registered in the graph.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))

import eval_v10 as fixture
from config import load_settings
from graph import build_graph
from identity import Identity
from model import create_chat_model
from registry import build_tools
from state import TurnContext

CASES = json.loads((ROOT / 'tests/eval_v11_cases.json').read_text())

class FixtureIdentityGuard:
    async def recheck(self, identity):
        return None


def prepare_fixture():
    # The shared fixture has no eligible orders by default; make order 3 editable.
    fixture.ORDERS[2]['status'] = 'PENDING_SHIPMENT'
    fixture.validate_fixture(*fixture.fixture_data())
    return fixture.serve_fixture()


def preflight():
    assert CASES['version'] == 'v11-agent-actions-1'
    ids = [case['id'] for case in CASES['cases']]
    assert len(ids) == len(set(ids)) and len(ids) == 7
    assert {case['expected_action'] for case in CASES['cases']} == {
        None, 'order.update', 'shipment.destination.update'}
    # Validate fixture/response schemas and ensure only read/proposal tools are registered.
    server, base_url = prepare_fixture()
    try:
        tools = build_tools(base_url, allow_actions=True)
        names = {tool.name for tool in tools}
        assert {'propose_order_update', 'propose_shipment_destination_update'} <= names
        assert not any(name.lower().startswith(('update_', 'patch_', 'delete_', 'create_')) for name in names)
        print(json.dumps({'preflight': 'passed', 'cases': len(CASES['cases']),
                          'fixture': 'synthetic', 'mutation_tools_registered': False}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


def make_identity(role):
    own = role == 'user'
    return Identity('1' if own else '2', 'eval-session-user' if own else 'eval-session-ops', 1,
                    role, datetime(2030, 1, 1, tzinfo=timezone.utc),
                    'own' if own else 'company', 'business', 'eval-user' if own else 'eval-company')


async def evaluate_case(model, base_url, case):
    identity = make_identity(case['role'])
    session_id, message_id = str(uuid4()), str(uuid4())
    context = TurnContext(identity=identity, identity_client=FixtureIdentityGuard(), allow_actions=True,
                          agent_session_id=session_id, message_id=message_id)
    graph = build_graph(model, build_tools(base_url, allow_actions=True))
    result = await graph.ainvoke({'messages': [HumanMessage(content=case['question'])]},
                                 config={'configurable': {'thread_id': str(uuid4())}}, context=context)
    answer = result['messages'][-1].content
    calls = []
    tool_names = {}
    for message in result['messages']:
        if isinstance(message, AIMessage):
            calls.extend({'name': call['name'], 'args': call.get('args', {})} for call in message.tool_calls)
            tool_names.update({call['id']: call['name'] for call in message.tool_calls})
        elif isinstance(message, ToolMessage):
            pass
    candidate = context.action_candidates[0] if context.action_candidates else None
    issues = []
    if candidate and candidate.get('candidate_id') and candidate['candidate_id'] in answer:
        issues.append('candidate_id_exposed_to_user')
    expected_action = case['expected_action']
    if expected_action is None and candidate is not None:
        issues.append('unexpected_action_candidate')
    if expected_action is not None:
        if candidate is None:
            issues.append('missing_action_candidate')
        else:
            if candidate.get('action_type') != expected_action:
                issues.append('wrong_action_type')
            if candidate.get('subject_id') != case['expected_subject_id']:
                issues.append('wrong_subject')
            for key, value in case.get('expected_payload', {}).items():
                if candidate.get('payload', {}).get(key) != value:
                    issues.append('wrong_payload:' + key)
    if len(context.action_candidates) > 1:
        issues.append('multiple_candidates')
    return {'id': case['id'], 'group': case['group'], 'role': case['role'],
            'expected_action': expected_action, 'candidate': candidate,
            'answer': answer, 'tool_calls': calls, 'model_calls': result.get('model_calls'),
            'stop_reason': result.get('stop_reason'), 'issues': issues,
            'manual_review': 'pending'}


async def run(round_name, case_id=None):
    settings = load_settings()
    model = create_chat_model(settings)
    server, base_url = prepare_fixture()
    started = datetime.now(timezone.utc).isoformat()
    outputs = []
    try:
        selected_cases = [case for case in CASES['cases'] if case_id is None or case['id'] == case_id]
        if case_id is not None and not selected_cases:
            raise ValueError(f'unknown case id: {case_id}')
        for case in selected_cases:
            print(f"[{round_name}] {case['id']} ({case['group']})", flush=True)
            try:
                outputs.append(await evaluate_case(model, base_url, case))
            except Exception as exc:  # Capture per-case provider/infra failures without secrets.
                outputs.append({'id': case['id'], 'group': case['group'],
                                'error_type': type(exc).__name__, 'issues': ['evaluation_error'],
                                'manual_review': 'failed'})
    finally:
        server.shutdown()
        server.server_close()
    digest = hashlib.sha256((ROOT / 'tests/eval_v11_cases.json').read_bytes()).hexdigest()
    report = {'round': round_name, 'sample_version': CASES['version'],
              'case_sha256': digest, 'git_commit': subprocess.check_output(
                  ['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
              'provider': settings.provider, 'model': settings.model, 'started_at': started,
              'fixture': 'synthetic read-only BE', 'mutation_tools_registered': False,
              'case_count': len(outputs), 'cases': outputs, 'manual_review': 'pending'}
    path = Path('/tmp') / 'jingpo-agent-v11' / f'{round_name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'report': str(path), 'cases': len(outputs),
                      'machine_failures': sum(bool(item.get('issues')) for item in outputs),
                      'manual_review': 'pending'}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', help='No model calls; validate cases and synthetic fixture')
    parser.add_argument('--run', action='store_true', help='Call the configured model against synthetic read-only data')
    parser.add_argument('--round', default='v11-baseline-1')
    parser.add_argument('--case-id', help='Run a single case (still uses the configured model API)')
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.run:
        asyncio.run(run(args.round, args.case_id))
    else:
        parser.error('choose --preflight (free) or --run (uses configured model API)')
