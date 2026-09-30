"""PostgreSQL integration checks for the V2 shipment state machine.

Run with DATABASE_URL pointing to an isolated, migrated *_test database.
"""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from db import SessionLocal
from errors import InvalidShipmentStateError, InvalidTaskShipmentError
from models import OperationLog, Order, Shipment, SimulationSettings, Station, TaskShipment, TrackingEvent, TransportTask
from orders.schemas import OrderCreateRequest
from orders.service import create_order
from shipments.schemas import ShipmentAddressUpdateRequest, ShipmentEventRequest
from shipments.service import (
    build_shipment_response_body,
    create_shipment,
    get_shipment,
    process_shipment_event,
    update_shipment_address,
)
from transport.schemas import TransportTaskCreateRequest
from transport.service import (
    arrive_transport_task,
    create_transport_task,
    depart_transport_task,
    list_candidate_shipments,
    calculate_task_delay,
)


@unittest.skipUnless((make_url(os.environ["DATABASE_URL"]).database or "").endswith("_test") if os.getenv("DATABASE_URL") else False, "requires an isolated *_test PostgreSQL database")
class V2FlowTests(unittest.TestCase):
    @staticmethod
    def call(function, *args):
        with SessionLocal() as session:
            return function(session, *args)

    def test_concurrent_task_creation_occupies_shipment_once(self):
        order = self.call(
            create_order,
            OrderCreateRequest(
                product_name="Concurrent sample", quantity=1,
                sender_name="Sender", sender_address="Sender street",
                recipient_name="Recipient", recipient_address="Recipient street",
            ),
            uuid4(),
        )
        shipment, _ = self.call(create_shipment, int(order["id"]), uuid4())
        shipment_id = int(shipment["id"])
        with SessionLocal() as session:
            station_a = session.scalar(select(Station.id).where(Station.code == "A"))
            clock = session.get(SimulationSettings, 1).current_time
        self.call(process_shipment_event, shipment_id, ShipmentEventRequest(event_type="PICKUP"), uuid4())
        self.call(process_shipment_event, shipment_id, ShipmentEventRequest(event_type="ARRIVE", station_id=str(station_a)), uuid4())

        def create_one(_):
            try:
                return self.call(
                    create_transport_task,
                    TransportTaskCreateRequest(
                        route_code="AB", expected_arrival_at=clock + timedelta(days=1),
                        shipment_ids=[shipment_id],
                    ),
                    uuid4(),
                )
            except InvalidTaskShipmentError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create_one, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        with SessionLocal() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(TaskShipment).where(
                    TaskShipment.shipment_id == shipment_id,
                    TaskShipment.released_at.is_(None),
                )),
                1,
            )
            self.assertEqual(session.get(Shipment, shipment_id).stage, "AT_STATION")

    def test_ab_delay_uses_task_time(self):
        expected = self._clock() + timedelta(hours=1)
        task = TransportTask(status="IN_TRANSIT", expected_arrival_at=expected)
        task.departed_at = expected - timedelta(hours=1)
        self.assertEqual(calculate_task_delay(task, "AB", expected + timedelta(minutes=9)), ("OVERDUE", 9))
        self.assertEqual(calculate_task_delay(task, "BC", expected + timedelta(minutes=9)), ("NOT_APPLICABLE", None))
        task.status = "ARRIVED"
        task.arrived_at = expected + timedelta(minutes=7)
        self.assertEqual(calculate_task_delay(task, "AB", expected + timedelta(hours=3)), ("LATE_ARRIVAL", 7))

    @staticmethod
    def _clock():
        with SessionLocal() as session:
            return session.get(SimulationSettings, 1).current_time

    def test_arrival_rolls_back_every_shipment_on_event_failure(self):
        with SessionLocal() as session:
            station_a = session.scalar(select(Station.id).where(Station.code == "A"))
            clock = session.get(SimulationSettings, 1).current_time
        shipment_ids = []
        for number in (1, 2):
            order = self.call(
                create_order,
                OrderCreateRequest(
                    product_name=f"Atomic sample {number}", quantity=1,
                    sender_name="Sender", sender_address="Sender street",
                    recipient_name="Recipient", recipient_address="Recipient street",
                ),
                uuid4(),
            )
            shipment, _ = self.call(create_shipment, int(order["id"]), uuid4())
            shipment_id = int(shipment["id"])
            self.call(process_shipment_event, shipment_id, ShipmentEventRequest(event_type="PICKUP"), uuid4())
            self.call(process_shipment_event, shipment_id, ShipmentEventRequest(event_type="ARRIVE", station_id=str(station_a)), uuid4())
            shipment_ids.append(shipment_id)

        task = self.call(
            create_transport_task,
            TransportTaskCreateRequest(
                route_code="AB", expected_arrival_at=clock + timedelta(days=1), shipment_ids=shipment_ids,
            ),
            uuid4(),
        )
        task_id = int(task["id"])
        self.call(depart_transport_task, task_id, uuid4())
        # A database failure on the second parcel must undo the first parcel's arrival too.
        with SessionLocal.begin() as session:
            session.execute(text(f"""
                CREATE OR REPLACE FUNCTION v2_test_reject_arrival() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.event_type = 'ARRIVE' AND NEW.shipment_id = {shipment_ids[1]}
                    THEN RAISE EXCEPTION 'synthetic arrival failure'; END IF;
                    RETURN NEW;
                END $$
            """))
            session.execute(text("CREATE TRIGGER v2_test_reject_arrival BEFORE INSERT ON tracking_events FOR EACH ROW EXECUTE FUNCTION v2_test_reject_arrival()"))
        failed_key = uuid4()
        try:
            with self.assertRaises(Exception):
                self.call(arrive_transport_task, task_id, failed_key)
        finally:
            with SessionLocal.begin() as session:
                session.execute(text("DROP TRIGGER v2_test_reject_arrival ON tracking_events"))
                session.execute(text("DROP FUNCTION v2_test_reject_arrival()"))

        with SessionLocal() as session:
            self.assertEqual(session.get(TransportTask, task_id).status, "IN_TRANSIT")
            self.assertIsNone(session.get(TransportTask, task_id).arrived_at)
            self.assertIsNone(session.scalar(select(OperationLog).where(OperationLog.idempotency_key == failed_key)))
            for shipment_id in shipment_ids:
                self.assertEqual(session.get(Shipment, shipment_id).stage, "IN_TRANSIT")
                self.assertEqual(session.get(Shipment, shipment_id).last_scanned_station_id, station_a)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(TaskShipment).where(
                    TaskShipment.task_id == task_id, TaskShipment.released_at.is_(None),
                )),
                2,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(TrackingEvent).where(
                    TrackingEvent.task_id == task_id, TrackingEvent.event_type == "ARRIVE",
                )),
                0,
            )

    def test_full_flow_and_repeat_arrivals(self):
        order = self.call(
            create_order,
            OrderCreateRequest(
                product_name="V2 sample", quantity=1,
                sender_name="Sender", sender_address="Original sender",
                recipient_name="Recipient", recipient_address="Original recipient",
            ),
            uuid4(),
        )
        shipment, status = self.call(create_shipment, int(order["id"]), uuid4())
        self.assertEqual(status, 201)
        shipment_id = int(shipment["id"])

        shipment = self.call(
            update_shipment_address, shipment_id,
            ShipmentAddressUpdateRequest(recipient_address="Delivery correction"), uuid4(),
        )
        self.assertEqual(shipment["recipient_address"], "Delivery correction")
        with SessionLocal() as session:
            self.assertEqual(session.get(Order, int(order["id"])).recipient_address, "Original recipient")
            station_ids = {
                station.code: station.id for station in session.scalars(select(Station))
            }
            clock = session.get(SimulationSettings, 1).current_time

        with self.assertRaises(InvalidShipmentStateError):
            self.call(
                process_shipment_event, shipment_id,
                ShipmentEventRequest(event_type="ARRIVE", station_id=str(station_ids["A"])), uuid4(),
            )
        shipment = self.call(
            process_shipment_event, shipment_id,
            ShipmentEventRequest(event_type="PICKUP"), uuid4(),
        )
        self.assertIsNone(shipment["last_scanned_station_id"])
        with SessionLocal() as session:
            candidates, _ = list_candidate_shipments(session, "AB", 1, 100)
            self.assertNotIn(shipment_id, [candidate.id for candidate in candidates])
        with self.assertRaises(InvalidTaskShipmentError):
            self.call(create_transport_task, TransportTaskCreateRequest(
                route_code="AB", expected_arrival_at=clock + timedelta(days=1), shipment_ids=[shipment_id],
            ), uuid4())
        with self.assertRaises(InvalidShipmentStateError):
            self.call(
                process_shipment_event, shipment_id,
                ShipmentEventRequest(event_type="ARRIVE", station_id=str(station_ids["B"])), uuid4(),
            )
        arrival_key = uuid4()
        first_arrival = ShipmentEventRequest(event_type="ARRIVE", station_id=str(station_ids["A"]))
        shipment = self.call(
            process_shipment_event, shipment_id,
            first_arrival, arrival_key,
        )
        self.assertEqual(shipment["stage"], "AT_STATION")
        self.assertEqual(self.call(process_shipment_event, shipment_id, first_arrival, arrival_key), shipment)
        duplicate = self.call(process_shipment_event, shipment_id, first_arrival, uuid4())
        self.assertEqual(len(duplicate["tracking_events"]), 3)
        self.assertIsNone(duplicate["tracking_events"][0]["task_id"])

        for route_code, origin, destination in (("AB", "A", "B"), ("BC", "B", "C")):
            with SessionLocal() as session:
                candidates, _ = list_candidate_shipments(session, route_code, 1, 100)
                self.assertIn(shipment_id, [candidate.id for candidate in candidates])
            task = self.call(
                create_transport_task,
                TransportTaskCreateRequest(
                    route_code=route_code,
                    expected_arrival_at=clock + timedelta(days=1),
                    shipment_ids=[shipment_id],
                ),
                uuid4(),
            )
            task_id = int(task["id"])
            self.assertEqual(task["shipments"][0]["stage"], "AT_STATION")
            with SessionLocal() as session:
                candidates, _ = list_candidate_shipments(session, route_code, 1, 100)
                self.assertNotIn(shipment_id, [candidate.id for candidate in candidates])
            with SessionLocal() as session:
                current, events = get_shipment(session, shipment_id)
                detail = build_shipment_response_body(session, current, events)
                self.assertEqual(detail["active_transport_task"]["status"], "PENDING_DEPARTURE")

            depart_key = uuid4()
            task = self.call(depart_transport_task, task_id, depart_key)
            self.assertEqual(self.call(depart_transport_task, task_id, depart_key), task)
            self.call(depart_transport_task, task_id, uuid4())
            self.assertEqual(task["status"], "IN_TRANSIT")
            self.assertEqual(task["shipments"][0]["stage"], "IN_TRANSIT")
            with SessionLocal() as session:
                current, events = get_shipment(session, shipment_id)
                detail = build_shipment_response_body(session, current, events)
                self.assertEqual(detail["active_transport_task"]["origin_station_id"], str(station_ids[origin]))
                self.assertEqual(detail["active_transport_task"]["destination_station_id"], str(station_ids[destination]))
                self.assertEqual(detail["last_scanned_station_id"], str(station_ids[origin]))

            arrive_key = uuid4()
            task = self.call(arrive_transport_task, task_id, arrive_key)
            self.assertEqual(self.call(arrive_transport_task, task_id, arrive_key), task)
            self.assertEqual(task["status"], "ARRIVED")
            self.assertEqual(task["shipments"][0]["stage"], "AT_STATION")
            with SessionLocal() as session:
                count = session.scalar(select(func.count()).select_from(TrackingEvent).where(TrackingEvent.shipment_id == shipment_id))
            self.call(arrive_transport_task, task_id, uuid4())
            with SessionLocal() as session:
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(TrackingEvent).where(TrackingEvent.shipment_id == shipment_id)),
                    count,
                )
                current, events = get_shipment(session, shipment_id)
                detail = build_shipment_response_body(session, current, events)
                self.assertIsNone(detail["active_transport_task"])
                self.assertEqual(detail["last_scanned_station_id"], str(station_ids[destination]))
                task_events = [event for event in detail["tracking_events"] if event["task_id"] == str(task_id)]
                self.assertEqual([(event["event_type"], event["station_id"]) for event in task_events],
                                 [("ARRIVE", str(station_ids[destination])), ("DEPART", str(station_ids[origin]))])

            if route_code == "AB":
                with self.assertRaises(InvalidShipmentStateError):
                    self.call(
                        process_shipment_event, shipment_id,
                        ShipmentEventRequest(event_type="START_DELIVERY"), uuid4(),
                    )

        self.call(process_shipment_event, shipment_id, ShipmentEventRequest(event_type="START_DELIVERY"), uuid4())
        shipment = self.call(process_shipment_event, shipment_id, ShipmentEventRequest(event_type="SIGN"), uuid4())
        self.assertEqual(shipment["stage"], "SIGNED")
        self.assertEqual(
            [event["event_type"] for event in reversed(shipment["tracking_events"])],
            ["SHIPMENT_CREATED", "PICKUP", "ARRIVE", "DEPART", "ARRIVE", "DEPART", "ARRIVE", "START_DELIVERY", "SIGN"],
        )
        with SessionLocal() as session:
            order_row = session.get(Order, int(order["id"]))
            self.assertEqual(order_row.status, "COMPLETED")
            self.assertEqual(order_row.recipient_address, "Original recipient")


if __name__ == "__main__":
    unittest.main()
