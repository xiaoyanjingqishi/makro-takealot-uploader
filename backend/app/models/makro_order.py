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

    # 运营视角 7 阶履约状态: NEW, PURCHASED, SENT_TO_FORWARDER, AIR_FREIGHT, ARRIVED_SA, SA_PICKUP, COMPLETED, CANCELLED_RTO
    fulfillment_status = Column(String(50), default="NEW", index=True)
    fulfillment_updated_at = Column(DateTime, nullable=True)

    # 关联 PO 拼单发货批次
    po_id = Column(Integer, ForeignKey("makro_purchase_orders.id", ondelete="SET NULL"), nullable=True, index=True)

    # 成本核算与追溯 (RMB)
    purchase_cost = Column(Float, default=0.0)             # 国内采购价 (RMB)
    domestic_logistics_fee = Column(Float, default=0.0)    # 国内发往货代快递费 (RMB，已平摊)
    independent_head_haul = Column(Float, default=0.0)     # 单单独立头程费 (按重核算或自定义)
    total_weight_g = Column(Integer, default=0)            # 订单实测总克重 (g)
    is_weight_measured = Column(Boolean, default=False, index=True) # 是否已填写实测克重
    air_freight_rate = Column(Float, default=0.0)          # 单单独立空运单价 (元/kg，0为使用系统默认)

    # 采购与物流单号追溯 (支持多笔采购单: [{'sn': '...', 'cost': 25.0, 'tracking_no': '...', 'notes': '...'}])
    purchase_order_sn = Column(String(100), nullable=True) # 1688采购订单号 / 采购渠道单号 (主单号)
    purchase_notes = Column(String(255), nullable=True)    # 采购规格 / 供应商备注
    domestic_tracking_no = Column(String(100), nullable=True) # 国内寄往货代的快递单号
    procurement_records_json = Column(Text, nullable=True) # 多笔采购明细 JSON 数组

    # 履约发货来源与调拨追溯: DIRECT_PROCUREMENT (直采直发), SA_STOCK_ALLOCATION (南非现货调拨), DOMESTIC_STOCK_ALLOCATION (国内调拨)
    fulfillment_type = Column(String(50), default='DIRECT_PROCUREMENT', index=True)
    allocated_stock_id = Column(Integer, nullable=True, index=True) # 关联 makro_buffer_stocks.id
    reallocated_from_order_sn = Column(String(100), nullable=True, index=True) # 现货来源订单号

    # 打包与打单追踪 (40x30mm 标签)
    label_printed = Column(Boolean, default=False, index=True) # 是否已打印标签
    label_printed_at = Column(DateTime, nullable=True)         # 首次打单时间 (北京时间)

    synced_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("store_id", "order_id", name="uq_store_order"),
    )

    store = relationship("Store", back_populates="orders")
    purchase_order = relationship("MakroPurchaseOrder", back_populates="orders")

