"""Explicit domestic demo lines and durations, not carrier timetables."""
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select
from db import SessionLocal
from models import TransportRoute, TransportLine
from lines.schemas import LineCreateRequest, LineUpdateRequest
from lines.service import write_line, line_body

# Minutes below are explicit illustrative parameters, not measured carrier SLAs.
DEMO = [
    ('GZ_SZ', '广州—深圳直达（演示）', ['GUANGZHOU_SHENZHEN'], [180], 60),
    ('GZ_DG', '广州—东莞直达（演示）', ['GUANGZHOU_DONGGUAN'], [120], 60),
    ('DG_SZ', '东莞—深圳直达（演示）', ['DONGGUAN_SHENZHEN'], [90], 60),
    ('SOC_SH_SZ', '上海虹桥 SOC → 深圳燕岗 SOC', ['ROUTE_SH_SZ'], [1440], 60),
    ('SOC_SZ_SH', '深圳燕岗 SOC → 上海虹桥 SOC', ['ROUTE_SZ_SH'], [1440], 60),
    ('SOC_SZ_DG', '深圳燕岗 SOC → 东莞 SOC', ['ROUTE_SZ-DG'], [120], 60),
    ('SOC_DG_SH', '东莞 SOC → 上海虹桥 SOC', ['ROUTE_DG_SH'], [1440], 60),
    ('LAST_MILE_C_D', '末端配送站 → 一个站点', ['ROUTE_C_D'], [60], 60),
    ('DEMO_GZ_SZ_VIA_DG', '广州—深圳经东莞（演示）', ['GUANGZHOU_DONGGUAN','DONGGUAN_SHENZHEN'], [120,90], 60),
    ('DEMO_SH_NJ_VIA_SZ_WX', '上海—南京经苏州无锡（演示）', ['SHANGHAI_SUZHOU','SUZHOU_WUXI','WUXI_NANJING'], [120,90,120], 60),
    ('DEMO_SJZ_TJ_VIA_BJ', '石家庄—天津经北京（演示）', ['SHIJIAZHUANG_BEIJING','BEIJING_TIANJIN'], [240,120], 90),
]


def main():
    for code, name, route_codes, durations, transfer in DEMO:
        with SessionLocal() as session:
            routes = [session.scalar(select(TransportRoute).where(TransportRoute.code == c)) for c in route_codes]
            if any(r is None for r in routes):
                print('skipped missing segments:', code)
                continue
            stations = [routes[0].origin_station_id] + [r.destination_station_id for r in routes]
            actual_code = f'L_SEG_{routes[0].id}' if len(routes) == 1 else code
            line = session.scalar(select(TransportLine).where(TransportLine.code == actual_code))
            definition = dict(name=name, station_ids=stations,
                legs=[dict(travel_minutes=v) for v in durations],
                transfer_overrides=[dict(station_id=s, minutes=transfer) for s in stations[1:-1]])
            line_id = line.id if line else None
            version = line.version if line else None
            if line:
                body = line_body(session, line)
                if body['name'] == name and [int(i) for i in body['station_ids']] == stations and \
                   [v['travel_minutes'] for v in body['legs']] == durations and [dict(station_id=int(t['station_id']),minutes=t['minutes']) for t in body['transfer_overrides']] == definition['transfer_overrides']:
                    print('unchanged:', actual_code)
                    continue
                # Review existing manual edits instead of silently replacing them on reruns.
                if any(v['travel_minutes'] is not None for v in body['legs']):
                    print('skipped existing timing:', actual_code)
                    continue
        request = LineUpdateRequest(expected_version=version, **definition) if line_id else LineCreateRequest(code=actual_code, **definition)
        key = uuid5(NAMESPACE_URL, f'jingpo/demo-transport-lines/v1/{actual_code}')
        with SessionLocal() as session:
            result = write_line(session, request, key, line_id)
        print('configured:', result['code'])


if __name__ == '__main__':
    main()
