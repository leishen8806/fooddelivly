from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from database import get_db
from models import Category, Product, AuditLog
from dependencies import get_current_staff, get_current_manager

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
    name: LocalizedText
    description: Optional[LocalizedText]
    price_minor: int
    currency: str
    image_key: Optional[str]
    available: bool

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

# Endpoints
@router.get("/menu", response_model=List[CategoryResponse])
async def get_menu(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Category)
        .filter(Category.active == True)
        .options(selectinload(Category.products))
        .order_by(Category.sort_order)
    )
    categories = result.scalars().all()
    
    # Filter only available products for the menu display
    for cat in categories:
        cat.products = [p for p in cat.products if p.available]
        
    return categories

@router.get("/admin/products", response_model=List[ProductResponse])
async def admin_get_products(
    staff_info: dict = Depends(get_current_staff), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Product).order_by(Product.id))
    products = result.scalars().all()
    return products

# We'll need schemas for creating products, updating them, etc.
class ProductCreate(BaseModel):
    category_id: int
    name: LocalizedText
    description: Optional[LocalizedText] = None
    price_minor: int = Field(gt=0, description="Price must be positive")
    currency: str = "USD"
    image_key: Optional[str] = None
    available: bool = True

class ProductUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[LocalizedText] = None
    description: Optional[LocalizedText] = None
    price_minor: Optional[int] = Field(None, gt=0)
    currency: Optional[str] = None
    image_key: Optional[str] = None
    available: Optional[bool] = None

class CategoryCreate(BaseModel):
    name: LocalizedText
    sort_order: int = 0
    active: bool = True

class CategoryUpdate(BaseModel):
    name: Optional[LocalizedText] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None

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
    category_result = await db.execute(select(Category).filter(Category.id == product.category_id))
    if not category_result.scalars().first():
        raise HTTPException(status_code=422, detail="Category not found")
    new_product = Product(
        category_id=product.category_id,
        name=product.name.model_dump(by_alias=True, exclude_none=True),
        description=product.description.model_dump(by_alias=True, exclude_none=True) if product.description else None,
        price_minor=product.price_minor,
        currency=product.currency,
        image_key=product.image_key,
        available=product.available
    )
    db.add(new_product)
    await db.flush()
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product", entity_id=str(new_product.id),
                    action="created", details={"name": new_product.name, "price_minor": new_product.price_minor,
                                               "currency": new_product.currency}))
    await db.commit()
    await db.refresh(new_product)
    return new_product

@router.patch("/admin/products/{product_id}", response_model=ProductResponse)
async def admin_update_product(product_id: int, changes: ProductUpdate,
                               manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).filter(Product.id == product_id).with_for_update())
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    values = changes.model_dump(exclude_unset=True, by_alias=True)
    if "category_id" in values:
        category_result = await db.execute(select(Category).filter(Category.id == values["category_id"]))
        if not category_result.scalars().first():
            raise HTTPException(status_code=422, detail="Category not found")
    for field, value in values.items():
        if field in {"name", "description"} and value is not None:
            value = value.model_dump(by_alias=True, exclude_none=True) if isinstance(value, LocalizedText) else value
        setattr(product, field, value)
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="product", entity_id=str(product.id),
                    action="updated", details={"changed_fields": list(values)}))
    await db.commit()
    await db.refresh(product)
    return product

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
