"""Import the pinned domestic region dictionary: python -m regions.seed [--dry-run]."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select, text

from db import SessionLocal
from models import Province, City, District


def load_data():
    directory = Path(__file__).parent / 'data'
    manifest = json.loads((directory / 'manifest.json').read_text())
    raw = (directory / 'china-regions.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['sha256']['china-regions.json']:
        raise ValueError('Region dataset checksum mismatch')
    data = json.loads(raw)
    for level, rows in data.items():
        if len(rows) != manifest['counts'][level]:
            raise ValueError(f'Unexpected count: {level}')
        codes = [r['code'] for r in rows]
        if len(codes) != len(set(codes)):
            raise ValueError(f'Duplicate codes: {level}')
        if any(not re.fullmatch(r'[0-9]{6}', r['code']) or not r['name'].strip() for r in rows):
            raise ValueError(f'Invalid region: {level}')
    provinces = {r['code'] for r in data['provinces']}
    cities = {r['code']: r['province_code'] for r in data['cities']}
    for r in data['cities'] + data['districts']:
        if r['province_code'] not in provinces:
            raise ValueError('Unknown province')
        city = r.get('city_code')
        if city is not None and cities.get(city) != r['province_code']:
            raise ValueError('City/province mismatch')
    return data, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Validate and report, without writing')
    args = parser.parse_args()
    data, manifest = load_data()
    report = {'source_commit': manifest['commit'], 'dry_run': args.dry_run, 'tables': {}}
    with SessionLocal() as session, session.begin():
        # Share the write lock used by orders so addresses cannot see half an import.
        session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': 74219001})
        maps = {}
        for level, model in [('provinces', Province), ('cities', City), ('districts', District)]:
            existing = {r.code: r for r in session.scalars(select(model))}
            created = unchanged = 0
            for row in data[level]:
                values = {k: v for k, v in row.items() if not k.endswith('_code')}
                if level != 'provinces':
                    values['province_id'] = maps['provinces'][row['province_code']].id
                if level == 'districts':
                    values['city_id'] = maps['cities'][row['city_code']].id if row['city_code'] else None
                obj = existing.get(row['code'])
                if obj is not None:
                    if any(getattr(obj, k) != v for k, v in values.items()):
                        raise ValueError(f'Existing region differs: {level}/{row["code"]}; review before updating')
                    unchanged += 1
                else:
                    obj = model(**values)
                    if args.dry_run:
                        obj.id = -int(row['code'])
                    else:
                        session.add(obj)
                    created += 1
                existing[row['code']] = obj
            if not args.dry_run:
                session.flush()
            maps[level] = existing
            report['tables'][level] = {'created': created, 'unchanged': unchanged}
        if args.dry_run:
            session.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
