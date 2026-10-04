from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from database import get_db
from models import Category, Product, ProductOption, ProductOptionGroup, ProductSaleWindow, StoreSettings, AuditLog, product_categories
from dependencies import get_current_staff, get_current_manager
from product_options import serialize_groups
from store_context import effective_product, load_overrides, resolve_store, staff_store
from sale_window import describe, is_on_sale, next_open_at

router = APIRouter(prefix="/api/v1", tags=["Products"])

# Schemas
class LocalizedText(BaseModel):
    en: str = Field(min_length=1, max_length=120)
    zh_CN: Optional[str] = Field(None, alias="zh-CN", max_length=120)
    km: Optional[str] = Field(None, max_length=120)
    
    class Config:
        populate_by_name = True

class ProductResponse(BaseModel):
    id: int
    category_id: int
    category_ids: List[int] = Field(default_factory=list)
    name: LocalizedText
    description: Optional[LocalizedText]
    price_minor: int
    currency: str
    image_key: Optional[str]
    available: bool
    sort_order: int = 0
    sweetness_enabled: bool

    class Config:
        from_attributes = True

class CategoryResponse(BaseModel):
    id: int
    name: LocalizedText
    sort_order: int
    active: bool
    products: List[ProductResponse] = []

    class Config:
        from_attributes = True

def serialize_product(product: Product, *, language: str = "en",
                      now_local: datetime | None = None,
                      price_minor: int | None = None,
                      sale_windows: list | None = None,
                      include_category_ids: bool = False,
                      include_inactive_options: bool = False) -> dict:
    """菜品对外结构。

    `available` 是后厨/管理端开关（卖完了），`orderable` 是「此刻能不能下单」——
    两者都满足才行。前端用 orderable 决定按钮，服务端下单时**还会再校验一次**。
    """
    if sale_windows is not None:
        windows = list(sale_windows)          # 门店覆盖后的售卖时间（JSON 形式）
    else:
        windows = list(product.sale_windows or [])
    on_sale = is_on_sale(windows, now_local.time()) if now_local else True
    next_open = next_open_at(windows, now_local) if now_local else None
    payload = {
        "id": product.id,
        "category_id": product.category_id,
        "name": product.name,
        "description": product.description,
        "price_minor": int(price_minor) if price_minor is not None else product.price_minor,
        "currency": product.currency,
        "image_key": product.image_key,
        "available": bool(product.available),
        "sort_order": product.sort_order,
        "sweetness_enabled": bool(product.sweetness_enabled),
        "sale_windows": describe(windows),
        "has_sale_window": bool(windows),
        "on_sale_now": on_sale,
        "orderable": bool(product.available) and on_sale,
        "next_sale_start": next_open.isoformat() if (next_open and not on_sale) else None,
        "option_groups": serialize_groups(product.option_groups or [], language,
                                           include_inactive=include_inactive_options),
    }
    if include_category_ids:
        payload["category_ids"] = [category.id for category in (product.categories or [])]
    return payload


_CHILD_LOADERS = (
    selectinload(Product.sale_windows),
    selectinload(Product.option_groups).selectinload(ProductOptionGroup.options),
    selectinload(Product.categories),
)


async def store_now(db: AsyncSession, store=None) -> tuple[datetime, str]:
    """店铺当前时间 + 语言。售卖时间必须按店铺时区判断，不能用服务器时区。"""
    settings = None
    if store is None:
        settings = (await db.execute(select(StoreSettings).limit(1))).scalars().first()
    tz_name = ((store.timezone if store is not None else settings.timezone)
               if (store is not None or settings is not None) else "Asia/Phnom_Penh")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Phnom_Penh")
    language = (store.staff_group_language if store is not None
                else settings.staff_group_language if settings else "en") or "en"
    return datetime.now(tz), language


