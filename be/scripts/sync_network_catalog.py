#!/usr/bin/env python3
"""Safely upsert regions, stations, service areas, routes, lines and timetables.

Source: SOURCE_DATABASE_URL, falling back to DATABASE_URL/.env
Target: TARGET_DATABASE_URL
Dry-run by default. Pass --apply to write. Existing destination rows are never deleted.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, insert, select, update
from sqlalchemy.engine import make_url

load_dotenv()

# (table, natural key columns, foreign keys to remap)
STEPS = [
    ("provinces", ("code",), {}),
    ("cities", ("code",), {"province_id": ("provinces", "province")}),
    ("districts", ("code",), {
        "province_id": ("provinces", "province"),
        "city_id": ("cities", "city"),
    }),
    ("stations", ("code",), {}),
    ("station_service_areas", ("purpose", "province_id", "city_id", "district_id"), {
        "station_id": ("stations", "station"),
        "province_id": ("provinces", "province"),
        "city_id": ("cities", "city"),
        "district_id": ("districts", "district"),
    }),
    ("transport_routes", ("code",), {
        "origin_station_id": ("stations", "station"),
        "destination_station_id": ("stations", "station"),
    }),
    ("transport_lines", ("code",), {
        "origin_station_id": ("stations", "station"),
        "destination_station_id": ("stations", "station"),
    }),
    ("transport_line_legs", ("line_id", "position"), {
        "line_id": ("transport_lines", "line"),
        "route_id": ("transport_routes", "route"),
    }),
    ("line_services", ("line_id", "code"), {
        "line_id": ("transport_lines", "line"),
    }),
    ("line_service_stops", ("service_id", "position"), {
        "service_id": ("line_services", "service"),
        "station_id": ("stations", "station"),
    }),
    ("scheduled_trips", ("service_id", "service_date", "service_version"), {
        "service_id": ("line_services", "service"),
        "line_id": ("transport_lines", "line"),
    }),
]


def normalized_identity(raw: str) -> tuple:
    u = make_url(raw)
    return (u.drivername, u.host, u.port, u.database)


def sqlalchemy_url(raw: str) -> str:
    """Use this project's psycopg 3 driver for plain PostgreSQL URLs."""
    url = make_url(raw)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def remap_json_snapshot(value, station_ids, route_ids):
    if isinstance(value, list):
        return [remap_json_snapshot(x, station_ids, route_ids) for x in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "station_id" and item is not None:
                item = str(station_ids[int(item)])
            elif key == "outbound_route_id" and item is not None:
                item = str(route_ids[int(item)])
            else:
                item = remap_json_snapshot(item, station_ids, route_ids)
            result[key] = item
        return result
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes; otherwise only preview")
    args = parser.parse_args()

    source_url = os.getenv("SOURCE_DATABASE_URL") or os.getenv("DATABASE_URL")
    target_url = os.getenv("TARGET_DATABASE_URL")
    if not source_url or not target_url:
        parser.error("请设置 SOURCE_DATABASE_URL（可从本地 .env 的 DATABASE_URL 读取）和 TARGET_DATABASE_URL")
    if normalized_identity(source_url) == normalized_identity(target_url):
        parser.error("源库与目标库看起来相同，已停止，避免误操作")

    source = create_engine(sqlalchemy_url(source_url), pool_pre_ping=True)
    target = create_engine(sqlalchemy_url(target_url), pool_pre_ping=True)
    src_meta, dst_meta = MetaData(), MetaData()
    try:
        src_meta.reflect(bind=source)
        dst_meta.reflect(bind=target)
        for table, _, _ in STEPS:
            if table not in src_meta.tables or table not in dst_meta.tables:
                parser.error(f"源库或目标库缺少表 {table}；请先把目标库迁移到与本地一致的 Alembic 版本")

        print("执行模式：" + ("写入" if args.apply else "仅预览；只读目标数据库"))
        mappings = defaultdict(dict)
        with source.connect() as src, target.begin() as dst:
            if not args.apply:
                dst.exec_driver_sql("SET TRANSACTION READ ONLY")
            synthetic_id = -1
            for table_name, natural_key, foreign_keys in STEPS:
                st, dt = src_meta.tables[table_name], dst_meta.tables[table_name]
                src_rows = [dict(row) for row in src.execute(select(st)).mappings()]
                existing = [dict(row) for row in dst.execute(select(dt)).mappings()]
                lookup = {tuple(row[k] for k in natural_key): row for row in existing}
                added = updated = unchanged = 0
                for source_row in src_rows:
                    row = dict(source_row)
                    old_id = row.pop("id")
                    for column, (parent, map_name) in foreign_keys.items():
                        parent_id = row[column]
                        if parent_id is None:
                            continue
                        mapped = mappings[map_name].get(parent_id)
                        if mapped is None:
                            raise RuntimeError(f"{table_name}.{column}={parent_id} 找不到已映射的 {parent} 记录")
                        row[column] = mapped
                    if table_name == "scheduled_trips":
                        row["stops_snapshot"] = remap_json_snapshot(
                            row["stops_snapshot"], mappings["station"], mappings["route"])
                    key = tuple(row[k] for k in natural_key)
                    match = lookup.get(key)
                    if match:
                        target_id = match["id"]
                        changes = {
                            name: value for name, value in row.items()
                            if name != "created_at" and match.get(name) != value
                        }
                        if changes:
                            updated += 1
                            if args.apply:
                                dst.execute(update(dt).where(dt.c.id == target_id).values(**changes))
                        else:
                            unchanged += 1
                    else:
                        added += 1
                        if args.apply:
                            row.pop("created_at", None)  # use target default
                            target_id = dst.execute(insert(dt).values(**row).returning(dt.c.id)).scalar_one()
                        else:
                            target_id = synthetic_id
                            synthetic_id -= 1
                    lookup[key] = {**row, "id": target_id}
                    if table_name == "stations":
                        mappings["station"][old_id] = target_id
                    elif table_name == "transport_routes":
                        mappings["route"][old_id] = target_id
                    elif table_name == "transport_lines":
                        mappings["line"][old_id] = target_id
                    elif table_name == "line_services":
                        mappings["service"][old_id] = target_id
                    elif table_name == "provinces":
                        mappings["province"][old_id] = target_id
                    elif table_name == "cities":
                        mappings["city"][old_id] = target_id
                    elif table_name == "districts":
                        mappings["district"][old_id] = target_id
                print(f"{table_name}: 源库 {len(src_rows)} 条；目标库原有 {len(existing)} 条；新增 {added}、需更新 {updated}、相同跳过 {unchanged}", flush=True)
            if not args.apply:
                print("\n预览结束。此模式没有写入 Neon；确认目标库正确且完成备份后，使用同样命令追加 --apply 执行。")
                return 0
        print("\n同步完成。未删除目标库数据；同自然键记录已更新，缺少的记录已新增。")
        return 0
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        raise
