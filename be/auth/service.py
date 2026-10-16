import os
import smtplib
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from urllib.parse import urlencode

from sqlalchemy import select, update

from auth.models import AuthAudit, EmailToken, GoogleIdentity, LoginSession, User
from auth.security import digest, secret
from business_time import server_now
from errors import NetworkError


@dataclass(frozen=True)
class Principal:
    user_id: int
    role: str
    auth_version: int
    token_hash: str
    read_only: bool


def fail(code, message, status=400):
    raise NetworkError(code, message, status)


def audit(session, user, action, changes, actor=None):
    session.add(AuthAudit(user_id=user.id, actor_user_id=actor, action=action, changes=changes))


def user_body(session, user, read_only=False):
    return {
        'id': str(user.id), 'username': user.username, 'email': user.email,
        'role': user.role, 'enabled': user.enabled, 'email_verified': user.email_verified,
        'needs_username': user.needs_username, 'auth_version': user.auth_version,
        'google_bound': session.scalar(select(GoogleIdentity.subject).where(GoogleIdentity.user_id == user.id)) is not None,
        'has_password': user.password_hash is not None,
        'data_scope': 'own' if user.role == 'user' else 'company',
        'answer_style': 'technical' if user.role == 'developer' else 'business',
        'permissions': ([] if not user.email_verified or user.needs_username
                        else ['business:read'] if read_only else permissions(user.role)),
    }


def permissions(role):
    result = ['business:read']
    if role in ('admin', 'ops', 'user'):
        result += ['orders:create', 'orders:update', 'orders:cancel']
    if role in ('admin', 'ops'):
        result += ['logistics:write', 'network:write']
    if role == 'admin':
        result += ['users:manage']
    return result


def issue_session(session, user, channel='web', expires_at=None, parent_hash=None):
    token, csrf = secret(), secret()
    now = server_now()
    expires = min(expires_at, now + timedelta(hours=24)) if expires_at else now + timedelta(hours=24)
    login = LoginSession(token_hash=digest(token), user_id=user.id, channel=channel,
        csrf_hash=digest(csrf), created_at=now, expires_at=expires, parent_hash=parent_hash)
    session.add(login)
    session.flush()
    parent = session.get(LoginSession, parent_hash) if parent_hash else login
    return {'access_token': token, 'csrf_token': csrf, 'token_type': 'Bearer',
            'session_id': str(login.public_id), 'login_session_id': str(parent.public_id),
            'expires_at': expires.isoformat(), 'user': user_body(session, user, channel in ('agent', 'cli'))}


def resolve_session(session, token_hash, lock=False):
    login = session.get(LoginSession, token_hash)
    if login and lock:
        session.get(User, login.user_id, with_for_update=True, populate_existing=True)
        login = session.get(LoginSession, token_hash, with_for_update=True, populate_existing=True)
    now = server_now()
    if not login or login.revoked_at or login.expires_at <= now:
        fail('AUTH_REQUIRED', '请重新登录', 401)
    if login.parent_hash:
        parent = session.get(LoginSession, login.parent_hash, with_for_update=lock)
        if not parent or parent.revoked_at or parent.expires_at <= now:
            fail('AUTH_REQUIRED', '请重新登录', 401)
    user = session.get(User, login.user_id, with_for_update=lock, populate_existing=True)
    if not user or not user.enabled:
        fail('ACCOUNT_DISABLED', '账号已停用', 401)
    return login, user


def revoke_all(session, user):
    now = server_now()
    session.execute(update(LoginSession).where(LoginSession.user_id == user.id,
                    LoginSession.revoked_at.is_(None)).values(revoked_at=now))
    user.auth_version += 1


def create_email_token(session, user, purpose):
    now, raw = server_now(), secret()
    session.execute(update(EmailToken).where(EmailToken.user_id == user.id,
        EmailToken.purpose == purpose, EmailToken.consumed_at.is_(None)).values(consumed_at=now))
    session.add(EmailToken(token_hash=digest(raw), user_id=user.id, purpose=purpose,
        expires_at=now + timedelta(minutes=60 if purpose == 'verify' else 30)))
    return raw


def send_email(address, purpose, raw):
    host = os.getenv('SMTP_HOST')
    if not host:
        fail('EMAIL_UNAVAILABLE', '邮件服务未配置，请稍后重试', 503)
    base = os.getenv('AUTH_FRONTEND_URL', 'http://localhost:5173').rstrip('/')
    path = '/verify-email' if purpose == 'verify' else '/reset-password'
    message = EmailMessage()
    message['Subject'] = 'JINGPO 邮箱验证' if purpose == 'verify' else 'JINGPO 重置密码'
    message['From'] = os.getenv('SMTP_FROM', 'noreply@jingpo.local')
    message['To'] = address
    message.set_content(('请打开链接完成操作：\n' + base + path + '?' + urlencode({'token': raw})
                        + '\n如果不是你本人操作，请忽略此邮件。') if raw else
                        '本次请求没有产生可用的操作链接。请检查注册资料，或登录已有账号后操作。')
    try:
        port = int(os.getenv('SMTP_PORT', '587'))
        smtp_class = smtplib.SMTP_SSL if os.getenv('SMTP_SSL', 'false').lower() == 'true' else smtplib.SMTP
        with smtp_class(host, port, timeout=10) as smtp:
            if os.getenv('SMTP_STARTTLS', 'true').lower() == 'true' and smtp_class is smtplib.SMTP:
                smtp.starttls()
            if os.getenv('SMTP_USERNAME'):
                smtp.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
            smtp.send_message(message)
    except (OSError, ValueError, smtplib.SMTPException):
        fail('EMAIL_DELIVERY_FAILED', '邮件发送失败，请稍后重试', 503)


def consume_email_token(session, raw, purpose):
    entry = session.get(EmailToken, digest(raw))
    if entry:
        session.get(User, entry.user_id, with_for_update=True, populate_existing=True)
        entry = session.get(EmailToken, digest(raw), with_for_update=True, populate_existing=True)
    now = server_now()
    if not entry or entry.purpose != purpose or entry.consumed_at or entry.expires_at <= now:
        fail('INVALID_EMAIL_TOKEN', '链接已失效，请重新申请')
    user = session.get(User, entry.user_id, with_for_update=True)
    if not user.enabled:
        fail('INVALID_EMAIL_TOKEN', '链接已失效，请重新申请')
    entry.consumed_at = now
    return user
