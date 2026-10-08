import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from errors import NetworkError
from orders.router import create_order
from orders.schemas import OrderCreateRequest


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
        self.session = object()
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

    @patch("orders.router.create_shipment")
    @patch("orders.router.create_order_service")
    def test_order_creation_automatically_creates_reviewed_shipment(self, create_order_service, create_shipment):
        create_order_service.return_value = dict(self.order_body)

        result = create_order(self.request, self.key, self.session)

        self.assertEqual(result.status, "SHIPMENT_CREATED")
        create_shipment.assert_called_once()
        self.assertEqual(create_shipment.call_args.kwargs["order_id"], 123)
        self.assertEqual(create_shipment.call_args.kwargs["scheduling_mode"], "REVIEWED")

    @patch("orders.router.create_shipment", side_effect=NetworkError("DESTINATION_NOT_FOUND", "no service area"))
    @patch("orders.router.create_order_service")
    def test_missing_delivery_coverage_keeps_order_pending(self, create_order_service, create_shipment):
        create_order_service.return_value = dict(self.order_body)

        result = create_order(self.request, self.key, self.session)

        self.assertEqual(result.status, "PENDING_SHIPMENT")


if __name__ == "__main__":
    unittest.main()
