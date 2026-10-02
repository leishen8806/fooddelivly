"""门店上下文：解析当前店铺、应用「模板 + 覆盖」。

已在规格里锁定的模型（docs/MULTI_MERCHANT_PLAN.md）：

  * `products.store_id IS NULL` = 总部模板；各店用 `store_product_overrides` 覆盖；
  * 覆盖字段为 NULL 表示「继承总部」；
  * **菜单展示与下单计价必须共用 `effective_product()`**，否则会出现
    「菜单显示 7 块、下单扣 5 块」这种最容易被客人发现的错误；
  * 覆盖表为空 = 与总部菜单完全一致（对现有单店行为零影响）。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Product, Store, StoreProductOverride

DEFAULT_STORE_CODE = "MAIN"


@dataclass(frozen=True)
class EffectiveProduct:
    """门店视角下的菜品（价格/上架/排序/售卖时间都已按覆盖解析）。"""
    product: Product
    price_minor: int
    available: bool
    sort_order: int
    sale_windows: list            # [{"start": "08:00", "end": "20:00"}]

    @property
    def id(self) -> int:
        return self.product.id


async def default_store(db: AsyncSession) -> Store | None:
    """主店：优先取 code=MAIN，其次取最早创建的一家。"""
    row = (await db.execute(select(Store).filter(Store.code == DEFAULT_STORE_CODE))).scalars().first()
    if row is not None:
        return row
    return (await db.execute(select(Store).order_by(Store.id))).scalars().first()


async def get_store(db: AsyncSession, store_id: int | None = None) -> Store | None:
    if store_id is None:
        return await default_store(db)
    return (await db.execute(select(Store).filter(Store.id == store_id))).scalars().first()


async def get_store_by_code(db: AsyncSession, code: str | None) -> Store | None:
    """深链/二维码带来的店铺码（`?start=store_ST01`、`/menu?store=ST01`）。"""
    if not code:
        return await default_store(db)
    return (await db.execute(select(Store).filter(Store.code == code))).scalars().first()


async def resolve_store(db: AsyncSession, *, store_code: str | None = None,
                        customer=None) -> Store | None:
    """解析当前请求应该用哪家店。

    优先级：显式传入的店铺码 > 客户绑定店 > 主店。
    客户传了非法店铺码时**回退到主店**而不是报错——二维码扫错不该让顾客点不了单。
    """
    if store_code:
        store = await get_store_by_code(db, store_code)
        if store is not None:
            return store
    if customer is not None and getattr(customer, "store_id", None):
        store = await get_store(db, customer.store_id)
        if store is not None:
            return store
    return await default_store(db)


async def load_overrides(db: AsyncSession, store_id: int | None,
                         product_ids: list[int]) -> dict[int, StoreProductOverride]:
    """一次把门店覆盖读出来，避免菜单里 N 次查询。"""
    if not store_id or not product_ids:
        return {}
    rows = (await db.execute(
        select(StoreProductOverride).filter(
            StoreProductOverride.store_id == store_id,
            StoreProductOverride.product_id.in_(list({int(pid) for pid in product_ids})),
        )
    )).scalars().all()
    return {row.product_id: row for row in rows}


def effective_product(product: Product, override: StoreProductOverride | None = None,
                      store: Store | None = None) -> EffectiveProduct:
    """把总部菜品与门店覆盖合并成门店视角的菜品。"""
    windows = product_sale_windows(product)
    if override is not None and override.sale_windows is not None:
        windows = list(override.sale_windows or [])
    return EffectiveProduct(
        product=product,
        price_minor=int(override.price_minor) if (override and override.price_minor) else int(product.price_minor),
        available=(bool(product.available)
                   and (override.available if (override and override.available is not None) else True)),
        sort_order=int(override.sort_order) if (override and override.sort_order is not None) else int(product.sort_order or 0),
        sale_windows=windows,
    )


def product_sale_windows(product: Product) -> list:
    """总部模板的售卖时间（转成 JSON 形式，与覆盖表同构）。"""
    if not getattr(product, "sale_windows", None):
        return []
    return [{"start": w.start_time.strftime("%H:%M"), "end": w.end_time.strftime("%H:%M")}
            for w in product.sale_windows]
