import json

from fastapi.responses import JSONResponse
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

from auth.dependencies import assert_current
from db import SessionLocal
from errors import NetworkError
from models import OperationLog, Order, Shipment


@event.listens_for(Session, 'do_orm_execute')
def scope_business_queries(state):
    principal = state.session.info.get('principal')
    if not principal or principal.role != 'user' or not state.is_select:
        return
    user_id = principal.user_id
    owned_orders = select(Order.id).where(Order.created_by_user_id == user_id)
    state.statement = state.statement.options(
        with_loader_criteria(Order, Order.created_by_user_id == user_id, include_aliases=True),
        with_loader_criteria(Shipment, Shipment.order_id.in_(owned_orders), include_aliases=True),
    )


@event.listens_for(Session, 'before_flush')
def stamp_actor(session, flush_context, instances):
    principal = session.info.get('principal')
    if not principal:
        return
    for value in session.new:
        if isinstance(value, Order):
            value.created_by_user_id = principal.user_id
        elif isinstance(value, OperationLog):
            value.actor_user_id = principal.user_id


def redact(value, role):
    if isinstance(value, list):
        return [redact(item, role) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: redact(item, role) for key, item in value.items()}
    if role == 'user':
        for field in ('waiting_members', 'shared_members'):
            if field in result:
                result[field] = []
        for field in ('cancel_reason', 'release_reason'):
            if field in result and 'order_no' not in result:
                result[field] = None
        for field in ('reason',):
            if field in result:
                result[field] = '运输安排已更新'
    if role in ('developer', 'user') and 'allowed_actions' in result:
        for action in result['allowed_actions']:
            action.update(enabled=False, reason='当前角色仅可查看物流进度', reason_code='FORBIDDEN')
    return result


async def protect_response(request, response):
    principal = getattr(request.state, 'principal', None)
    path = request.url.path
    if not principal or path.startswith(('/api/v1/auth', '/api/v1/admin')) or response.status_code >= 400:
        return response
    try:
        with SessionLocal() as session:
            assert_current(session, principal)
    except NetworkError as error:
        return JSONResponse(status_code=error.status, content={
            'error': {'code': error.code, 'message': error.message, 'details': None},
            'request_id': request.state.request_id})
    if principal.role not in ('user', 'developer'):
        response.headers['Cache-Control'] = 'no-store'
        return response
    if not response.headers.get('content-type', '').startswith('application/json'):
        return response
    raw = b''.join([chunk async for chunk in response.body_iterator])
    payload = json.loads(raw)
    result = JSONResponse(redact(payload, principal.role), status_code=response.status_code,
                          background=response.background)
    result.raw_headers = [(key, value) for key, value in response.raw_headers
                          if key.lower() not in (b'content-length', b'cache-control')]
    result.headers['Content-Length'] = str(len(result.body))
    result.headers['Cache-Control'] = 'no-store'
    return result