# Endpoints
@router.get("/menu")
async def get_menu(store: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """菜单。`?store=ST01` 指定门店（深链/二维码带过来），不传则用主店。

    价格/上架/排序/售卖时间都按「模板 + 覆盖」解析——与下单计价共用
    store_context.effective_product()，保证看到的价格就是扣款的价格。
    """
    result = await db.execute(
        select(Category)
        .filter(Category.active == True)
        .options(selectinload(Category.products).selectinload(Product.sale_windows),
                 selectinload(Category.products).selectinload(Product.option_groups)
                 .selectinload(ProductOptionGroup.options))
        .order_by(Category.sort_order)
    )
    categories = result.scalars().all()
    store_row = await resolve_store(db, store_code=store)
    now_local, language = await store_now(db, store_row)
    all_products = [p for cat in categories for p in cat.products]
    overrides = await load_overrides(db, store_row.id if store_row else None,
                                     [p.id for p in all_products])

    payload = []
    for cat in categories:
        effective = [effective_product(p, overrides.get(p.id), store_row) for p in cat.products]
        # 门店覆盖可以单独下架；总部下架仍然全局生效（effective_product 里取与）
        effective = [e for e in effective if e.available]
        effective.sort(key=lambda e: (e.sort_order, e.id))
        payload.append({
            "id": cat.id,
            "name": cat.name,
            "sort_order": cat.sort_order,
            "active": bool(cat.active),
            "products": [serialize_product(e.product, language=language, now_local=now_local,
                                           price_minor=e.price_minor, sale_windows=e.sale_windows)
                         for e in effective],
        })
    return payload

@router.get("/admin/products")
async def admin_get_products(
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Product).options(*_CHILD_LOADERS)
        .order_by(Product.category_id, Product.sort_order, Product.id))
    products = result.scalars().all()
    store_row = await staff_store(db, staff_info)
    now_local, language = await store_now(db, store_row)
    return [serialize_product(p, language=language, now_local=now_local,
                              include_category_ids=True,
                              include_inactive_options=True) for p in products]

# We'll need schemas for creating products, updating them, etc.
class ProductCreate(BaseModel):
    category_id: Optional[int] = None
    category_ids: Optional[List[int]] = Field(None, max_length=50)
    name: LocalizedText
    description: Optional[LocalizedText] = None
    price_minor: int = Field(gt=0, description="Price must be positive")
    currency: str = "USD"
    image_key: Optional[str] = None
    available: bool = True
    sort_order: int = 0
    sweetness_enabled: bool = False

class ProductUpdate(BaseModel):
    category_id: Optional[int] = None
    category_ids: Optional[List[int]] = Field(None, max_length=50)
    name: Optional[LocalizedText] = None
    description: Optional[LocalizedText] = None
    price_minor: Optional[int] = Field(None, gt=0)
    currency: Optional[str] = None
    image_key: Optional[str] = None
    available: Optional[bool] = None
    sort_order: Optional[int] = None
    sweetness_enabled: Optional[bool] = None

class CategoryCreate(BaseModel):
    name: LocalizedText
    sort_order: int = 0
    active: bool = True

class CategoryUpdate(BaseModel):
    name: Optional[LocalizedText] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


def _normalize_category_ids(category_ids: List[int] | None, category_id: int | None = None) -> List[int]:
    raw = category_ids if category_ids is not None else ([category_id] if category_id is not None else [])
    normalized: List[int] = []
    for value in raw:
        if value is None or value <= 0:
            continue
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise HTTPException(status_code=422, detail="At least one product category is required")
    return normalized


async def _validate_categories(db: AsyncSession, category_ids: List[int]) -> None:
    rows = (await db.execute(select(Category.id).filter(Category.id.in_(category_ids)))).scalars().all()
    if len(rows) != len(category_ids):
        raise HTTPException(status_code=422, detail="One or more categories were not found")


async def _replace_product_categories(db: AsyncSession, product_id: int, category_ids: List[int]) -> None:
    await db.execute(product_categories.delete().where(product_categories.c.product_id == product_id))
    await db.execute(product_categories.insert(), [
        {"product_id": product_id, "category_id": category_id, "sort_order": index}
        for index, category_id in enumerate(category_ids)
    ])


def _product_response_payload(product: Product, category_ids: List[int]) -> dict:
    return {
        "id": product.id,
        "category_id": product.category_id,
        "category_ids": category_ids,
        "name": product.name,
        "description": product.description,
        "price_minor": product.price_minor,
        "currency": product.currency,
        "image_key": product.image_key,
        "available": product.available,
        "sort_order": product.sort_order,
        "sweetness_enabled": product.sweetness_enabled,
    }

@router.get("/admin/categories")
async def admin_get_categories(manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category).order_by(Category.sort_order, Category.id))
    return result.scalars().all()

@router.post("/admin/categories")
async def admin_create_category(category: CategoryCreate, manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    row = Category(name=category.name.model_dump(by_alias=True, exclude_none=True), sort_order=category.sort_order, active=category.active)
    db.add(row)
    await db.flush()
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="category", entity_id=str(row.id), action="created", details={"name": row.name}))
    await db.commit()
    await db.refresh(row)
    return row

