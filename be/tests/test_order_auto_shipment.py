import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from errors import NetworkError
from orders.router import create_order
from orders.schemas import OrderCreateRequest
from orders.router import auto_schedule_shipment


class OrderAutoShipmentTests(unittest.TestCase):
    def setUp(self):
        self.request = OrderCreateRequest(
            product_name="test",
            quantity=1,
            sender_name="sender",
            sender_address="sender address",
            recipient_name="recipient",
            recipient_address="recipient address",
        )
        self.key = uuid4()
        self.session = MagicMock()
        self.order_body = {
            "id": "123",
            "order_no": "ORD-000123",
            "product_name": "test",
            "quantity": 1,
            "sender_name": "sender",
            "sender_address": "sender address",
            "recipient_name": "recipient",
            "recipient_address": "recipient address",
            "region_code": "Z",
            "status": "PENDING_SHIPMENT",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

    @patch("orders.router.confirm_schedule")
    @patch("orders.router.preview_schedule")
    @patch("orders.router.shipment_service_options")
    def test_auto_schedule_confirms_earliest_available_trip(self, service_options, preview_schedule, confirm_schedule):
        service_options.return_value = {"items": [{"trip_id": "8"}, {"trip_id": "9"}]}
        preview_schedule.return_value = {
            "can_confirm": True,
            "preview_token": "signed-token",
            "warnings": [{"code": "TRAVEL_BELOW_REFERENCE:0"}],
        }

        auto_schedule_shipment(self.session, 456, self.key)

        self.assertEqual(preview_schedule.call_args.args[2].scheduled_trip_id, 8)
        request = confirm_schedule.call_args.args[2]
        self.assertEqual(request.acknowledged_warning_codes, ["TRAVEL_BELOW_REFERENCE:0"])
        self.session.rollback.assert_called_once()

    @patch("orders.router.confirm_schedule")
    @patch("orders.router.preview_schedule")
    @patch("orders.router.shipment_service_options", return_value={"items": []})
    def test_no_trip_leaves_shipment_unscheduled(self, service_options, preview_schedule, confirm_schedule):
        auto_schedule_shipment(self.session, 456, self.key)

        preview_schedule.assert_not_called()
        confirm_schedule.assert_not_called()

    @patch("orders.router.create_shipment")
    @patch("orders.router.auto_schedule_shipment")
    @patch("orders.router.create_order_service")
    def test_order_creation_automatically_creates_reviewed_shipment(self, create_order_service, auto_schedule_shipment, create_shipment):
        create_order_service.return_value = dict(self.order_body)
        create_shipment.return_value = ({"id": "456"}, 201)

        result = create_order(self.request, self.key, self.session)

        self.assertEqual(result.status, "SHIPMENT_CREATED")
        create_shipment.assert_called_once()
        self.assertEqual(create_shipment.call_args.kwargs["order_id"], 123)
        self.assertEqual(create_shipment.call_args.kwargs["scheduling_mode"], "REVIEWED")
        auto_schedule_shipment.assert_called_once()
        self.assertEqual(auto_schedule_shipment.call_args.args[1], 456)

    @patch("orders.router.create_shipment", side_effect=NetworkError("DESTINATION_NOT_FOUND", "no service area"))
    @patch("orders.router.auto_schedule_shipment")
    @patch("orders.router.create_order_service")
    def test_missing_delivery_coverage_keeps_order_pending(self, create_order_service, auto_schedule_shipment, create_shipment):
        create_order_service.return_value = dict(self.order_body)

        result = create_order(self.request, self.key, self.session)

        self.assertEqual(result.status, "PENDING_SHIPMENT")
        auto_schedule_shipment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
