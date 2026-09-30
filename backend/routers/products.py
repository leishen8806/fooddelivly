from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from database import get_db
from models import Category, Product
from dependencies import get_current_staff, get_current_manager

router = APIRouter(prefix="/api/v1", tags=["Products"])

# Schemas
class LocalizedText(BaseModel):
    en: str
    zh_CN: Optional[str] = Field(None, alias="zh-CN")
    km: Optional[str] = None
    
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

@router.post("/admin/products", response_model=ProductResponse)
async def admin_create_product(
    product: ProductCreate, 
    manager_info: dict = Depends(get_current_manager), 
    db: AsyncSession = Depends(get_db)
):
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
    await db.commit()
    await db.refresh(new_product)
    return new_product
