from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    language_code = Column(String, nullable=True)
    preferred_language = Column(String, nullable=True)
    # The Telegram deep link selects the exact order whose proof is being sent.
    pending_payment_order_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, unique=True, index=True, nullable=True)
    login_name = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="STAFF") # 'STAFF' or 'MANAGER'
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(JSON, nullable=False) # { "en": "...", "zh-CN": "...", "km": "..." }
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    
    products = relationship("Product", back_populates="category", order_by="(Product.sort_order, Product.id)")

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    name = Column(JSON, nullable=False)
    description = Column(JSON, nullable=True)
    price_minor = Column(Integer, nullable=False)
    currency = Column(String, default="USD")
    image_key = Column(String, nullable=True)
    available = Column(Boolean, default=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    sweetness_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    
    category = relationship("Category", back_populates="products")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    public_code = Column(String, unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    room_number = Column(String, nullable=False)
    order_status = Column(String, default="NEW")
    payment_status = Column(String, default="UNPAID")
    currency = Column(String, default="USD")
    customer_language = Column(String, nullable=False, default="en")
    total_minor = Column(Integer, nullable=False)
    telegram_group_message_id = Column(String, nullable=True)
    idempotency_key = Column(String, index=True, nullable=True)
    request_digest = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('customer_id', 'idempotency_key', name='uq_order_idempotency'),
    )
    
    items = relationship("OrderItem", back_populates="order")

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name_snapshot = Column(JSON, nullable=False)
    unit_price_minor = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    options_json = Column(JSON, nullable=True)
    line_total_minor = Column(Integer, nullable=False)
    
    order = relationship("Order", back_populates="items")

class PaymentProof(Base):
    __tablename__ = "payment_proofs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    telegram_file_id = Column(String, nullable=False)
    submitted_by = Column(Integer, ForeignKey("customers.id"))
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    review_status = Column(String, default="PENDING")

class PaymentReview(Base):
    __tablename__ = "payment_reviews"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    staff_id = Column(Integer, ForeignKey("staff.id"))
    decision = Column(String, nullable=False) # 'APPROVED' or 'REJECTED'
    reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class OrderEvent(Base):
    __tablename__ = "order_events"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    actor_type = Column(String, nullable=False) # 'CUSTOMER', 'STAFF', 'SYSTEM'
    actor_id = Column(Integer, nullable=True) # customer_id or staff_id
    event = Column(String, nullable=False) # e.g. 'CREATED', 'ACCEPTED', 'PAYMENT_CONFIRMED'
    from_state = Column(String, nullable=True)
    to_state = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class StoreSettings(Base):
    __tablename__ = "store_settings"

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, default="USD")
    timezone = Column(String, default="Asia/Phnom_Penh")
    aba_qr_asset_key = Column(String, nullable=True)
    payment_link = Column(String, nullable=True)
    telegram_staff_group_id = Column(String, nullable=True)
    staff_group_language = Column(String, nullable=False, default="en")
    open_hours = Column(String, nullable=True)
    delivery_mode = Column(String, nullable=True)

