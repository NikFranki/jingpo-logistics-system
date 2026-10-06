"""Validate region selection and preserve the address names at write time."""
from typing import Annotated
from pydantic import BaseModel, Field
from sqlalchemy import select
from models import Province, City, District
from errors import NetworkError

SIDES = ('sender', 'recipient')
LEVELS = ('province', 'city', 'district')
REGION_FIELDS = tuple(f'{side}_{level}_id' for side in SIDES for level in LEVELS)
SNAPSHOT_FIELDS = tuple(f'{side}_{level}_name' for side in SIDES for level in LEVELS)


class AddressSelection(BaseModel):
    sender_province_id: Annotated[int | None, Field(gt=0, strict=True)] = None
    sender_city_id: Annotated[int | None, Field(gt=0, strict=True)] = None
    sender_district_id: Annotated[int | None, Field(gt=0, strict=True)] = None
    recipient_province_id: Annotated[int | None, Field(gt=0, strict=True)] = None
    recipient_city_id: Annotated[int | None, Field(gt=0, strict=True)] = None
    recipient_district_id: Annotated[int | None, Field(gt=0, strict=True)] = None


class AddressResponse(BaseModel):
    sender_province_id: str | None = None
    sender_city_id: str | None = None
    sender_district_id: str | None = None
    recipient_province_id: str | None = None
    recipient_city_id: str | None = None
    recipient_district_id: str | None = None
    sender_province_name: str | None = None
    sender_city_name: str | None = None
    sender_district_name: str | None = None
    recipient_province_name: str | None = None
    recipient_city_name: str | None = None
    recipient_district_name: str | None = None


def address_body(value):
    return {**{field: str(getattr(value, field)) if getattr(value, field) is not None else None for field in REGION_FIELDS},
            **{field: getattr(value, field) for field in SNAPSHOT_FIELDS}}


def request_body(request, create=False):
    body = request.model_dump(exclude_unset=not create)
    # Preserve old hashes exactly when new address fields were not supplied.
    for field in REGION_FIELDS:
        if field not in request.model_fields_set:
            body.pop(field, None)
    for field in ("earliest_handover_at", "latest_delivery_at"):
        if field not in request.model_fields_set:
            body.pop(field, None)
    return body


def validate_patch(body):
    for side in SIDES:
        fields = [f'{side}_{level}_id' for level in LEVELS]
        if any(field in body for field in fields):
            if not all(field in body for field in fields) or body[fields[0]] is None:
                raise ValueError(f'{side}: supply province_id, city_id and district_id together; only absent lower levels may be null')


def resolve_changes(session, body):
    changes = dict(body)
    for side in SIDES:
        keys = [f'{side}_{level}_id' for level in LEVELS]
        if not any(key in body for key in keys):
            continue
        ids = [body.get(key) for key in keys]
        if ids[0] is None:
            raise NetworkError('INVALID_ADDRESS_REGION', '填写行政区域时必须选择省级区域', 422)
        selected = []
        for model, resource_id in zip((Province, City, District), ids):
            row = session.get(model, resource_id) if resource_id is not None else None
            if resource_id is not None and (row is None or not row.enabled):
                raise NetworkError('INVALID_ADDRESS_REGION', '所选行政区域不存在或已停用', 422)
            selected.append(row)
        province, city, district = selected
        if city and city.province_id != province.id:
            raise NetworkError('REGION_PARENT_MISMATCH', '城市不属于所选省级区域', 422)
        if district and (district.province_id != province.id or district.city_id != ids[1]):
            raise NetworkError('REGION_PARENT_MISMATCH', '区县不属于所选省级区域或城市', 422)
        if district is None:
            if city:
                children = session.scalar(select(District.id).where(District.city_id == city.id, District.enabled.is_(True)).limit(1))
            else:
                children = session.scalar(select(City.id).where(City.province_id == province.id, City.enabled.is_(True)).limit(1))
                children = children or session.scalar(select(District.id).where(District.province_id == province.id,
                    District.city_id.is_(None), District.enabled.is_(True)).limit(1))
            if children:
                raise NetworkError('ADDRESS_REGION_INCOMPLETE', '请继续选择下级行政区域', 422)
        for key, resource_id in zip(keys, ids):
            changes[key] = resource_id
        for level, row in zip(LEVELS, selected):
            changes[f'{side}_{level}_name'] = row.name if row else None
    return changes
