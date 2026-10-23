"""Permission-bound preview, execution and status for a fixed set of Agent actions."""
import hashlib
import json
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import business_time
from auth.dependencies import assert_current
from auth.models import AgentActionProposal, AgentWritePolicy, AuthAudit, LoginSession, User
from auth.service import fail, resolve_session
from db import SessionLocal, transaction_scope
from errors import (IdempotencyKeyReusedError, InvalidShipmentDestinationError,
                    NetworkError, OrderNotEditableError, OrderNotFoundError,
                    ShipmentNotFoundError)
from models import OperationLog, Order, Shipment, Station, TaskShipment, TransportTask
from orders.schemas import OrderUpdateRequest
from orders.service import build_order_response_body, get_order, update_order
from regions.addresses import REGION_FIELDS, resolve_changes
from shipments.schemas import ShipmentDestinationUpdateRequest
from shipments.service import build_destination_action, update_shipment_destination
from logistics_types import TaskStatus


ACTION_LABELS = {
    'order.update': '修改订单',
    'shipment.destination.update': '更正运单目的站',
}
FIELD_LABELS = {
    'product_name': '货品名称', 'quantity': '数量', 'sender_name': '寄件人',
    'sender_address': '寄件地址', 'recipient_name': '收件人', 'recipient_address': '收件地址',
    'earliest_handover_at': '最早交运时间', 'latest_delivery_at': '最晚送达时间',
}
REGION_LABELS = {
    'sender_province_id': '寄件省份', 'sender_city_id': '寄件城市', 'sender_district_id': '寄件区县',
    'recipient_province_id': '收件省份', 'recipient_city_id': '收件城市', 'recipient_district_id': '收件区县',
}
AUTO_ELIGIBLE_ACTION_TYPES: frozenset[str] = frozenset()
PROPOSAL_LIFETIME = timedelta(minutes=5)


def _hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def _root_session(session: Session, principal) -> LoginSession:
    login = session.get(LoginSession, principal.token_hash)
    if login is None:
        fail('AUTH_REQUIRED', '请重新登录', 401)
    return session.get(LoginSession, login.parent_hash) if login.parent_hash else login


def _ensure_action_role(principal, action_type: str) -> None:
    if principal.role == 'developer' or principal.read_only:
        fail('FORBIDDEN', '当前账号只允许查询', 403)
    if action_type == 'order.update' and principal.role not in ('user', 'ops', 'admin'):
        fail('FORBIDDEN', '当前账号无权修改订单', 403)
    # Destination corrections are logistics writes, which V8 reserves for ops/admin.
    if action_type == 'shipment.destination.update' and principal.role not in ('ops', 'admin'):
        fail('FORBIDDEN', '当前账号无权更正运单目的站', 403)


def policy_body(session: Session, user: User) -> dict:
    policy = session.get(AgentWritePolicy, user.id)
    enabled = list(policy.enabled_action_types) if policy else []
    # The allowlist is deliberately empty until a separate risk review approves actions.
    enabled = [item for item in enabled if item in AUTO_ELIGIBLE_ACTION_TYPES]
    return {'enabled_action_types': enabled,
            'eligible_action_types': sorted(AUTO_ELIGIBLE_ACTION_TYPES),
            'version': policy.version if policy else 0}


def update_policy(session: Session, principal, enabled_action_types: list[str]) -> dict:
    unknown = set(enabled_action_types) - AUTO_ELIGIBLE_ACTION_TYPES
    if unknown:
        fail('INVALID_ACTION', '当前没有已批准自动执行的操作', 422)
    with session.begin():
        login, user = resolve_session(session, principal.token_hash, lock=True)
        if user.auth_version != principal.auth_version or user.role != principal.role:
            fail('AUTH_CHANGED', '账号权限已变化，请刷新后重试', 409)
        policy = session.get(AgentWritePolicy, user.id, with_for_update=True)
        previous = list(policy.enabled_action_types) if policy else []
        if policy is None:
            policy = AgentWritePolicy(user_id=user.id, enabled_action_types=[], version=0)
            session.add(policy)
            session.flush()
        policy.enabled_action_types = sorted(set(enabled_action_types))
        policy.version += 1
        session.add(AuthAudit(actor_user_id=user.id, user_id=user.id,
                              action='AGENT_WRITE_POLICY_UPDATED',
                              changes={'before': previous, 'after': policy.enabled_action_types}))
        session.flush()
        return policy_body(session, user)


