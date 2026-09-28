from sqlalchemy import Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroOrder(Base):
    __tablename__ = "makro_orders"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_id = Column(String(100), nullable=False, index=True)

    order_id = Column(String(100), nullable=False, index=True)  # 如 OD438479535860214100
    shipment_id = Column(String(100), nullable=True, index=True)  # 如 3aeeeec8-ebbb-4f4f-ae81-60b6cb1330a0

    # 状态: upcoming, processing, pending_labels, rtd, pending_handover, in_transit, completed, cancelled
    status = Column(String(50), nullable=False, index=True)
    service_profile = Column(String(50), default="NON_FBF")
    payment_type = Column(String(50), default="prepaid")

    # 金额
    total_amount = Column(Float, default=0.0)
    currency = Column(String(10), default="ZAR")

    # 买家收件信息
    buyer_name = Column(String(200), nullable=True)
    buyer_phone = Column(String(50), nullable=True)
    shipping_city = Column(String(100), nullable=True)
    shipping_state = Column(String(100), nullable=True)
    shipping_pincode = Column(String(50), nullable=True)
    shipping_address_line1 = Column(String(255), nullable=True)
    shipping_address_full = Column(Text, nullable=True)  # JSON 完整地址快照

    # 物流与追踪
    tracking_id = Column(String(100), nullable=True, index=True)
    courier_name = Column(String(100), nullable=True)  # 如 DPD-Laser
    delivery_vendor = Column(String(100), nullable=True)
    pickup_vendor = Column(String(100), nullable=True)

    # 履约时效 SLA 监控
    order_date = Column(DateTime, nullable=True, index=True)
    dispatch_by_date = Column(DateTime, nullable=True)  # 承诺发货截止时间
    delivered_date = Column(DateTime, nullable=True, index=True)  # 买家签收送达时间
    is_sla_breached = Column(Boolean, default=False, index=True)  # 是否超时

    # 关联商品明细 (JSON 存储 order_items 与 tracking 快照)
    raw_items_json = Column(Text, nullable=True)
    raw_payload_json = Column(Text, nullable=True)

    synced_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("store_id", "order_id", name="uq_store_order"),
    )

    store = relationship("Store", back_populates="orders")
