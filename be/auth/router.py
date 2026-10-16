import os
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from auth.dependencies import COOKIE, CSRF_COOKIE, business_user, current_user
from auth.models import AuthAudit, EmailToken, LoginSession, User
from auth.schemas import CompleteProfile, EmailRequest, Login, Recheck, Register, ResetPassword, TokenRequest, UserUpdate
from auth.security import digest, password_hash, password_matches, throttle
from auth.service import audit, consume_email_token, create_email_token, fail, issue_session, resolve_session, revoke_all, send_email, user_body
from business_time import server_now
from db import SessionLocal


router = APIRouter(prefix='/api/v1/auth', tags=['auth'])
admin_router = APIRouter(prefix='/api/v1/admin', tags=['users'])
Current = Annotated[object, Depends(current_user)]
Business = Annotated[object, Depends(business_user)]
EMAIL_RESPONSE = {'message': '若账号资料可用，邮箱将收到后续操作说明。'}


def limited(request, scope, identifier=''):
    address = request.client.host if request.client else 'unknown'
    throttle(scope + ':ip', address, 60)
    if identifier:
        throttle(scope + ':account', identifier, 10)


def cookie_login(response, result, request):
    previous_token = request.cookies.get(COOKIE)
    if previous_token:
        with SessionLocal.begin() as session:
            previous = session.get(LoginSession, digest(previous_token), with_for_update=True)
            if previous and not previous.revoked_at:
                previous.revoked_at = server_now()
    secure = os.getenv('AUTH_COOKIE_SECURE', 'true').lower() == 'true'
    response.set_cookie(COOKIE, result.pop('access_token'), max_age=86400, httponly=True, secure=secure, samesite='lax', path='/')
    response.set_cookie(CSRF_COOKIE, result['csrf_token'], max_age=86400, httponly=False, secure=secure, samesite='lax', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return result


def deliver(user_id, address, purpose, raw):
    try:
        send_email(address, purpose, raw)
    except Exception:
        if user_id and raw:
            with SessionLocal.begin() as session:
                entry = session.get(EmailToken, digest(raw))
                if entry:
                    entry.consumed_at = server_now()
        raise


@router.post('/register', status_code=202)
def register(body: Register, request: Request):
    limited(request, 'register', body.email)
    if not os.getenv('SMTP_HOST'):
        fail('EMAIL_UNAVAILABLE', '邮件服务未配置，请稍后重试', 503)
    raw, user_id = None, None
    encoded = password_hash(body.password)
    try:
        with SessionLocal.begin() as session:
            exists = session.scalar(select(User.id).where(or_(User.username == body.username, User.email == body.email)))
            if exists is None:
                user = User(username=body.username, email=body.email, password_hash=encoded, role='user')
                session.add(user)
                session.flush()
                raw = create_email_token(session, user, 'verify')
                user_id = user.id
                audit(session, user, 'REGISTER', {})
    except IntegrityError:
        raw, user_id = None, None
    deliver(user_id, body.email, 'verify', raw)
    return EMAIL_RESPONSE


@router.post('/login')
def login(body: Login, request: Request, response: Response):
    username = body.username.strip().lower()
    limited(request, 'login', username)
    with SessionLocal.begin() as session:
        user = session.scalar(select(User).where(User.username == username).with_for_update())
        if not password_matches(body.password.get_secret_value(), user.password_hash if user else None) or not user or not user.enabled:
            fail('INVALID_CREDENTIALS', '用户名或密码不正确', 401)
        result = issue_session(session, user)
        audit(session, user, 'LOGIN', {'channel': 'web'}, user.id)
    return cookie_login(response, result, request)


@router.get('/me')
@router.get('/introspect')
def me(principal: Current, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with SessionLocal() as session:
        login, user = resolve_session(session, principal.token_hash)
        parent = session.get(LoginSession, login.parent_hash) if login.parent_hash else login
        return {'user': user_body(session, user, principal.read_only), 'expires_at': login.expires_at.isoformat(),
                'session_id': str(login.public_id), 'login_session_id': str(parent.public_id),
                'read_only': principal.read_only}


@router.post('/recheck')
def recheck(body: Recheck, principal: Business):
    if body.auth_version != principal.auth_version:
        fail('AUTH_CHANGED', '账号权限已变化，请重新查询', 409)
    return {'valid': True, 'auth_version': principal.auth_version}


@router.post('/logout')
def logout(principal: Current, response: Response):
    with SessionLocal.begin() as session:
        login, user = resolve_session(session, principal.token_hash, lock=True)
        login.revoked_at = server_now()
        audit(session, user, 'LOGOUT', {}, user.id)
    response.delete_cookie(COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    return {'message': '已退出登录'}


@router.post('/verify-email')
def verify_email(body: TokenRequest, request: Request):
    limited(request, 'verify')
    with SessionLocal.begin() as session:
        user = consume_email_token(session, body.token, 'verify')
        if not user.email_verified:
            user.auth_version += 1
        user.email_verified = True
        audit(session, user, 'VERIFY_EMAIL', {}, user.id)
    return {'message': '邮箱已验证'}


@router.post('/resend-verification', status_code=202)
def resend(body: EmailRequest, request: Request):
    return email_action(body, request, 'verify')


@router.post('/forgot-password', status_code=202)
def forgot_password(body: EmailRequest, request: Request):
    return email_action(body, request, 'reset')


def email_action(body, request, purpose):
    limited(request, purpose, body.email)
    if not os.getenv('SMTP_HOST'):
        fail('EMAIL_UNAVAILABLE', '邮件服务未配置，请稍后重试', 503)
    raw, user_id = None, None
    with SessionLocal.begin() as session:
        user = session.scalar(select(User).where(User.email == body.email).with_for_update())
        if user and user.enabled and (purpose == 'reset' or not user.email_verified):
            raw, user_id = create_email_token(session, user, purpose), user.id
    deliver(user_id, body.email, purpose, raw)
    return EMAIL_RESPONSE


@router.post('/reset-password')
def reset_password(body: ResetPassword, request: Request):
    limited(request, 'reset-consume')
    encoded = password_hash(body.password)
    with SessionLocal.begin() as session:
        user = consume_email_token(session, body.token, 'reset')
        user.password_hash = encoded
        revoke_all(session, user)
        audit(session, user, 'RESET_PASSWORD', {}, user.id)
    return {'message': '密码已更新，请重新登录'}


@router.post('/complete-profile')
def complete_profile(body: CompleteProfile, principal: Current):
    try:
        with SessionLocal.begin() as session:
            _, user = resolve_session(session, principal.token_hash, lock=True)
            if not user.needs_username:
                fail('PROFILE_ALREADY_COMPLETE', '用户名已设置', 409)
            user.username, user.needs_username = body.username, False
            user.auth_version += 1
            session.flush()
            audit(session, user, 'COMPLETE_PROFILE', {'username': user.username}, user.id)
            return user_body(session, user)
    except IntegrityError:
        fail('USERNAME_UNAVAILABLE', '用户名不可用，请更换', 409)


@router.post('/agent-token')
def agent_token(principal: Business, request: Request, response: Response):
    if principal.read_only:
        fail('FORBIDDEN', '不能使用查询凭据创建新的登录', 403)
    limited(request, 'agent-token', str(principal.user_id))
    response.headers['Cache-Control'] = 'no-store'
    with SessionLocal.begin() as session:
        parent, user = resolve_session(session, principal.token_hash, lock=True)
        return issue_session(session, user, 'agent', parent.expires_at, parent.token_hash)


def require_admin(principal):
    if principal.role != 'admin' or principal.read_only:
        fail('FORBIDDEN', '仅管理员可以管理用户', 403)


@admin_router.get('/users')
def users(principal: Business, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str = Query('', max_length=100)):
    require_admin(principal)
    with SessionLocal() as session:
        query = select(User)
        if search:
            query = query.where(or_(User.username.contains(search, autoescape=True), User.email.contains(search, autoescape=True)))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(User.id).offset((page - 1) * page_size).limit(page_size))
        return {'items': [user_body(session, user) for user in rows], 'total': total, 'page': page, 'page_size': page_size}


@admin_router.patch('/users/{user_id}')
def update_user(user_id: int, body: UserUpdate, principal: Business):
    require_admin(principal)
    changes = body.model_dump(exclude_unset=True)
    if not changes or any(value is None for value in changes.values()):
        fail('INVALID_USER_UPDATE', '至少提供一个有效修改字段', 422)
    with SessionLocal.begin() as session:
        from business_time import begin_business_write
        begin_business_write(session)
        _, admin = resolve_session(session, principal.token_hash, lock=True)
        if admin.role != 'admin':
            fail('FORBIDDEN', '仅管理员可以管理用户', 403)
        user = session.get(User, user_id, with_for_update=True)
        if not user:
            fail('NOT_FOUND', '用户不存在', 404)
        if user.role == 'admin':
            fail('ADMIN_PROTECTED', '不能降级或停用唯一管理员', 409)
        before = {field: getattr(user, field) for field in changes}
        for field, value in changes.items():
            setattr(user, field, value)
        if changes.get('enabled') is False:
            revoke_all(session, user)
        elif before != changes:
            user.auth_version += 1
        audit(session, user, 'UPDATE_USER', {'before': before, 'after': changes}, principal.user_id)
        session.flush()
        return user_body(session, user)


@admin_router.get('/users/{user_id}/audit')
def user_audit(user_id: int, principal: Business, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    require_admin(principal)
    with SessionLocal() as session:
        query = select(AuthAudit).where(AuthAudit.user_id == user_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(AuthAudit.id.desc()).offset((page - 1) * page_size).limit(page_size))
        return {'items': [{'id': str(row.id), 'actor_user_id': str(row.actor_user_id) if row.actor_user_id else None,
                'user_id': str(row.user_id), 'action': row.action, 'changes': row.changes, 'occurred_at': row.occurred_at}
                for row in rows], 'total': total, 'page': page, 'page_size': page_size}
