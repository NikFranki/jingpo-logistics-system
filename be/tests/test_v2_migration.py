"""Replay V1 history through V2 using a disposable database created by this test.

Opt in with RUN_V2_MIGRATION_TESTS=1 and a DATABASE_URL naming a *_test database.
The PostgreSQL role needs CREATE DATABASE permission. Existing databases are not changed.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from shipments.schemas import ShipmentDetailResponse, ShipmentEventRequest
from shipments.service import process_shipment_event


V1 = "ccce65c68961"
HEAD = "d92f4b76e301"
STAGES = ["PENDING_PICKUP", "PICKED_UP", "AT_A", "IN_TRANSIT_AB", "AT_B",
          "IN_TRANSIT_BC", "AT_C", "OUT_FOR_DELIVERY", "SIGNED"]
EVENTS = ["SHIPMENT_CREATED", "PICKUP", "ENTER_A", "DEPART_AB", "ARRIVE_B",
          "DEPART_BC", "ARRIVE_C", "START_DELIVERY", "SIGN"]
NEW_STAGES = ["PENDING_PICKUP", "PICKED_UP", "AT_STATION", "IN_TRANSIT", "AT_STATION",
              "IN_TRANSIT", "AT_STATION", "OUT_FOR_DELIVERY", "SIGNED"]
NEW_EVENTS = ["SHIPMENT_CREATED", "PICKUP", "ARRIVE", "DEPART", "ARRIVE",
              "DEPART", "ARRIVE", "START_DELIVERY", "SIGN"]
BE_DIR = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv("RUN_V2_MIGRATION_TESTS") == "1", "opt-in migration rehearsal")
class V2MigrationTests(unittest.TestCase):
    def setUp(self):
        source = make_url(os.environ["DATABASE_URL"])
        if not source.database or not source.database.endswith("_test"):
            self.fail("DATABASE_URL must name an isolated *_test database")
        self.name = "jingpo_v2_migration_" + uuid4().hex[:12] + "_test"
        self.admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{self.name}"')
        self.addCleanup(self._drop_database)
        self.url = source.set(database=self.name)
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)
        self.run_migration(V1)

    def _drop_database(self):
        with self.admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{self.name}"')
        self.admin.dispose()

    def run_migration(self, revision, *, succeeds=True):
        env = {**os.environ, "DATABASE_URL": self.url.render_as_string(hide_password=False)}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=BE_DIR, env=env, capture_output=True, text=True, timeout=30,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def seed_history(self):
        base = datetime(2026, 9, 20, tzinfo=timezone.utc)
        self.arrival_keys = {}
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO simulation_settings (id) VALUES (1)"))
            stations = {code: conn.execute(text(
                "INSERT INTO stations (code, name) VALUES (:code, :name) RETURNING id"
            ), {"code": code, "name": code + " station"}).scalar_one() for code in "ABC"}
            routes = {code: conn.execute(text(
                "INSERT INTO transport_routes (code, origin_station_id, destination_station_id) "
                "VALUES (:code, :origin, :destination) RETURNING id"
            ), {"code": code, "origin": stations[code[0]], "destination": stations[code[1]]}).scalar_one()
                for code in ("AB", "BC")}
            self.station_a = stations["A"]
            self.shipments = {}
            for index, stage in enumerate(STAGES):
                order_id = conn.execute(text(
                    "INSERT INTO orders (product_name, quantity, sender_name, sender_address, "
                    "recipient_name, recipient_address, status) "
                    "VALUES ('V1 history', 1, 'Sender', 'AT_A', 'Recipient', 'Destination', :status) RETURNING id"
                ), {"status": "COMPLETED" if stage == "SIGNED" else "SHIPMENT_CREATED"}).scalar_one()
                scanned = None if index < 2 else stations["A" if index < 4 else "B" if index < 6 else "C"]
                shipment = conn.execute(text(
                    "INSERT INTO shipments (order_id, sender_address, recipient_address, stage, last_scanned_station_id) "
                    "VALUES (:order, 'AT_A', 'Destination', :stage, :station) RETURNING *"
                ), {"order": order_id, "stage": stage, "station": scanned}).mappings().one()
                shipment_id = shipment["id"]
                self.shipments[stage] = shipment_id
                tasks = {}
                for route, departure_index, arrival_index in (("AB", 3, 4), ("BC", 5, 6)):
                    if index < departure_index:
                        continue
                    arrived = index >= arrival_index
                    task_id = conn.execute(text(
                        "INSERT INTO transport_tasks (route_id, status, expected_arrival_at, departed_at, arrived_at) "
                        "VALUES (:route, :status, :expected, :departed, :arrived) RETURNING id"
                    ), {"route": routes[route], "status": "ARRIVED" if arrived else "IN_TRANSIT",
                        "expected": base + timedelta(days=1), "departed": base + timedelta(minutes=departure_index),
                        "arrived": base + timedelta(minutes=arrival_index) if arrived else None}).scalar_one()
                    tasks[route] = task_id
                    conn.execute(text(
                        "INSERT INTO task_shipments (task_id, shipment_id, released_at) VALUES (:task, :shipment, :released)"
                    ), {"task": task_id, "shipment": shipment_id,
                        "released": base + timedelta(minutes=arrival_index) if arrived else None})
                history = []
                for event_index, event_type in enumerate(EVENTS[:index + 1]):
                    event_station = (stations["A"] if event_index in (2, 3) else
                                     stations["B"] if event_index in (4, 5) else
                                     stations["C"] if event_index == 6 else None)
                    task_id = tasks.get("AB" if event_index in (3, 4) else "BC") if event_index in (3, 4, 5, 6) else None
                    key = uuid4()
                    action = {"PICKUP": "PICKUP_SHIPMENT", "ENTER_A": "ENTER_STATION",
                              "START_DELIVERY": "START_DELIVERY", "SIGN": "SIGN_SHIPMENT"}.get(event_type, "HISTORY_SAMPLE")
                    request_hash = hashlib.sha256(f"SHIPMENT_EVENT:{shipment_id}:{event_type}".encode()).hexdigest()
                    event_id = conn.execute(text(
                        "INSERT INTO operation_logs (idempotency_key, request_hash, action, resource_type, resource_id, "
                        "before_data, after_data, response_body, response_status, occurred_at) "
                        "VALUES (:key, :hash, :action, 'SHIPMENT', :shipment, NULL, "
                        "CAST(:after AS jsonb), '{}'::jsonb, 200, :time) RETURNING id"
                    ), {"key": key, "hash": request_hash, "action": action, "shipment": shipment_id,
                        "after": json.dumps({"stage": STAGES[event_index]}), "time": base + timedelta(minutes=event_index)}).scalar_one()
                    event = conn.execute(text(
                        "INSERT INTO tracking_events (shipment_id, event_type, occurred_at, station_id, task_id, operation_id) "
                        "VALUES (:shipment, :event, :time, :station, :task, :operation) RETURNING *"
                    ), {"shipment": shipment_id, "event": event_type, "time": base + timedelta(minutes=event_index),
                        "station": event_station, "task": task_id, "operation": event_id}).mappings().one()
                    history.append({"id": str(event["id"]), "event_type": event_type,
                                    "occurred_at": event["occurred_at"].isoformat(),
                                    "station_id": str(event_station) if event_station else None,
                                    "task_id": str(task_id) if task_id else None})
                    response = {"id": str(shipment_id), "shipment_no": shipment["shipment_no"],
                                "order_id": str(order_id), "sender_address": "AT_A", "recipient_address": "Destination",
                                "region_code": "Z", "stage": STAGES[event_index],
                                "last_scanned_station_id": str(event_station or scanned) if event_index >= 2 else None,
                                "created_at": shipment["created_at"].isoformat(), "updated_at": shipment["updated_at"].isoformat(),
                                "tracking_events": list(reversed(history)),
                                "allowed_actions": [{"action": "ENTER_A", "enabled": event_index == 1,
                                                     "reason_code": None if event_index == 1 else "INVALID_STAGE",
                                                     "reason": None if event_index == 1 else "Entering station A requires PICKED_UP stage"}]}
                    conn.execute(text("UPDATE operation_logs SET response_body = CAST(:body AS jsonb) WHERE id = :id"),
                                 {"id": event_id, "body": json.dumps(response)})
                    if event_type == "ENTER_A":
                        self.arrival_keys[shipment_id] = key

    def snapshot(self):
        with self.engine.connect() as conn:
            return {table: list(conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings())
                    for table in ("shipments", "tracking_events", "transport_tasks", "task_shipments", "operation_logs")}

    def test_history_mapping_preserves_identity_and_old_key_replay(self):
        self.seed_history()
        before = self.snapshot()
        # A V1 stage/scan conflict must stop before changing constraints or history.
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE shipments SET last_scanned_station_id = "
                              "(SELECT id FROM stations WHERE code='B') WHERE id=:id"),
                         {"id": self.shipments["AT_A"]})
        result = self.run_migration("head", succeeds=False)
        self.assertIn("Invalid V1 shipment location", result.stderr)
        with self.engine.begin() as conn:
            self.assertEqual(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one(), V1)
            conn.execute(text("UPDATE shipments SET last_scanned_station_id=:station WHERE id=:id"),
                         {"station": self.station_a, "id": self.shipments["AT_A"]})
        self.assertEqual(self.snapshot(), before)
        self.run_migration("head")
        after = self.snapshot()
        for table in ("transport_tasks", "task_shipments"):
            for old, new in zip(before[table], after[table]):
                self.assertEqual({key: new[key] for key in old}, dict(old))
        self.assertEqual([row["stage"] for row in after["shipments"]], NEW_STAGES)
        for old, new in zip(before["tracking_events"], after["tracking_events"]):
            expected = dict(old)
            expected["event_type"] = NEW_EVENTS[EVENTS.index(old["event_type"])]
            self.assertEqual(dict(new), expected)
        for old, new in zip(before["operation_logs"], after["operation_logs"]):
            for field in ("id", "idempotency_key", "action", "resource_id", "occurred_at", "created_at"):
                self.assertEqual(new[field], old[field])
            payload = ShipmentDetailResponse.model_validate(new["response_body"])
            self.assertEqual(payload.sender_address, "AT_A")
            self.assertEqual(payload.allowed_actions[0].action, "ARRIVE")
        shipment_id = self.shipments["AT_A"]
        with Session(self.engine) as session:
            replay = process_shipment_event(session, shipment_id,
                ShipmentEventRequest(event_type="ARRIVE", station_id=str(self.station_a)),
                self.arrival_keys[shipment_id])
        self.assertEqual(replay["stage"], "AT_STATION")
        self.assertEqual(replay["tracking_events"][0]["event_type"], "ARRIVE")
        self.assertEqual(self.snapshot(), after)

    def test_empty_database_upgrades(self):
        self.run_migration("head")
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one(), HEAD)
            self.assertEqual(conn.execute(text("SELECT count(*) FROM shipments")).scalar_one(), 0)


if __name__ == "__main__":
    unittest.main()
