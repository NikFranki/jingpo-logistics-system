from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, exists
from sqlalchemy.orm import Session
from pydantic import BaseModel
from db import get_db
from models import Province, City, District
from errors import NetworkError

router = APIRouter(prefix='/api/v1', tags=['administrative-regions'])
DB = Annotated[Session, Depends(get_db)]
Id = Annotated[int, Query(gt=0)]


class RegionResponse(BaseModel):
    id: str
    code: str
    name: str
    level: str
    province_id: str | None = None
    city_id: str | None = None
    kind: str | None = None
    has_cities: bool = False
    has_districts: bool = False


def body(region, level):
    return dict(id=str(region.id), code=region.code, name=region.name, level=level,
                province_id=str(region.province_id) if level != 'PROVINCE' else None,
                city_id=str(region.city_id) if level == 'DISTRICT' and region.city_id else None,
                kind=region.kind if level == 'PROVINCE' else None)


def require(session, model, resource_id):
    value = session.get(model, resource_id)
    if value is None:
        raise NetworkError('REGION_NOT_FOUND', '所选行政区域不存在', 404)
    return value


@router.get('/provinces', response_model=list[RegionResponse])
def provinces(session: DB):
    city_exists = exists(select(City.id).where(City.province_id == Province.id, City.enabled.is_(True)))
    direct_exists = exists(select(District.id).where(District.province_id == Province.id,
                         District.city_id.is_(None), District.enabled.is_(True)))
    rows = session.execute(select(Province, city_exists, direct_exists).where(Province.enabled.is_(True))
                           .order_by(Province.sort_order, Province.code)).all()
    return [{**body(region, 'PROVINCE'), 'has_cities': cities, 'has_districts': districts}
            for region, cities, districts in rows]


@router.get('/cities', response_model=list[RegionResponse])
def cities(province_id: Id, session: DB):
    parent = require(session, Province, province_id)
    if not parent.enabled:
        return []
    district_exists = exists(select(District.id).where(District.city_id == City.id, District.enabled.is_(True)))
    rows = session.execute(select(City, district_exists).where(City.province_id == province_id,
                          City.enabled.is_(True)).order_by(City.sort_order, City.code)).all()
    return [{**body(region, 'CITY'), 'has_districts': children} for region, children in rows]


@router.get('/districts', response_model=list[RegionResponse])
def districts(province_id: Id, session: DB, city_id: Id | None = None):
    province = require(session, Province, province_id)
    if city_id is not None:
        city = require(session, City, city_id)
        if city.province_id != province_id:
            raise NetworkError('REGION_PARENT_MISMATCH', '所选城市不属于该省级区域', 422)
        if not city.enabled:
            return []
    if not province.enabled:
        return []
    rows = session.scalars(select(District).where(District.province_id == province_id,
                           District.city_id == city_id, District.enabled.is_(True))
                           .order_by(District.sort_order, District.code))
    return [body(region, 'DISTRICT') for region in rows]