@router.patch("/admin/categories/{category_id}")
async def admin_update_category(category_id: int, changes: CategoryUpdate,
                                manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category).filter(Category.id == category_id).with_for_update())
    category = result.scalars().first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    values = changes.model_dump(exclude_unset=True, by_alias=True)
    if "name" in values and values["name"] is not None:
        values["name"] = changes.name.model_dump(by_alias=True, exclude_none=True)
    for field, value in values.items():
        setattr(category, field, value)
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="category", entity_id=str(category.id),
                    action="updated", details={"changed_fields": list(values)}))
    await db.commit()
    await db.refresh(category)
    return category

@router.post("/admin/products", response_model=ProductResponse)
async def admin_create_product(
    product: ProductCreate, 
    manager_info: dict = Depends(get_current_manager), 
    db: AsyncSession = Depends(get_db)
):
    category_ids = _normalize_category_ids(product.category_ids, product.category_id)
    await _validate_categories(db, category_ids)
    new_product = Product(
        category_id=category_ids[0],
        name=product.name.model_dump(by_alias=True, exclude_none=True),
        description=product.description.model_dump(by_alias=True, exclude_none=True) if product.description else None,
        price_minor=product.price_minor,
        currency=product.currency,
        image_key=product.image_key,
        available=product.available,
        sort_order=product.sort_order,
        sweetness_enabled=product.sweetness_enabled,
    )
    db.add(new_product)
    await db.flush()
    await _replace_product_categories(db, new_product.id, category_ids)
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product", entity_id=str(new_product.id),
                    action="created", details={"name": new_product.name, "price_minor": new_product.price_minor,
                                               "currency": new_product.currency,
                                               "sweetness_enabled": new_product.sweetness_enabled}))
    await db.commit()
    await db.refresh(new_product)
    return _product_response_payload(new_product, category_ids)

@router.patch("/admin/products/{product_id}", response_model=ProductResponse)
async def admin_update_product(product_id: int, changes: ProductUpdate,
                               manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).filter(Product.id == product_id).with_for_update())
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    values = changes.model_dump(exclude_unset=True, by_alias=True)
    raw_category_ids = values.pop("category_ids", None)
    legacy_category_id = values.pop("category_id", None)
    category_ids = None
    if raw_category_ids is not None or legacy_category_id is not None:
        category_ids = _normalize_category_ids(raw_category_ids, legacy_category_id)
        await _validate_categories(db, category_ids)
        values["category_id"] = category_ids[0]
    for field, value in values.items():
        if field in {"name", "description"} and value is not None:
            value = value.model_dump(by_alias=True, exclude_none=True) if isinstance(value, LocalizedText) else value
        setattr(product, field, value)
    if category_ids is not None:
        await _replace_product_categories(db, product.id, category_ids)
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product", entity_id=str(product.id),
                    action="updated", details={"changed_fields": list(values),
                                                "sweetness_enabled": product.sweetness_enabled}))
    await db.commit()
    await db.refresh(product)
    if category_ids is None:
        category_ids = [product.category_id]
    return _product_response_payload(product, category_ids)

@router.delete("/admin/products/{product_id}")
async def admin_archive_product(product_id: int, manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).filter(Product.id == product_id).with_for_update())
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.available = False
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product", entity_id=str(product.id),
                    action="archived", details={"available": False}))
    await db.commit()
    return {"message": "Product set unavailable"}


# ---------------------------------------------------------------------------
# 售卖时间 & 规格 / 附加选择（管理端）
# ---------------------------------------------------------------------------

class SaleWindowIn(BaseModel):
    start: str = Field(description="HH:MM（店铺时区）")
    end: str = Field(description="HH:MM；小于 start 表示跨午夜")


class SaleWindowsIn(BaseModel):
    windows: List[SaleWindowIn] = Field(default_factory=list, max_length=12)


class OptionIn(BaseModel):
    name: LocalizedText
    price_delta_minor: int = Field(0, ge=-1_000_000, le=1_000_000)
    is_default: bool = False
    active: bool = True


class OptionGroupIn(BaseModel):
    name: LocalizedText
    kind: str = Field(pattern="^(SPEC|ADDON)$")
    required: bool = False
    multi_select: bool = False
    max_select: Optional[int] = Field(None, ge=1, le=50)
    active: bool = True
    options: List[OptionIn] = Field(min_length=1, max_length=50)


