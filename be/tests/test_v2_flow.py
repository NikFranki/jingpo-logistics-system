import os
import unittest
from datetime import timedelta

from sqlalchemy.engine import make_url

import business_time
from db import SessionLocal
from models import TransportTask
from transport.service import calculate_task_delay


@unittest.skipUnless(
    (make_url(os.environ["DATABASE_URL"]).database or "").endswith("_test")
    if os.getenv("DATABASE_URL")
    else False,
    "requires an isolated *_test PostgreSQL database",
)
class V2FlowTests(unittest.TestCase):
    def test_ab_delay_uses_task_time(self):
        expected = self._clock() + timedelta(hours=1)
        task = TransportTask(
            status="IN_TRANSIT",
            expected_arrival_at=expected,
            delay_monitoring_enabled=True,
        )
        task.departed_at = expected - timedelta(hours=1)
        self.assertEqual(
            calculate_task_delay(task, expected + timedelta(minutes=9)),
            ("OVERDUE", 9),
        )
        task.delay_monitoring_enabled = False
        self.assertEqual(
            calculate_task_delay(task, expected + timedelta(minutes=9)),
            ("NOT_APPLICABLE", None),
        )
        task.delay_monitoring_enabled = True
        task.status = "ARRIVED"
        task.arrived_at = expected + timedelta(minutes=7)
        self.assertEqual(
            calculate_task_delay(task, expected + timedelta(hours=3)),
            ("LATE_ARRIVAL", 7),
        )

    @staticmethod
    def _clock():
        with SessionLocal():
            return business_time.server_now()


if __name__ == "__main__":
    unittest.main()
