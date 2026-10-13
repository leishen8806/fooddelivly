"""Static regression checks for lifecycle safety defaults."""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models import Order, Store


class OrderLifecycleHardeningTest(unittest.TestCase):
    def test_automation_defaults_are_disabled(self):
        self.assertEqual(Store.__table__.c.unpaid_order_timeout_minutes.default.arg, 0)
        self.assertEqual(Store.__table__.c.unpaid_order_reminder_minutes.default.arg, 0)
        self.assertEqual(Store.__table__.c.shortage_customer_response_timeout_minutes.default.arg, 0)
        self.assertEqual(Store.__table__.c.shortage_refund_review_minor.default.arg, 0)

    def test_paid_receipt_snapshot_is_separate_from_adjusted_total(self):
        self.assertIsNotNone(Order.__table__.c.paid_confirmed_minor)
        self.assertIsNotNone(Order.__table__.c.adjusted_total_minor)
        self.assertIsNotNone(Order.__table__.c.removed_minor)

    def test_locales_have_lifecycle_messages_in_all_languages(self):
        path = pathlib.Path(__file__).resolve().parents[2] / "docs" / "ui" / "locales.json"
        locales = json.loads(path.read_text(encoding="utf-8"))
        keys = {"order.cancelledExpired", "order.expiryReminder", "order.lateProofAfterExpiry",
                "order.shortageResolved", "order.openShortage", "payment.lateProofAfterExpiry",
                "order.shortageReminder", "order.shortageEscalated", "admin.shortageRefundReview"}
        for language in ("zh-CN", "en", "km"):
            self.assertTrue(keys.issubset(locales[language]))


if __name__ == "__main__":
    unittest.main()