class OptionGroupsIn(BaseModel):
    groups: List[OptionGroupIn] = Field(default_factory=list, max_length=20)


def _parse_hhmm(value: str, field: str) -> time:
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be HH:MM")
    return parsed.replace(second=0, microsecond=0)


async def _locked_product(db: AsyncSession, product_id: int) -> Product:
    result = await db.execute(select(Product).filter(Product.id == product_id).with_for_update())
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.put("/admin/products/{product_id}/sale-windows")
async def admin_set_sale_windows(product_id: int, payload: SaleWindowsIn,
                                 manager_info: dict = Depends(get_current_manager),
                                 db: AsyncSession = Depends(get_db)):
    """整体替换售卖时间段。传空数组 = 恢复全天可售。"""
    product = await _locked_product(db, product_id)

    parsed = []
    for raw in payload.windows:
        start = _parse_hhmm(raw.start, "start")
        end = _parse_hhmm(raw.end, "end")
        if start == end:
            # 起止相同会有歧义（0 长度还是 24 小时？），直接拒绝
            raise HTTPException(status_code=422, detail="开始与结束时间不能相同（全天可售请留空）")
        parsed.append((start, end))

    await db.execute(
        ProductSaleWindow.__table__.delete().where(ProductSaleWindow.product_id == product_id))
    for index, (start, end) in enumerate(sorted(parsed)):
        db.add(ProductSaleWindow(product_id=product_id, start_time=start, end_time=end,
                                 sort_order=index))
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product",
                    entity_id=str(product_id), action="sale_windows_updated",
                    details={"windows": [{"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M")}
                                         for s, e in parsed]}))
    await db.commit()
    return {"product_id": product_id,
            "windows": [{"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M")}
                        for s, e in sorted(parsed)]}


@router.put("/admin/products/{product_id}/option-groups")
async def admin_set_option_groups(product_id: int, payload: OptionGroupsIn,
                                  manager_info: dict = Depends(get_current_manager),
                                  db: AsyncSession = Depends(get_db)):
    """整体替换规格 / 附加分组。

    整体替换而不是增删改单条：管理端是「编辑完整表单再保存」的交互，
    一次 PUT 语义最清楚，也不会出现「组删了选项还留着」的中间态。
    历史订单里存的是**下单时的名称与加价快照**，所以这里重建不影响旧订单。
    """
    product = await _locked_product(db, product_id)

    # 主语言名不能为空（LocalizedText 已保证 en 有值）
    for group in payload.groups:
        if group.kind == "SPEC" and group.multi_select:
            raise HTTPException(status_code=422, detail="规格组是单选，不能开启多选")
        if group.kind == "SPEC" and group.max_select is not None:
            raise HTTPException(status_code=422, detail="规格组是单选，不需要设置最多可选数量")
        if group.multi_select and group.kind == "SPEC":
            raise HTTPException(status_code=422, detail="规格组是单选，不能开启多选")
        defaults = [o for o in group.options if o.is_default]
        if group.kind == "SPEC" and len(defaults) > 1:
            raise HTTPException(status_code=422, detail="单选组最多只能有一个默认选项")
        if not any(o.active for o in group.options):
            raise HTTPException(status_code=422, detail="每个分组至少要有一个启用中的选项")

    existing = (await db.execute(
        select(ProductOptionGroup).filter(ProductOptionGroup.product_id == product_id)
    )).scalars().all()
    for group in existing:
        await db.delete(group)          # 级联删掉选项
    await db.flush()

    summary = []
    for index, group in enumerate(payload.groups):
        row = ProductOptionGroup(
            product_id=product_id,
            name=group.name.model_dump(by_alias=True, exclude_none=True),
            kind=group.kind,
            required=group.required,
            multi_select=(group.kind == "ADDON") and group.multi_select,
            max_select=group.max_select if group.kind == "ADDON" else None,
            sort_order=index,
            active=group.active,
        )
        db.add(row)
        await db.flush()
        for opt_index, option in enumerate(group.options):
            db.add(ProductOption(
                group_id=row.id,
                name=option.name.model_dump(by_alias=True, exclude_none=True),
                price_delta_minor=option.price_delta_minor,
                is_default=option.is_default,
                sort_order=opt_index,
                active=option.active,
            ))
        summary.append({"index": index, "name": row.name, "kind": row.kind,
                        "options": len(group.options)})

    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product",
                    entity_id=str(product_id), action="option_groups_updated",
                    details={"groups": summary}))
    await db.commit()
    return {"product_id": product_id, "groups": summary}
