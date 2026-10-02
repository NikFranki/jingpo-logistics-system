"""Initialize explicit city-station demo coverage; python -m network.seed_service_areas."""
import argparse
import hashlib
import json
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import select

import business_time
from db import SessionLocal
from models import Station, Province, City, StationServiceArea, OperationLog
from network.coverage import ensure_scope_available, area_body

# This is an explicit demo operating policy, not inference from station names.
# SOC/hub stations and A/B/C/D are intentionally not assigned city-wide delivery.
CITY_COVERAGE = {
    'SHIJIAZHUANG': '130100', 'TAIYUAN': '140100', 'HOHHOT': '150100', 'BAOTOU': '150200',
    'SHENYANG': '210100', 'DALIAN': '210200', 'CHANGCHUN': '220100', 'HARBIN': '230100',
    'NANJING': '320100', 'WUXI': '320200', 'SUZHOU': '320500', 'HANGZHOU': '330100',
    'NINGBO': '330200', 'WENZHOU': '330300', 'HEFEI': '340100', 'JINAN': '370100',
    'QINGDAO': '370200', 'FUZHOU': '350100', 'XIAMEN': '350200', 'NANCHANG': '360100',
    'ZHENGZHOU': '410100', 'WUHAN': '420100', 'CHANGSHA': '430100', 'GUANGZHOU': '440100',
    'SHENZHEN': '440300', 'DONGGUAN': '441900', 'FOSHAN': '440600', 'ZHUHAI': '440400',
    'NANNING': '450100', 'HAIKOU': '460100', 'SANYA': '460200', 'CHENGDU': '510100',
    'GUIYANG': '520100', 'KUNMING': '530100', 'LHASA': '540100', 'XIAN': '610100',
    'LANZHOU': '620100', 'YINCHUAN': '640100', 'XINING': '630100', 'URUMQI': '650100',
}
PROVINCE_COVERAGE = {'BEIJING': '110000', 'TIANJIN': '120000',
                     'SHANGHAI': '310000', 'CHONGQING': '500000'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    report = {'dry_run': args.dry_run, 'created': [], 'unchanged': [], 'skipped_missing_station': []}
    with SessionLocal() as session, session.begin():
        now = business_time.begin_business_write(session)
        for level, mapping in [('CITY', CITY_COVERAGE), ('PROVINCE', PROVINCE_COVERAGE)]:
            model = City if level == 'CITY' else Province
            for station_code, region_code in mapping.items():
                station = session.scalar(select(Station).where(Station.code == station_code))
                if station is None:
                    report['skipped_missing_station'].append(station_code)
                    continue
                region = session.scalar(select(model).where(model.code == region_code))
                if region is None:
                    raise ValueError(f'Missing region {region_code}; initialize regions first')
                p = region.province_id if level == 'CITY' else region.id
                c = region.id if level == 'CITY' else None
                existing = session.scalar(select(StationServiceArea).where(
                    StationServiceArea.station_id == station.id, StationServiceArea.province_id == p,
                    StationServiceArea.city_id.is_not_distinct_from(c), StationServiceArea.district_id.is_(None)))
                if existing:
                    report['unchanged'].append(station_code)
                    continue  # Preserve manually disabled configuration as well.
                area = StationServiceArea(station_id=station.id, province_id=p, city_id=c, enabled=True)
                ensure_scope_available(session, area)
                report['created'].append(station_code)
                if args.dry_run:
                    continue
                session.add(area)
                session.flush()
                body = area_body(session, area)
                key = uuid5(NAMESPACE_URL, f'jingpo/demo-delivery-coverage/v1/{station_code}/{region_code}')
                if session.scalar(select(OperationLog.id).where(OperationLog.idempotency_key == key)):
                    raise ValueError(f'Initialization key already used: {station_code}')
                session.add(OperationLog(idempotency_key=key,
                    request_hash=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
                    action='INITIALIZE_STATION_SERVICE_AREA', resource_type='STATION', resource_id=station.id,
                    before_data=None, after_data=body, response_body=body, response_status=201, occurred_at=now))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