def _order_preview(session: Session, principal, order_id: int, raw_payload: dict):
    _ensure_action_role(principal, 'order.update')
    try:
        request = OrderUpdateRequest.model_validate(raw_payload)
    except ValidationError as exc:
        fail('INVALID_PAYLOAD', '订单修改内容不符合允许的字段和格式', 422)
    order = get_order(session, order_id)
    if order is None:
        fail('NOT_FOUND', '当前账号范围内未找到该订单', 404)
    if principal.role == 'user' and order.created_by_user_id != principal.user_id:
        fail('NOT_FOUND', '当前账号范围内未找到该订单', 404)
    if order.status != 'PENDING_SHIPMENT':
        fail('BUSINESS_STATE_CONFLICT', '订单当前状态不可修改', 409)
    payload = request.model_dump(mode='json', exclude_unset=True)
    normalized = resolve_changes(session, request.model_dump(exclude_unset=True))
    field_names = dict(FIELD_LABELS)
    field_names.update(REGION_LABELS)
    changes = []
    for field, after_value in normalized.items():
        if field not in payload:
            continue
        before_value = getattr(order, field)
        after_name_field = field.removesuffix('_id') + '_name' if field in REGION_FIELDS else None
        if after_name_field:
            before_name = getattr(order, after_name_field)
            after_name = normalized.get(after_name_field)
            before_display = f'{before_name}（{before_value}）' if before_name else ('未设置' if before_value is None else str(before_value))
            after_display = f'{after_name}（{after_value}）' if after_name else ('未设置' if after_value is None else str(after_value))
        else:
            before_display, after_display = before_value, after_value
            if hasattr(before_value, 'isoformat'):
                before_display = before_value.isoformat()
            if hasattr(after_value, 'isoformat'):
                after_display = after_value.isoformat()
        if before_value != after_value:
            changes.append({'field': field, 'label': field_names.get(field, field),
                            'before': before_display, 'after': after_display})
    if not changes:
        fail('INVALID_ACTION', '修改内容与订单当前信息相同', 422)
    return order, request, payload, {
        'subject': {'type': 'order', 'id': str(order.id), 'number': order.order_no},
        'changes': changes,
        'impact': ['订单仍须处于待发货状态；已创建运单后不能修改订单快照'],
    }


def _shipment_preview(session: Session, principal, shipment_id: int, raw_payload: dict):
    _ensure_action_role(principal, 'shipment.destination.update')
    try:
        request = ShipmentDestinationUpdateRequest.model_validate(raw_payload)
    except ValidationError:
        fail('INVALID_PAYLOAD', '目的站更正内容不符合允许的字段和格式', 422)
    shipment = session.get(Shipment, shipment_id)
    if shipment is None:
        fail('NOT_FOUND', '当前账号范围内未找到该运单', 404)
    if shipment.destination_station_id != request.expected_destination_station_id:
        fail('PROPOSAL_STALE', '运单目的站已变化，请重新查询', 409)
    if shipment.destination_station_id == request.destination_station_id:
        fail('INVALID_ACTION', '新目的站与当前目的站相同', 422)
    target = session.get(Station, request.destination_station_id)
    if not target or not target.enabled or not target.allows_delivery:
        fail('INVALID_NETWORK_CONFIGURATION', '目标站不存在、已停用或不允许派送', 422)
    states = list(session.scalars(select(TransportTask.status).join(
        TaskShipment, TaskShipment.task_id == TransportTask.id).where(
            TaskShipment.shipment_id == shipment_id,
            TaskShipment.association_state.in_(['ACTIVE', 'PLANNED']))))
    active_in_transit = session.scalar(select(TransportTask.id).join(
        TaskShipment, TaskShipment.task_id == TransportTask.id).where(
            TaskShipment.shipment_id == shipment_id,
            TaskShipment.association_state == 'ACTIVE',
            TransportTask.status == TaskStatus.IN_TRANSIT).limit(1)) is not None
    occupied = bool(states)
    unstarted = occupied and all(state in {TaskStatus.WAITING_CARGO, TaskStatus.WAITING_PREDECESSOR,
                                           TaskStatus.PENDING_DEPARTURE} for state in states)
    eligibility = build_destination_action(shipment.stage, occupied, active_in_transit, unstarted)
    if not eligibility['enabled']:
        fail('BUSINESS_STATE_CONFLICT', eligibility['reason'], 409)
    current_station = session.get(Station, shipment.destination_station_id)
    payload = request.model_dump(mode='json')
    return shipment, request, payload, {
        'subject': {'type': 'shipment', 'id': str(shipment.id), 'number': shipment.shipment_no},
        'changes': [{'field': 'destination_station_id', 'label': '目的站',
                     'before': f'{current_station.name}（{current_station.id}）',
                     'after': f'{target.name}（{target.id}）'},
                    {'field': 'reason', 'label': '更正原因',
                     'before': '—', 'after': request.reason}],
        'impact': ['需重新核对运输计划；目的站更正不会自动重写已有运输计划'],
    }


