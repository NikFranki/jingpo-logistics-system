from datetime import datetime, timedelta, timezone
from uuid import uuid4
from identity import Identity, IdentityError


def actor(user='1', role='ops', version=1, login=None):
    return Identity(user, login or str(uuid4()), version, role,
                    datetime.now(timezone.utc) + timedelta(hours=1),
                    'own' if role == 'user' else 'company',
                    'technical' if role == 'developer' else 'business',
                    'test-token-' + user)


class Auth:
    def __init__(self, identity=None):
        self.identity = identity or actor()
        self.revoked = False
        self.changed = False
        self.checks = 0

    async def authenticate(self, token):
        if token != 'test-token':
            raise IdentityError()
        await self.recheck(self.identity)
        return self.identity

    async def recheck(self, identity):
        self.checks += 1
        if self.revoked:
            raise IdentityError()
        if self.changed or identity.auth_version != self.identity.auth_version:
            raise IdentityError(409, 'AUTH_CHANGED', '权限已变化')
