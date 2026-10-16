import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlalchemy.dialects.postgresql import insert

from business_time import server_now
from db import SessionLocal
from errors import NetworkError
from auth.models import RateBucket


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def secret():
    return secrets.token_urlsafe(32)


def normalize_username(value):
    if not isinstance(value, str):
        raise ValueError('用户名必须是文本')
    if not 1 <= len(value) <= 40:
        raise ValueError('用户名长度需为 1～40 位')
    return value


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError('邮箱必须是文本')
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value):
        raise ValueError('邮箱格式不正确')
    return value


def validate_password(value):
    if not isinstance(value, str):
        raise ValueError('密码必须是文本')
    if not 12 <= len(value) <= 128 or not any(char.isalpha() for char in value) or not any(char.isdigit() for char in value):
        raise ValueError('密码需为 12～128 位，包含字母和数字')
    return value


def password_hash(value):
    salt = secrets.token_hex(16)
    encoded = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=2**17, r=8, p=1,
                             maxmem=256 * 1024 * 1024, dklen=32).hex()
    return f'scrypt${salt}${encoded}'


def password_matches(value, encoded):
    if not encoded:
        encoded = 'scrypt$' + '00' * 16 + '$' + '00' * 32
    try:
        scheme, salt, expected = encoded.split('$')
        if scheme != 'scrypt':
            return False
        actual = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=2**17, r=8, p=1,
                                maxmem=256 * 1024 * 1024, dklen=32).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def throttle(scope, identifier, limit=10, seconds=900):
    now = server_now()
    key = digest(f'{scope}:{identifier}')
    with SessionLocal.begin() as session:
        statement = insert(RateBucket).values(key=key, count=1, expires_at=now + timedelta(seconds=seconds))
        statement = statement.on_conflict_do_update(index_elements=[RateBucket.key], set_={
            'count': RateBucket.count + 1,
        }, where=RateBucket.expires_at > now).returning(RateBucket.count)
        count = session.scalar(statement)
        if count is None:
            bucket = session.get(RateBucket, key, with_for_update=True)
            bucket.count, bucket.expires_at = 1, now + timedelta(seconds=seconds)
            count = 1
    if count > limit:
        raise NetworkError('RATE_LIMITED', '操作太频繁，请稍后重试', 429)
