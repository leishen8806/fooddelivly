"""Fast, database-free regression tests for the P0 order lifecycle rules.

These tests intentionally describe the failing contract before the PostgreSQL
integration suite is run: a customer may only cancel an unpaid new order and
the timeout boundary is deterministic.
"""
from datetime import datetime, timedelta, timezone
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from order_lifecycle import customer_can_cancel, unpaid_order_expired


class OrderLifecycleRulesTest(unittest.TestCase):
    def test_customer_can_cancel_only_new_unpaid_or_rejected(self):
        self.assertTrue(customer_can_cancel("NEW", "UNPAID"))
        self.assertTrue(customer_can_cancel("NEW", "REJECTED"))
        self.assertFalse(customer_can_cancel("NEW", "PROOF_SUBMITTED"))
        self.assertFalse(customer_can_cancel("ACCEPTED", "UNPAID"))
        self.assertFalse(customer_can_cancel("COMPLETED", "PAID_CONFIRMED"))

    def test_timeout_is_disabled_at_zero(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(unpaid_order_expired(now - timedelta(days=2), 0, now))

    def test_timeout_uses_inclusive_boundary(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(unpaid_order_expired(now - timedelta(minutes=30), 30, now))
        self.assertFalse(unpaid_order_expired(now - timedelta(minutes=29, seconds=59), 30, now))


if __name__ == "__main__":
    unittest.main()
