"""Initialize the existing domestic demo network, preserving manual configuration."""
import argparse
import hashlib
import json
from collections import deque
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select, func
from db import SessionLocal
from models import Station, StationServiceArea, TransportRoute, TransportLine, TransportLineLeg
from lines.schemas import LineCreateRequest, LineUpdateRequest
from lines.service import write_line, line_body

REGIONS = {
    'NORTH': 'BEIJING TIANJIN SHIJIAZHUANG TAIYUAN HOHHOT BAOTOU',
    'NORTHEAST': 'SHENYANG DALIAN CHANGCHUN HARBIN',
    'EAST': 'SHANGHAI NANJING WUXI SUZHOU HANGZHOU NINGBO WENZHOU HEFEI JINAN QINGDAO FUZHOU XIAMEN NANCHANG',
    'CENTRAL': 'ZHENGZHOU WUHAN CHANGSHA',
    'SOUTH': 'GUANGZHOU SHENZHEN DONGGUAN FOSHAN ZHUHAI NANNING HAIKOU SANYA',
    'SOUTHWEST': 'CHENGDU CHONGQING GUIYANG KUNMING LHASA',
    'NORTHWEST': 'XIAN LANZHOU YINCHUAN XINING URUMQI',
}
ZONE = {code: zone for zone, codes in REGIONS.items() for code in codes.split()}


def defaults(a, b, provinces):
    # Explicit illustrative minutes; not road measurements or carrier commitments.
    if 'URUMQI' in (a.code, b.code) or 'LHASA' in (a.code, b.code):
        return 1440
    if provinces[a.id] == provinces[b.id]:
        return 180
    return 360 if ZONE[a.code] == ZONE[b.code] else 1080


def shortest_paths(edges, start, end, limit):
    def bfs(blocked=None):
        queue = deque([(start, [start], [])])
        seen = {start}
        while queue:
            current, nodes, timing = queue.popleft()
            for target, duration in edges.get(current, []):
                if target in seen or (current, target) == blocked:
                    continue
                next_nodes, next_timing = nodes + [target], timing + [duration]
                if target == end:
                    return next_nodes, next_timing
                seen.add(target)
                queue.append((target, next_nodes, next_timing))
        return None
    first = bfs()
    if first is None:
        return []
    found = [first]
    if limit > 1:
        alternatives = []
        for pair in zip(first[0], first[0][1:]):
            value = bfs(pair)
            if value is not None and value[0] != first[0]:
                alternatives.append(value)
        if alternatives:
            found.append(min(alternatives, key=lambda value: (len(value[1]), sum(value[1]), value[0])))
    return found


KNOWN = set()


def signature(definition):
    transfer = {v['station_id']:v['minutes'] for v in definition.get('transfer_overrides',[])}
    return tuple((a,b,definition['legs'][i]['travel_minutes'],0 if i == 0 else transfer.get(a))
                 for i,(a,b) in enumerate(zip(definition['station_ids'],definition['station_ids'][1:])))


