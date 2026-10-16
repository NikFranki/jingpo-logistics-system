from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class User(Base):
    __tablename__ = 'auth_users'
    __table_args__ = (
        CheckConstraint("role IN ('admin','ops','developer','user')", name='ck_auth_users_role'),
        CheckConstraint("role != 'admin' OR enabled", name='ck_auth_admin_enabled'),
        Index('uq_auth_single_admin', 'role', unique=True, postgresql_where=text("role = 'admin'")),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), server_default='user')
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text('true'))
    email_verified: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    needs_username: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    auth_version: Mapped[int] = mapped_column(Integer, server_default='1')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[int] = mapped_column(ForeignKey('auth_users.id'), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    parent_hash: Mapped[str | None] = mapped_column(ForeignKey('auth_sessions.token_hash'))
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailToken(Base):
    __tablename__ = 'auth_email_tokens'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('auth_users.id'), index=True)
    purpose: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoogleIdentity(Base):
    __tablename__ = 'auth_google_identities'
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('auth_users.id'), unique=True)


class OAuthAttempt(Base):
    __tablename__ = 'auth_oauth_attempts'
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128))
    verifier: Mapped[str] = mapped_column(String(128))
    session_hash: Mapped[str | None] = mapped_column(ForeignKey('auth_sessions.token_hash'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceGrant(Base):
    __tablename__ = 'auth_device_grants'
    device_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('auth_users.id'))
    auth_version: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    denied: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))


class AuthAudit(Base):
    __tablename__ = 'auth_audit'
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey('auth_users.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('auth_users.id'))
    action: Mapped[str] = mapped_column(String(64))
    changes: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateBucket(Base):
    __tablename__ = 'auth_rate_buckets'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
