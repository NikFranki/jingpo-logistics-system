"""Online BE identity checks. Credentials never enter graph state or model messages."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from time import monotonic

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class IdentityError(Exception):
    def __init__(self, status=401, code="AUTH_REQUIRED", message="登录已失效，请重新登录。"):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


class User(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    id: str = Field(pattern=r"^[1-9][0-9]*$")
    role: str
    enabled: bool
    email_verified: bool
    needs_username: bool
    auth_version: int = Field(ge=0)
    data_scope: Literal["own", "company"]
    answer_style: Literal["business", "technical"]
    permissions: list[str]


class Introspection(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    user: User
    session_id: str
    login_session_id: str
    expires_at: str
    read_only: bool


@dataclass(frozen=True)
class Identity:
    user_id: str
    login_session_id: str
    auth_version: int
    role: str
    expires_at: datetime
    data_scope: str
    answer_style: str
    token: str = field(repr=False, compare=False)

    @property
    def owner(self):
        return self.user_id, self.login_session_id

    @property
    def own_only(self):
        return self.data_scope == "own"


class IdentityClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    async def request(self, method, path, token, **kwargs):
        if not token or len(token) > 256 or any(c.isspace() for c in token):
            raise IdentityError()
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=3, follow_redirects=False) as client:
                response = await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.RequestError:
            raise IdentityError(503, "AUTH_SERVICE_UNAVAILABLE", "身份服务暂不可用，请稍后重试。") from None
        if response.status_code == 409:
            raise IdentityError(409, "AUTH_CHANGED", "账号权限已变化，请开始新对话。")
        if response.status_code in (401, 403):
            raise IdentityError()
        if response.status_code != 200:
            raise IdentityError(503, "AUTH_SERVICE_UNAVAILABLE", "身份服务暂不可用，请稍后重试。")
        return response

    async def authenticate(self, token):
        response = await self.request("GET", "/api/v1/auth/introspect", token)
        try:
            info = Introspection.model_validate(response.json())
            UUID(info.session_id)
            UUID(info.login_session_id)
            expires = datetime.fromisoformat(info.expires_at.replace("Z", "+00:00"))
            user = info.user
            if (not info.read_only or user.role not in {"admin", "ops", "developer", "user"}
                    or not user.enabled or not user.email_verified or user.needs_username
                    or "business:read" not in user.permissions
                    or expires.utcoffset() is None or expires <= datetime.now(timezone.utc)):
                raise IdentityError()
        except (ValueError, TypeError, ValidationError):
            raise IdentityError(503, "AUTH_SERVICE_UNAVAILABLE", "身份响应异常，请稍后重试。") from None
        return Identity(user.id, info.login_session_id, user.auth_version, user.role, expires,
                        user.data_scope, user.answer_style, token)

    async def recheck(self, identity):
        if identity.expires_at <= datetime.now(timezone.utc):
            raise IdentityError()
        response = await self.request("POST", "/api/v1/auth/recheck", identity.token,
                                      json={"auth_version": identity.auth_version})
        try:
            data = response.json()
            if data.get("valid") is not True or type(data.get("auth_version")) is not int or data["auth_version"] != identity.auth_version:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise IdentityError(503, "AUTH_SERVICE_UNAVAILABLE", "身份响应异常，请稍后重试。") from None

    async def read_action_proposal(self, identity, proposal_id):
        response = await self.request('GET', f'/api/v1/agent-actions/{proposal_id}', identity.token)
        try:
            data = response.json()
            if (data.get('proposal_id') != proposal_id or
                    data.get('status') not in {'PREVIEWED', 'SUCCEEDED', 'REJECTED', 'EXPIRED', 'FAILED'}):
                raise ValueError()
            return data
        except (ValueError, TypeError, AttributeError):
            raise IdentityError(503, 'ACTION_STATUS_INVALID', '业务操作状态暂时无法核对。') from None


COMPANY_TOOLS = {"search_transport_tasks", "get_transport_task", "find_shipments_with_destination_changes"}


def allowed_tool(identity, name):
    return identity is None or not identity.own_only or name not in COMPANY_TOOLS


class IdentityGuard:
    """One bounded online-check budget shared by a turn and its SSE output."""
    def __init__(self, client, maximum=256, seconds=120):
        self.client, self.remaining = client, maximum
        self.deadline = monotonic() + seconds

    async def recheck(self, identity):
        remaining = self.deadline - monotonic()
        if self.remaining <= 0 or remaining <= 0:
            raise IdentityError(503, "AUTH_SERVICE_UNAVAILABLE", "身份校验预算已用完，请重新查询。")
        self.remaining -= 1
        import asyncio
        try:
            async with asyncio.timeout(remaining):
                await self.client.recheck(identity)
        except TimeoutError:
            raise IdentityError(503, "AUTH_SERVICE_UNAVAILABLE", "身份校验超时，请重新查询。") from None
