import base64
import hashlib
import json
import os
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from auth.dependencies import COOKIE, business_user
from auth.models import DeviceGrant, GoogleIdentity, OAuthAttempt, User
from auth.router import cookie_login, limited
from auth.schemas import DeviceApproval, DeviceRequest
from auth.security import digest, normalize_email, secret
from auth.service import audit, fail, issue_session, resolve_session, user_body
from business_time import server_now
from db import SessionLocal


router = APIRouter(prefix='/api/v1/auth', tags=['auth'])
STATE_COOKIE = 'jingpo_google_state'


def google_config():
    client_id, client_secret, redirect_uri = (os.getenv('GOOGLE_CLIENT_ID'), os.getenv('GOOGLE_CLIENT_SECRET'), os.getenv('GOOGLE_REDIRECT_URI'))
    if not all((client_id, client_secret, redirect_uri)):
        fail('GOOGLE_UNAVAILABLE', 'Google 登录尚未配置', 503)
    return client_id, client_secret, redirect_uri


def google_start(request, response, principal=None):
    client_id, _, redirect_uri = google_config()
    limited(request, 'google-start')
    state, nonce, verifier = secret(), secret(), secret()
    with SessionLocal.begin() as session:
        session.add(OAuthAttempt(state_hash=digest(state), nonce=nonce, verifier=verifier,
            session_hash=principal.token_hash if principal else None, expires_at=server_now() + timedelta(minutes=10)))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True,
        secure=os.getenv('AUTH_COOKIE_SECURE', 'true').lower() == 'true', samesite='lax', path='/api/v1/auth/google')
    response.headers['Cache-Control'] = 'no-store'
    return {'authorization_url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code', 'scope': 'openid email',
        'state': state, 'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account'})}


@router.get('/google/start')
def start_google(request: Request, response: Response):
    return google_start(request, response)


@router.post('/google/bind/start')
def bind_google(request: Request, response: Response, principal=Depends(business_user)):
    if principal.read_only:
        fail('FORBIDDEN', '查询凭据不能绑定身份', 403)
    return google_start(request, response, principal)


