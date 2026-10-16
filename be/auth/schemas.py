from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr

from auth.security import normalize_email, normalize_username, validate_password


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


Username = Annotated[str, BeforeValidator(normalize_username)]
Email = Annotated[str, BeforeValidator(normalize_email)]
Password = Annotated[str, BeforeValidator(validate_password)]


class Register(Input):
    username: Username
    email: Email
    password: Password


class Login(Input):
    username: str = Field(min_length=1, max_length=40)
    password: SecretStr = Field(min_length=1, max_length=128)


class EmailRequest(Input):
    email: Email


class TokenRequest(Input):
    token: str = Field(min_length=20, max_length=256)


class ResetPassword(TokenRequest):
    password: Password


class ChangePassword(Input):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    password: Password


class CompleteProfile(Input):
    username: Username


class UserUpdate(Input):
    role: Literal['user', 'ops', 'developer'] | None = None
    enabled: bool | None = Field(default=None, strict=True)


class DeviceRequest(Input):
    device_code: str = Field(min_length=20, max_length=256)


class DeviceApproval(Input):
    user_code: str = Field(pattern=r'^[A-Z0-9]{8}$')
    approve: bool = Field(strict=True)


class Recheck(Input):
    auth_version: int = Field(gt=0)