def create_preview(session: Session, principal, request, actor_session_id: UUID) -> dict:
    _scrub_expired_payloads(session, business_time.server_now())
    _ensure_action_role(principal, request.action_type)
    subject_id = int(request.subject_id)
    if request.action_type == 'order.update':
        try:
            typed = OrderUpdateRequest.model_validate(request.payload)
        except ValidationError:
            fail('INVALID_PAYLOAD', '订单修改内容不符合允许的字段和格式', 422)
        payload = typed.model_dump(mode='json', exclude_unset=True)
        subject_type = 'order'
    else:
        try:
            typed = ShipmentDestinationUpdateRequest.model_validate(request.payload)
        except ValidationError:
            fail('INVALID_PAYLOAD', '目的站更正内容不符合允许的字段和格式', 422)
        payload = typed.model_dump(mode='json')
        subject_type = 'shipment'
    digest = _hash({'action_type': request.action_type, 'subject_id': subject_id, 'payload': payload})
    # Serialize retries for the same Agent candidate before checking or inserting it.
    session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:candidate, 0))'),
                    {'candidate': str(request.candidate_id)})
    existing = session.scalar(select(AgentActionProposal).where(
        AgentActionProposal.candidate_id == request.candidate_id).with_for_update())
    if existing:
        if existing.actor_user_id != principal.user_id:
            fail('NOT_FOUND', '提案不存在', 404)
        if existing.payload_hash != digest or existing.agent_session_id != request.agent_session_id or existing.message_id != request.message_id:
            fail('PROPOSAL_CONSUMED', '该提案编号已绑定其他内容', 409)
        if existing.expires_at <= business_time.server_now():
            fail('PROPOSAL_EXPIRED', '提案已过期，请重新发起修改请求', 409)
        if existing.status != 'PREVIEWED':
            fail('PROPOSAL_CONSUMED', '该提案已结束，不能再次预览或执行', 409)
        return {'proposal_id': str(existing.id), **existing.preview}
    if request.action_type == 'order.update':
        subject, _, payload, display = _order_preview(session, principal, subject_id, request.payload)
    else:
        subject, _, payload, display = _shipment_preview(session, principal, subject_id, request.payload)
    login = session.get(LoginSession, principal.token_hash)
    if not login:
        fail('AUTH_REQUIRED', '请重新登录', 401)
    now = business_time.server_now()
    policy = session.get(AgentWritePolicy, principal.user_id)
    enabled = set(policy.enabled_action_types) if policy else set()
    execution_policy = ('AUTO_ALLOWED' if request.action_type in AUTO_ELIGIBLE_ACTION_TYPES and
                        request.action_type in enabled else 'CONFIRM_REQUIRED')
    version = subject.updated_at
    proposal = AgentActionProposal(
        actor_user_id=principal.user_id, actor_auth_version=principal.auth_version,
        policy_version=policy.version if policy else 0,
        actor_session_id=actor_session_id, candidate_id=request.candidate_id,
        agent_session_id=request.agent_session_id, message_id=request.message_id,
        action_type=request.action_type, subject_type=subject_type, subject_id=subject_id,
        payload=payload, payload_hash=digest, snapshot_version=version,
        preview={**display, 'candidate_id': str(request.candidate_id),
                 'action_type': request.action_type, 'execution_policy': execution_policy,
                 'expires_at': (now + PROPOSAL_LIFETIME).isoformat()},
        expires_at=now + PROPOSAL_LIFETIME,
    )
    session.add(proposal)
    session.flush()
    return {**proposal.preview, 'proposal_id': str(proposal.id)}