def exchange_google(code, verifier, nonce):
    client_id, client_secret, redirect_uri = google_config()
    body = urlencode({'code': code, 'client_id': client_id, 'client_secret': client_secret,
        'redirect_uri': redirect_uri, 'grant_type': 'authorization_code', 'code_verifier': verifier}).encode()
    try:
        with urlopen(URLRequest('https://oauth2.googleapis.com/token', data=body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=10) as response:
            tokens = json.load(response)
        from google.auth.transport.requests import Request as GoogleRequest
        from google.auth.exceptions import GoogleAuthError
        from google.oauth2.id_token import verify_oauth2_token
        try:
            claims = verify_oauth2_token(tokens['id_token'], GoogleRequest(), client_id)
        except GoogleAuthError:
            fail('GOOGLE_UNAVAILABLE', 'Google 校验服务暂不可用，请稍后重试', 503)
        if claims.get('nonce') != nonce or not claims.get('sub') or len(claims['sub']) > 255:
            raise ValueError('invalid identity')
        return claims
    except (HTTPError, URLError, ValueError, KeyError, OSError):
        fail('GOOGLE_AUTH_FAILED', 'Google 身份校验失败，请重新登录', 401)


@router.get('/google/callback')
def google_callback(request: Request, state: str = Query('', max_length=256), code: str = Query('', max_length=4096), error: str = Query('', max_length=100)):
    cookie = request.cookies.get(STATE_COOKIE)
    if not cookie or not secrets.compare_digest(cookie, state):
        fail('OAUTH_STATE_INVALID', '登录请求已失效，请重试', 400)
    with SessionLocal.begin() as session:
        attempt = session.get(OAuthAttempt, digest(state), with_for_update=True)
        if not attempt or attempt.consumed_at or attempt.expires_at <= server_now():
            fail('OAUTH_STATE_INVALID', '登录请求已失效，请重试', 400)
        attempt.consumed_at = server_now()
        verifier, nonce, parent_hash = attempt.verifier, attempt.nonce, attempt.session_hash
        attempt.verifier = ''
    if error or not code:
        fail('GOOGLE_AUTH_CANCELLED', 'Google 登录已取消')
    claims = exchange_google(code, verifier, nonce)
    try:
        email = normalize_email(claims.get('email', ''))
    except ValueError:
        fail('GOOGLE_EMAIL_REQUIRED', 'Google 未提供有效邮箱')
    trusted = claims.get('email_verified') is True and (email.endswith('@gmail.com') or bool(claims.get('hd')))
    try:
        with SessionLocal.begin() as session:
            from business_time import begin_business_write
            begin_business_write(session)
            identity = session.get(GoogleIdentity, claims['sub'])
            if parent_hash:
                browser_token = request.cookies.get(COOKIE, '')
                if not browser_token or not secrets.compare_digest(digest(browser_token), parent_hash):
                    fail('OAUTH_STATE_INVALID', '登录账号已变化，请重新绑定', 409)
                _, user = resolve_session(session, parent_hash, lock=True)
                if not user.email_verified:
                    fail('EMAIL_NOT_VERIFIED', '请先验证原账号邮箱', 403)
                if identity and identity.user_id != user.id:
                    fail('GOOGLE_ALREADY_BOUND', '该 Google 身份已绑定其他账号', 409)
                existing = session.scalar(select(GoogleIdentity).where(GoogleIdentity.user_id == user.id))
                if existing and existing.subject != claims['sub']:
                    fail('GOOGLE_ALREADY_BOUND', '该账号已绑定其他 Google 身份', 409)
                if not identity:
                    session.add(GoogleIdentity(subject=claims['sub'], user_id=user.id))
                audit(session, user, 'BIND_GOOGLE', {}, user.id)
                result = None
            elif identity:
                user = session.get(User, identity.user_id, with_for_update=True)
                if not user.enabled:
                    fail('ACCOUNT_DISABLED', '账号已停用', 401)
                result = issue_session(session, user)
            else:
                if session.scalar(select(User.id).where(User.email == email)):
                    fail('GOOGLE_ACCOUNT_CONFLICT', '请先登录原账号，再主动绑定 Google', 409)
                user = User(username='google-' + secrets.token_hex(12), email=email, role='user',
                    email_verified=trusted, needs_username=True)
                session.add(user)
                session.flush()
                session.add(GoogleIdentity(subject=claims['sub'], user_id=user.id))
                audit(session, user, 'REGISTER_GOOGLE', {})
                result = issue_session(session, user)
    except IntegrityError:
        fail('GOOGLE_ACCOUNT_CONFLICT', '账号发生冲突，请登录原账号后绑定', 409)
    response = RedirectResponse(os.getenv('AUTH_FRONTEND_URL', 'http://localhost:5173').rstrip('/') + '/oauth/google/callback', status_code=303)
    response.delete_cookie(STATE_COOKIE, path='/api/v1/auth/google')
    if result:
        cookie_login(response, result, request)
    return response


@router.post('/cli/authorize', status_code=201)
def cli_authorize(request: Request, response: Response):
    limited(request, 'cli-authorize')
    raw = secret()
    user_code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
    with SessionLocal.begin() as session:
        session.add(DeviceGrant(device_hash=digest(raw), user_code_hash=digest(user_code),
            expires_at=server_now() + timedelta(minutes=10)))
    response.headers['Cache-Control'] = 'no-store'
    return {'device_code': raw, 'user_code': user_code, 'expires_in': 600, 'interval': 5,
        'verification_uri': os.getenv('AUTH_FRONTEND_URL', 'http://localhost:5173').rstrip('/') + '/cli/authorize'}


@router.post('/cli/approve')
def cli_approve(body: DeviceApproval, request: Request, principal=Depends(business_user)):
    limited(request, 'cli-approve', str(principal.user_id))
    if principal.read_only:
        fail('FORBIDDEN', '请在网页登录后授权 CLI', 403)
    with SessionLocal.begin() as session:
        grant = session.scalar(select(DeviceGrant).where(DeviceGrant.user_code_hash == digest(body.user_code)).with_for_update())
        if not grant or grant.expires_at <= server_now() or grant.consumed_at or grant.user_id or grant.denied:
            fail('DEVICE_CODE_INVALID', '授权码已失效')
        _, user = resolve_session(session, principal.token_hash, lock=True)
        grant.denied = not body.approve
        if body.approve:
            grant.user_id, grant.auth_version = user.id, user.auth_version
        audit(session, user, 'CLI_APPROVAL', {'approved': body.approve}, user.id)
    return {'message': '已批准授权' if body.approve else '已拒绝授权'}


@router.post('/cli/token')
def cli_token(body: DeviceRequest, request: Request, response: Response):
    from auth.security import throttle
    throttle('cli-poll', request.client.host if request.client else 'unknown', 300)
    result = None
    with SessionLocal.begin() as session:
        grant = session.get(DeviceGrant, digest(body.device_code), with_for_update=True)
        now = server_now()
        if not grant or grant.expires_at <= now or grant.consumed_at:
            error = 'expired_token'
        elif grant.denied:
            error = 'access_denied'
        elif grant.last_polled_at and now < grant.last_polled_at + timedelta(seconds=5):
            error = 'slow_down'
        else:
            grant.last_polled_at = now
            if not grant.user_id:
                error = 'authorization_pending'
            else:
                user = session.get(User, grant.user_id, with_for_update=True)
                if not user.enabled or not user.email_verified or user.needs_username or user.auth_version != grant.auth_version:
                    error = 'access_denied'
                    grant.denied = True
                else:
                    result = issue_session(session, user, 'cli')
                    grant.consumed_at = now
                    audit(session, user, 'LOGIN', {'channel': 'cli'}, user.id)
    response.headers['Cache-Control'] = 'no-store'
    if result:
        return result
    response.status_code = 400
    return {'error': error}


@router.post('/cli/cancel')
def cli_cancel(body: DeviceRequest):
    with SessionLocal.begin() as session:
        grant = session.get(DeviceGrant, digest(body.device_code), with_for_update=True)
        if grant and not grant.consumed_at:
            grant.denied = True
    return {'message': '已取消授权'}