def save(definition, report):
    if signature(definition) in KNOWN:
        report['unchanged'] += 1
        return
    with SessionLocal() as session:
        existing = session.scalar(select(TransportLine).where(TransportLine.code == definition['code']))
        if existing:
            report['unchanged'] += 1
            return
    with SessionLocal() as session:
        write_line(session, LineCreateRequest(**definition),
                   uuid5(NAMESPACE_URL, 'jingpo/national-demo-lines/v1/' + definition['code']))
    KNOWN.add(signature(definition))
    report['created'] += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default=None)
    args = parser.parse_args()
    report = dict(created=0, unchanged=0, completed_timings=0, skipped_disabled_pairs=0,
                  missing_pairs=[], scope='44 existing mainland city stations; illustrative timings')
    with SessionLocal() as session:
        stations = {s.id:s for s in session.scalars(select(Station).where(Station.code.in_(ZONE)))}
        provinces = dict(session.execute(select(StationServiceArea.station_id, StationServiceArea.province_id)
                                         .where(StationServiceArea.purpose == 'PICKUP')).tuples().all())
        routes = list(session.scalars(select(TransportRoute).where(
            TransportRoute.origin_station_id.in_(stations), TransportRoute.destination_station_id.in_(stations))))
        direct = { (r.origin_station_id,r.destination_station_id):r for r in routes }
        # Complete existing direct-line reference fields without changing real segments or manual values.
        timing_updates = []
        for route in routes:
            line = session.scalar(select(TransportLine).where(TransportLine.code == f'L_SEG_{route.id}'))
            if line is None:
                continue
            body = line_body(session, line)
            if body['legs'][0]['travel_minutes'] is None:
                timing_updates.append((line.id,line.version,defaults(stations[route.origin_station_id],
                    stations[route.destination_station_id],provinces)))
    for line_id, version, duration in timing_updates:
        with SessionLocal() as session:
            write_line(session, LineUpdateRequest(expected_version=version, legs=[dict(travel_minutes=duration)]),
                       uuid5(NAMESPACE_URL, f'jingpo/national-demo-timing/v1/{line_id}'), line_id)
        report['completed_timings'] += 1
    # Explicit representative inter-region hub corridors, with illustrative driving/transfer buffers.
    hubs = [('HARBIN','BEIJING',1440),('BEIJING','SHANGHAI',1440),
            ('SHANGHAI','SHENZHEN',1080),
            ('SHANGHAI','GUANGZHOU',1800),('GUANGZHOU','CHENGDU',1800),
            ('CHENGDU','XIAN',840),('XIAN','URUMQI',2880),('CHENGDU','LHASA',2880),
            ('SHANGHAI','FUZHOU',720),('NANJING','WUHAN',600),
            ('WUHAN','GUANGZHOU',900),('BEIJING','XIAN',1200),
            ('CHENGDU','KUNMING',840),('ZHENGZHOU','WUHAN',600)]
    by_code = {s.code:s for s in stations.values()}
    for source,target,duration in hubs:
        for a,b in [(by_code[source],by_code[target]),(by_code[target],by_code[source])]:
            if (a.id,b.id) in direct:
                continue
            save(dict(code=f'DEMO_{a.code}_{b.code}',name=f'{a.name} → {b.name}（演示干线）'[:100],
                station_ids=[a.id,b.id],legs=[dict(travel_minutes=duration)]),report)
    # Missing reverse segments are explicit demo corridors, not all-city direct links.
    for (start,end), route in direct.items():
        if (end,start) not in direct and route.enabled:
            a,b = stations[end],stations[start]
            code = 'DEMO_REV_' + hashlib.sha256(f'{end}:{start}'.encode()).hexdigest()[:16].upper()
            save(dict(code=code,name=f'{a.name} → {b.name}（演示直达）'[:100],station_ids=[end,start],
                      legs=[dict(travel_minutes=defaults(a,b,provinces))]), report)
    with SessionLocal() as session:
        edges = {}
        covered = set()
        for line in session.scalars(select(TransportLine).where(TransportLine.enabled.is_(True),
                    TransportLine.origin_station_id.in_(stations),TransportLine.destination_station_id.in_(stations))):
            body = line_body(session,line)
            if not body['usable']:
                continue
            covered.add((line.origin_station_id,line.destination_station_id))
            if len(body['legs']) == 1:
                leg = body['legs'][0]
                duration = leg['travel_minutes']
                if duration is not None:
                    edges.setdefault(line.origin_station_id,[]).append((line.destination_station_id,duration))
        for source in edges:
            edges[source] = sorted(set(edges[source]), key=lambda e: (e[1],stations[e[0]].code))
        disabled_pairs = {(r.origin_station_id,r.destination_station_id) for r in routes if not r.enabled}
    # Preserve one copy of identical generated station/time chains; keep audit history.
    with SessionLocal() as session:
        profiles = {}
        metadata = {}
        rows = session.execute(select(TransportLine.id,TransportLine.code,TransportLine.version,TransportLine.enabled,
            TransportRoute.origin_station_id,TransportRoute.destination_station_id,TransportLineLeg.position,
            func.coalesce(TransportLineLeg.travel_override_minutes,TransportRoute.travel_minutes),
            TransportLineLeg.origin_transfer_override_minutes).join(TransportLineLeg,TransportLineLeg.line_id==TransportLine.id)
            .join(TransportRoute,TransportRoute.id==TransportLineLeg.route_id).order_by(TransportLine.id,TransportLineLeg.position))
        for ident,code,version,enabled,a,b,position,travel,transfer in rows:
            metadata[ident] = code,version,enabled
            profiles.setdefault(ident,[]).append((a,b,travel,0 if position==0 else transfer))
    owners = {}
    report['deduplicated'] = 0
    for ident,parts in profiles.items():
        code,version,enabled = metadata[ident]
        profile = tuple(parts)
        KNOWN.add(profile)
        if not enabled:
            continue
        if profile in owners and code.startswith('DEMO_CHAIN_'):
            with SessionLocal() as session:
                write_line(session,LineUpdateRequest(expected_version=version,enabled=False),
                    uuid5(NAMESPACE_URL,f'jingpo/generated-demo-dedup/v1/{ident}'),ident)
            report['deduplicated'] += 1
        else:
            owners[profile] = ident
    for start in sorted(stations):
        for end in sorted(stations):
            if start == end:
                continue
            if (start,end) in disabled_pairs:
                report['skipped_disabled_pairs'] += 1
                continue
            same_region = ZONE[stations[start].code] == ZONE[stations[end].code]
            candidates = shortest_paths(edges,start,end,2 if same_region else 1)
            if not candidates:
                report['missing_pairs'].append([stations[start].code,stations[end].code])
                continue
            for nodes,timing in candidates:
                if len(timing) == 1:
                    continue
                if (start,end) in covered and not same_region:
                    continue
                signature = ':'.join(str(n) for n in nodes)
                code = 'DEMO_CHAIN_' + hashlib.sha256(signature.encode()).hexdigest()[:16].upper()
                names = ' → '.join(stations[n].name for n in nodes)
                save(dict(code=code,name=(names+'（演示中转）')[:100],station_ids=nodes,
                    legs=[dict(travel_minutes=t) for t in timing],
                    transfer_overrides=[dict(station_id=n,minutes=60) for n in nodes[1:-1]]),report)
    if args.report:
        with open(args.report,'w') as file:
            json.dump(report,file,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