def _proposal_body(proposal: AgentActionProposal, status: str | None = None) -> dict:
    return {
        'proposal_id': str(proposal.id), 'candidate_id': str(proposal.candidate_id),
        'agent_session_id': str(proposal.agent_session_id), 'message_id': str(proposal.message_id),
        'action_type': proposal.action_type, 'status': status or proposal.status,
        'approval_mode': proposal.approval_mode,
        **proposal.preview,
        'result': proposal.result,
        'completed_at': proposal.completed_at.isoformat() if proposal.completed_at else None,
    }


def _scrub_expired_payloads(session: Session, now) -> int:
    """Drop personal action payloads after the confirmation window closes."""
    expired = list(session.scalars(select(AgentActionProposal).where(
        AgentActionProposal.expires_at <= now,
        AgentActionProposal.payload != {},
    ).with_for_update(skip_locked=True).limit(100)))
    for proposal in expired:
        if proposal.status == 'PREVIEWED':
            proposal.status = 'EXPIRED'
            proposal.completed_at = now
        proposal.payload = {}
        preview = dict(proposal.preview)
        preview['changes'] = [
            {'field': item.get('field'), 'label': item.get('label'),
             'before': '[已清理]', 'after': '[已清理]'}
            for item in preview.get('changes', [])
        ]
        proposal.preview = preview
    return len(expired)


def cleanup_expired_proposals() -> int:
    """Periodic bounded cleanup; request paths also opportunistically clean rows."""
    with SessionLocal.begin() as session:
        before = business_time.server_now()
        return _scrub_expired_payloads(session, before)


def read_proposal(session: Session, principal, proposal_id: UUID) -> dict:
    proposal = session.get(AgentActionProposal, proposal_id)
    if proposal is None:
        fail('NOT_FOUND', '提案不存在', 404)
    root = _root_session(session, principal)
    if proposal.actor_user_id != principal.user_id or root is None or proposal.actor_session_id != root.public_id:
        fail('NOT_FOUND', '提案不存在', 404)
    now = business_time.server_now()
    if proposal.expires_at <= now and proposal.payload:
        _scrub_expired_payloads(session, now)
        session.flush()
    return _proposal_body(proposal)


def reject_proposal(session: Session, principal, proposal_id: UUID) -> dict:
    with session.begin():
        business_time.begin_business_write(session)
        proposal = session.scalar(select(AgentActionProposal).where(
            AgentActionProposal.id == proposal_id).with_for_update())
        if proposal is None:
            fail('NOT_FOUND', '提案不存在', 404)
        root = _root_session(session, principal)
        if proposal.actor_user_id != principal.user_id or root is None or proposal.actor_session_id != root.public_id:
            fail('NOT_FOUND', '提案不存在', 404)
        if proposal.status != 'PREVIEWED':
            return _proposal_body(proposal)
        now = business_time.server_now()
        proposal.status = 'EXPIRED' if proposal.expires_at <= now else 'REJECTED'
        proposal.completed_at = now
        return _proposal_body(proposal)


