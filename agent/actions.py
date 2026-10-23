"""Structured proposal-only tools for the web Agent; they never call a write API."""
from typing import Annotated
from uuid import uuid4

from langchain_core.tools import tool
from langgraph.runtime import get_runtime
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from be_client import BEClientError, get_order, get_shipment, get_stations
from state import TurnContext


class OrderChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    quantity: int | None = Field(default=None, gt=0, strict=True)
    sender_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    sender_address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    recipient_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    recipient_address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    earliest_handover_at: AwareDatetime | None = None
    latest_delivery_at: AwareDatetime | None = None
    sender_province_id: int | None = Field(default=None, gt=0, strict=True)
    sender_city_id: int | None = Field(default=None, gt=0, strict=True)
    sender_district_id: int | None = Field(default=None, gt=0, strict=True)
    recipient_province_id: int | None = Field(default=None, gt=0, strict=True)
    recipient_city_id: int | None = Field(default=None, gt=0, strict=True)
    recipient_district_id: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode='after')
    def validate_update(self):
        supplied = self.model_fields_set
        if not supplied:
            raise ValueError('至少提供一个需要修改的订单字段')
        nullable = {'earliest_handover_at', 'latest_delivery_at', 'sender_province_id',
                    'sender_city_id', 'sender_district_id', 'recipient_province_id',
                    'recipient_city_id', 'recipient_district_id'}
        for field in supplied - nullable:
            if getattr(self, field) is None:
                raise ValueError('订单字段不能设置为空')
        for side in ('sender', 'recipient'):
            region = {f'{side}_{level}_id' for level in ('province', 'city', 'district')}
            if supplied & region and not region.issubset(supplied):
                raise ValueError('修改地址区域时必须提供省、市、区县字段；无下级区域时显式传 null')
        if (self.earliest_handover_at is not None and self.latest_delivery_at is not None and
                self.earliest_handover_at > self.latest_delivery_at):
            raise ValueError('最早交运时间不能晚于最晚送达时间')
        return self


class OrderActionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    order_id: int = Field(gt=0, strict=True)
    changes: OrderChanges


class DestinationActionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shipment_id: int = Field(gt=0, strict=True)
    destination_station_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def resolve_delivery_station_name(name: str, stations: list[dict]) -> tuple[dict | None, list[dict]]:
    """Resolve a user-facing station name only when it identifies one enabled delivery station."""
    key = ''.join(name.split()).casefold()
    exact_all = [station for station in stations
                 if isinstance(station.get('name'), str)
                 and ''.join(station['name'].split()).casefold() == key]
    eligible = [station for station in stations
                if station.get('enabled') is True and station.get('allows_delivery') is True
                and isinstance(station.get('name'), str)]
    exact = [station for station in eligible if ''.join(station['name'].split()).casefold() == key]
    if len(exact_all) == 1 and len(exact) == 1:
        return exact[0], []
    if exact_all:
        return None, exact
    # Allow a short city/station prefix only when it still points to one station.
    partial = [station for station in eligible
               if ''.join(station['name'].split()).casefold().startswith(key)]
    if len(partial) == 1:
        return partial[0], []
    return None, partial


def build_action_tools(base_url: str):
    @tool(args_schema=OrderActionInput)
    async def propose_order_update(order_id: int, changes: dict) -> dict:
        """为已明确的本人订单准备修改提案；只读核对当前数据并生成候选，不执行修改。必须先用完整订单号或 ID 唯一定位订单，changes 只填用户明确要求修改的字段。"""
        context = get_runtime(TurnContext).context
        identity = context.identity
        if not context.allow_actions or identity is None or identity.role == 'developer':
            return {'status': 'unavailable', 'message': '当前会话只支持查询。'}
        if context.action_candidate_claimed or context.action_candidates:
            return {'status': 'unavailable', 'message': '每条消息只能准备一个业务修改提案。'}
        context.action_candidate_claimed = True
        try:
            current = (await get_order(base_url, order_id, context))['data']
        except (BEClientError, ValueError):
            context.action_candidate_claimed = False
            return {'status': 'unavailable', 'message': '当前账号范围内无法确认该订单，未生成修改提案。'}
        if current.get('status') != 'PENDING_SHIPMENT':
            context.action_candidate_claimed = False
            return {'status': 'unavailable', 'message': '订单当前状态不允许修改，未生成提案。'}
        candidate = {
            'candidate_id': str(uuid4()), 'agent_session_id': context.agent_session_id,
            'message_id': context.message_id, 'action_type': 'order.update',
            'subject_type': 'order', 'subject_id': str(order_id),
            'payload': OrderChanges.model_validate(changes).model_dump(mode='json', exclude_unset=True),
        }
        context.action_candidates.append(candidate)
        return {'status': 'candidate_created',
                'subject': {'type': 'order', 'id': str(order_id), 'number': current.get('order_no')},
                'fields': list(candidate['payload'])}

    @tool(args_schema=DestinationActionInput)
    async def propose_shipment_destination_update(
        shipment_id: int, destination_station_name: str, reason: str,
    ) -> dict:
        """为明确的运单准备目的站更正提案；仅 ops/admin 可用。按用户给出的站点名称查询 BE 站点目录并自动解析唯一可用的派送站，不要要求用户提供站点 ID；同名或名称不明确时先请用户选择。先只读核对当前目的站，再生成提案；不执行修改。更正原因必须来自用户明确表达。"""
        context = get_runtime(TurnContext).context
        identity = context.identity
        if (not context.allow_actions or identity is None or identity.role not in ('ops', 'admin')):
            return {'status': 'unavailable', 'message': '当前账号不能更正运单目的站。'}
        if context.action_candidate_claimed or context.action_candidates:
            return {'status': 'unavailable', 'message': '每条消息只能准备一个业务修改提案。'}
        context.action_candidate_claimed = True
        try:
            current = (await get_shipment(base_url, shipment_id, context))['data']
            stations = (await get_stations(base_url, context))['data']
        except (BEClientError, ValueError):
            context.action_candidate_claimed = False
            return {'status': 'unavailable', 'message': '无法读取当前运单或站点目录，未生成目的站更正提案。'}
        destination, matches = resolve_delivery_station_name(destination_station_name, stations)
        if destination is None:
            context.action_candidate_claimed = False
            if matches:
                names = '、'.join(f"{item['name']}（{item['code']}）" for item in matches[:8])
                return {'status': 'needs_clarification',
                        'message': f'“{destination_station_name}”对应多个可用派送站，请选择：{names}。不需要提供站点 ID。'}
            return {'status': 'unavailable',
                    'message': f'站点目录中没有找到唯一的可用派送站“{destination_station_name}”，未生成提案。'}
        current_station = current.get('destination_station_id')
        if not current_station:
            context.action_candidate_claimed = False
            return {'status': 'unavailable', 'message': '无法确认运单当前目的站，未生成提案。'}
        candidate = {
            'candidate_id': str(uuid4()), 'agent_session_id': context.agent_session_id,
            'message_id': context.message_id, 'action_type': 'shipment.destination.update',
            'subject_type': 'shipment', 'subject_id': str(shipment_id),
            'payload': {'expected_destination_station_id': int(current_station),
                        'destination_station_id': int(destination['id']), 'reason': reason.strip()},
        }
        context.action_candidates.append(candidate)
        return {'status': 'candidate_created',
                'subject': {'type': 'shipment', 'id': str(shipment_id), 'number': current.get('shipment_no')},
                'current_destination_station_id': str(current_station),
                'destination_station': {'name': destination['name'], 'code': destination['code']}}

    return [propose_order_update, propose_shipment_destination_update]
