from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: UUID
    agent_session_id: UUID
    message_id: UUID
    action_type: Literal['order.update', 'shipment.destination.update']
    subject_id: str = Field(pattern=r'^[1-9][0-9]{0,18}$')
    payload: dict[str, Any]

    @field_validator('subject_id')
    @classmethod
    def valid_bigint(cls, value):
        if int(value) > 9223372036854775807:
            raise ValueError('subject_id is outside the supported range')
        return value


class AgentWritePolicyUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled_action_types: list[Literal['order.update', 'shipment.destination.update']] = Field(max_length=8)


class ActionExecuteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['approved', 'automatic']


class ActionRejectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason_code: Literal['USER_CANCELLED']
