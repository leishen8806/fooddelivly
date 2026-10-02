from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, ForeignKey, JSON, Time, UniqueConstraint
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
    # 同理：用户在 /wallet 里发起充值后，接下来发的那张截图属于哪张充值单。
    # （不建外键，与上面的 pending_payment_order_id 保持一致；wallet schema 可独立迁移）
    pending_recharge_order_id = Column(Integer, nullable=True)
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
    # 售卖时间窗：没有记录 = 全天可售
    sale_windows = relationship("ProductSaleWindow", back_populates="product",
                                cascade="all, delete-orphan",
                                order_by="(ProductSaleWindow.sort_order, ProductSaleWindow.id)")
    option_groups = relationship("ProductOptionGroup", back_populates="product",
                                 cascade="all, delete-orphan",
                                 order_by="(ProductOptionGroup.sort_order, ProductOptionGroup.id)")

class ProductSaleWindow(Base):
    """菜品售卖时间段（店铺时区，每天生效）。start > end 表示跨午夜。"""
    __tablename__ = "product_sale_windows"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")

    product = relationship("Product", back_populates="sale_windows")


class ProductOptionGroup(Base):
    """规格组（SPEC，单选）或附加组（ADDON，多选）。"""
    __tablename__ = "product_option_groups"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    name = Column(JSON, nullable=False)                       # {"en": "...", "zh-CN": "...", "km": "..."}
    kind = Column(String, nullable=False)                     # SPEC | ADDON
    required = Column(Boolean, nullable=False, default=False, server_default="false")
    multi_select = Column(Boolean, nullable=False, default=False, server_default="false")
    max_select = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    active = Column(Boolean, nullable=False, default=True, server_default="true")

    product = relationship("Product", back_populates="option_groups")
    options = relationship("ProductOption", back_populates="group",
                           cascade="all, delete-orphan",
                           order_by="(ProductOption.sort_order, ProductOption.id)")


class ProductOption(Base):
    """具体选项：中杯 / 大杯 / 加珍珠 …… 带价格增减（最小货币单位）。"""
    __tablename__ = "product_options"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("product_option_groups.id", ondelete="CASCADE"), nullable=False)
    name = Column(JSON, nullable=False)
    price_delta_minor = Column(Integer, nullable=False, default=0, server_default="0")
    is_default = Column(Boolean, nullable=False, default=False, server_default="false")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    active = Column(Boolean, nullable=False, default=True, server_default="true")

    group = relationship("ProductOptionGroup", back_populates="options")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    public_code = Column(String, unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    room_number = Column(String, nullable=False)
    order_status = Column(String, default="NEW")
    payment_status = Column(String, default="UNPAID")
    # 'MANUAL'（人工转账 + 截图审核）或 'WALLET'（下单时用钱包余额抵扣）
    payment_method = Column(String, nullable=False, default="MANUAL", server_default="MANUAL")
    currency = Column(String, default="USD")
    customer_language = Column(String, nullable=False, default="en")
    total_minor = Column(Integer, nullable=False)
    # 金额构成（total = subtotal + delivery + service），对账与分店结算要用
    subtotal_minor = Column(Integer, nullable=True)
    delivery_fee_minor = Column(Integer, nullable=False, default=0, server_default="0")
    service_fee_minor = Column(Integer, nullable=False, default=0, server_default="0")
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

class ReportDelivery(Base):
    """每日报表的投递记录：保证「一天一份」不重复发。

    (report_key, chat_id) 上有唯一约束——调度器与外部 cron 都先抢占这一行，
    抢不到就说明当天已经发过，直接跳过。
    """
    __tablename__ = "report_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    report_key = Column(String, nullable=False)   # 例如 daily_sales:2026-10-01
    report_date = Column(Date, nullable=False)
    chat_id = Column(String, nullable=False)
    message_id = Column(String, nullable=True)
    payload = Column(JSON, nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StoreSettings(Base):
    __tablename__ = "store_settings"

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, default="USD")
    timezone = Column(String, default="Asia/Phnom_Penh")
    aba_qr_asset_key = Column(String, nullable=True)
    payment_link = Column(String, nullable=True)
    telegram_staff_group_id = Column(String, nullable=True)
    staff_group_language = Column(String, nullable=False, default="en")
    open_hours = Column(String, nullable=True)          # 旧的自由文本，已由 business_hours 取代
    delivery_mode = Column(String, nullable=True)
    # 经营参数：下单时服务端强制校验
    is_accepting_orders = Column(Boolean, nullable=False, default=True, server_default="true")
    business_hours = Column(JSON, nullable=False, default=list, server_default="[]")
    min_order_minor = Column(Integer, nullable=False, default=0, server_default="0")
    delivery_fee_minor = Column(Integer, nullable=False, default=0, server_default="0")
    service_fee_minor = Column(Integer, nullable=False, default=0, server_default="0")

