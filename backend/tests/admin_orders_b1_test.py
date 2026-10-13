import asyncio

import pytest
from fastapi import HTTPException

from models import Order
from routers.admin_orders import _like, _order_filters, _received_minor, router


def test_order_search_escapes_like_wildcards_and_keeps_sort_routes():
    assert _like("' OR 1=1 --%_") == "' OR 1=1 --\\%\\_"
    paths = [route.path for route in router.routes]
    assert "/api/v1/admin/orders/search" in paths
    assert "/api/v1/admin/orders/counts" in paths
    assert "/api/v1/admin/orders/{order_code}" in paths


def test_received_amount_is_confirmed_less_completed_refunds():
    order = Order(payment_status="PAID_CONFIRMED", paid_confirmed_minor=1000)
    assert _received_minor(order, 250) == 750
    order.payment_status = "PROOF_SUBMITTED"
    assert _received_minor(order, 0) == 0


def test_invalid_status_is_rejected_before_query():
    with pytest.raises(HTTPException) as error:
        asyncio.run(_order_filters(object(), {"store_id": None}, None, None, None, ["DROP TABLE"], None, None))
    assert error.value.status_code == 422
