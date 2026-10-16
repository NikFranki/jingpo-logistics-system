import hmac
import os
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import Depends, Header, Request
from fastapi.security import HTTPBearer
from sqlalchemy import select

from auth.security import digest
from auth.service import Principal, fail, resolve_session
from db import SessionLocal
from models import Order, Shipment


COOKIE = 'jingpo_session'
CSRF_COOKIE = 'jingpo_csrf'
bearer_scheme = HTTPBearer(auto_error=False)


def authenticate(request: Request, business=False):
    header = request.headers.get('Authorization', '')
    bearer = bool(header)
    if bearer and not header.startswith('Bearer '):
        fail('AUTH_REQUIRED', '请重新登录', 401)
    token = header[7:] if bearer else request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        fail('AUTH_REQUIRED', '请先登录', 401)
    with SessionLocal() as session:
        login, user = resolve_session(session, digest(token))
        if not bearer and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            allowed = os.getenv('AUTH_FRONTEND_URL', 'http://localhost:5173').rstrip('/')
            csrf = request.headers.get('X-CSRF-Token', '')
            if origin != allowed or not csrf or not hmac.compare_digest(digest(csrf), login.csrf_hash):
                fail('CSRF_REJECTED', '页面已失效，请刷新后重试', 403)
        if business and not user.email_verified:
            fail('EMAIL_NOT_VERIFIED', '请先验证邮箱', 403)
        if business and user.needs_username:
            fail('USERNAME_REQUIRED', '请先补全用户名', 403)
        principal = Principal(user.id, user.role, user.auth_version, login.token_hash,
                              login.channel in ('agent', 'cli'))
    request.state.principal = principal
    return principal


def current_user(request: Request, credentials=Depends(bearer_scheme)):
    return authenticate(request)


def business_user(request: Request, credentials=Depends(bearer_scheme)):
    return authenticate(request, business=True)


def scoped_key(request: Request, idempotency_key: UUID = Header(alias='Idempotency-Key')):
    principal = request.state.principal
    return uuid5(NAMESPACE_URL, f'jingpo:user:{principal.user_id}:{idempotency_key}')


def authorize_business(request: Request, credentials=Depends(bearer_scheme)):
    principal = authenticate(request, business=True)
    method = request.method
    read = method in ('GET', 'HEAD')
    if principal.read_only and not read:
        fail('READ_ONLY_CREDENTIAL', '该登录只允许查询', 403)
    if principal.role == 'developer' and not read:
        fail('FORBIDDEN', '当前角色只允许查询', 403)
    if principal.role in ('admin', 'ops', 'developer'):
        return principal
    route = request.scope['route'].path
    allowed_read = (
        route in ('/api/v1/orders', '/api/v1/orders/{order_id}', '/api/v1/shipments', '/api/v1/server-time')
        or route in ('/api/v1/provinces', '/api/v1/cities', '/api/v1/districts')
        or route == '/api/v1/ordering-options'
        or (route.startswith('/api/v1/shipments/{shipment_id}')
            and route.rsplit('/', 1)[-1] in ('{shipment_id}', 'schedule', 'schedule-history', 'destination-changes', 'transport-tasks'))
    )
    allowed_write = route in ('/api/v1/orders/create', '/api/v1/orders/{order_id}', '/api/v1/orders/{order_id}/cancel')
    if not ((read and allowed_read) or (not read and allowed_write)):
        fail('FORBIDDEN', '当前角色无权执行此操作', 403)
    with SessionLocal() as session:
        order_id = request.path_params.get('order_id')
        shipment_id = request.path_params.get('shipment_id')
        if order_id is not None:
            visible = session.scalar(select(Order.id).where(Order.id == int(order_id),
                                      Order.created_by_user_id == principal.user_id))
            if visible is None:
                fail('NOT_FOUND', '记录不存在', 404)
        if shipment_id is not None:
            visible = session.scalar(select(Shipment.id).join(Order).where(Shipment.id == int(shipment_id),
                                      Order.created_by_user_id == principal.user_id))
            if visible is None:
                fail('NOT_FOUND', '记录不存在', 404)
    return principal


def assert_current(session, principal):
    login, user = resolve_session(session, principal.token_hash, lock=True)
    if user.auth_version != principal.auth_version or user.role != principal.role:
        fail('AUTH_CHANGED', '账号权限已变化，请刷新后重试', 409)
    if not user.email_verified or user.needs_username:
        fail('FORBIDDEN', '请先完成账号验证', 403)