def execute_proposal(session: Session, principal, proposal_id: UUID, mode: str, request_id: str) -> dict:
    owned_proposal = False
    business_execution_started = False
    try:
        with session.begin():
            clock = business_time.begin_business_write(session)
            proposal = session.scalar(select(AgentActionProposal).where(
                AgentActionProposal.id == proposal_id).with_for_update())
            if proposal is None:
                fail('NOT_FOUND', '提案不存在', 404)
            _scrub_expired_payloads(session, business_time.server_now())
            root = _root_session(session, principal)
            if proposal.actor_user_id != principal.user_id or root is None or proposal.actor_session_id != root.public_id:
                fail('NOT_FOUND', '提案不存在', 404)
            owned_proposal = True
            assert_current(session, principal)
            if proposal.actor_auth_version != principal.auth_version:
                fail('AUTH_CHANGED', '账号权限已变化，请重新预览', 409)
            if proposal.status == 'SUCCEEDED':
                return _proposal_body(proposal)
            if proposal.status != 'PREVIEWED':
                fail('PROPOSAL_CONSUMED', '该提案已结束，请重新生成预览', 409)
            if proposal.expires_at <= clock:
                proposal.status = 'EXPIRED'
                proposal.completed_at = clock
                proposal.payload = {}
                fail('PROPOSAL_EXPIRED', '提案已过期，请重新预览', 409)
            _ensure_action_role(principal, proposal.action_type)
            if mode == 'automatic':
                policy = session.get(AgentWritePolicy, principal.user_id, with_for_update=True)
                if (proposal.action_type not in AUTO_ELIGIBLE_ACTION_TYPES or not policy or
                        proposal.action_type not in policy.enabled_action_types or
                        proposal.policy_version != policy.version):
                    fail('FORBIDDEN', '该操作未获自动执行授权', 403)
            idempotency_key = uuid5(NAMESPACE_URL, f'jingpo:agent-action:{proposal.id}')
            business_execution_started = True
            if proposal.action_type == 'order.update':
                current_order = get_order(session, proposal.subject_id)
                if (current_order is None or
                        (principal.role == 'user' and current_order.created_by_user_id != principal.user_id)):
                    fail('NOT_FOUND', '当前账号范围内未找到该订单', 404)
                typed = OrderUpdateRequest.model_validate(proposal.payload)
                body = update_order(session, proposal.subject_id, typed, idempotency_key,
                                    expected_updated_at=proposal.snapshot_version)
                subject_number = body['order_no']
                state = body['status']
                summary = {'updated_at': body['updated_at'], 'status': state,
                           'changed_fields': list(proposal.payload)}
            else:
                typed = ShipmentDestinationUpdateRequest.model_validate(proposal.payload)
                body = update_shipment_destination(session, proposal.subject_id, typed, idempotency_key,
                                                   expected_updated_at=proposal.snapshot_version)
                subject_number = body['shipment_no']
                state = body['stage']
                summary = {'updated_at': body['updated_at'], 'stage': state,
                           'destination_station_id': body['destination_station_id']}
            operation = session.scalar(select(OperationLog).where(OperationLog.idempotency_key == idempotency_key))
            if operation:
                operation.agent_action_proposal_id = proposal.id
                operation.agent_candidate_id = proposal.candidate_id
                operation.agent_session_id = proposal.agent_session_id
                operation.agent_message_id = proposal.message_id
                proposal.operation_log_id = operation.id
            proposal.status = 'SUCCEEDED'
            proposal.approval_mode = mode
            proposal.result = {'subject': {'type': proposal.subject_type,
                                           'id': str(proposal.subject_id), 'number': subject_number},
                               'summary': summary, 'be_request_id': request_id}
            proposal.completed_at = business_time.server_now()
            return _proposal_body(proposal)
    except (NetworkError, OrderNotFoundError, OrderNotEditableError,
            ShipmentNotFoundError, InvalidShipmentDestinationError,
            IdempotencyKeyReusedError) as exc:
        # The failed business transaction is rolled back. Persist a terminal status
        # only after rollback, so no failed proposal can later be approved.
        if owned_proposal and business_execution_started:
            with session.begin():
                proposal = session.scalar(select(AgentActionProposal).where(
                    AgentActionProposal.id == proposal_id).with_for_update())
                if proposal and proposal.status == 'PREVIEWED':
                    proposal.status = 'EXPIRED' if proposal.expires_at <= business_time.server_now() else 'FAILED'
                    proposal.completed_at = business_time.server_now()
        if isinstance(exc, OrderNotFoundError) or isinstance(exc, ShipmentNotFoundError):
            raise NetworkError('NOT_FOUND', '当前账号范围内未找到该业务对象', 404) from exc
        if isinstance(exc, OrderNotEditableError):
            raise NetworkError('BUSINESS_STATE_CONFLICT', '订单当前状态不可修改', 409) from exc
        if isinstance(exc, InvalidShipmentDestinationError):
            code = 'PROPOSAL_STALE' if '变化' in str(exc) or 'PROPOSAL_STALE' in str(exc) else 'BUSINESS_STATE_CONFLICT'
            raise NetworkError(code, '运单当前状态已变化或不允许更正目的站，请重新预览', 409) from exc
        if isinstance(exc, IdempotencyKeyReusedError):
            raise NetworkError('PROPOSAL_CONSUMED', '该提案已执行，请查询执行状态', 409) from exc
        raise
