from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroBufferStock(Base):
    """
    海外滞留现货与在途缓冲库存表
    记录因订单取消、退货、拒签而滞留在各履约环节的物理货品，支持快速匹配与转单履约
    """
    __tablename__ = "makro_buffer_stocks"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    # 商品基础标识
    sku_id = Column(String(100), nullable=False, index=True)
    fsn = Column(String(100), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    image_url = Column(String(500), nullable=True)
    quantity = Column(Integer, default=1)  # 物理可用件数

    # 物理滞留位置:
    # SA_WAREHOUSE (南非本地中转仓现货 - 价值最高)
    # IN_TRANSIT_AIR (国际空运在途截留 - 即将落地南非)
    # DOMESTIC_FORWARDER (国内货代/国内在途滞留 - 可退1688或国内转单)
    location = Column(String(50), default="SA_WAREHOUSE", index=True)

    # 资产状态:
    # AVAILABLE (闲置待调拨)
    # REALLOCATED (已调拨给新订单履约)
    # REFUNDED_1688 (已在1688申请退款退货)
    # SCRAPPED (报废/丢失/线下处理)
    status = Column(String(50), default="AVAILABLE", index=True)

    # 来源订单溯源
    source_order_id = Column(Integer, ForeignKey("makro_orders.id", ondelete="SET NULL"), nullable=True, index=True)
    source_order_sn = Column(String(100), nullable=True, index=True)
    source_po_id = Column(Integer, nullable=True)
    source_po_number = Column(String(100), nullable=True)

    # 调拨目标订单溯源
    reallocated_to_order_id = Column(Integer, ForeignKey("makro_orders.id", ondelete="SET NULL"), nullable=True, index=True)
    reallocated_to_order_sn = Column(String(100), nullable=True, index=True)
    reallocated_at = Column(DateTime, nullable=True)

    # 成本资产结转 (RMB)
    purchase_cost_cny = Column(Float, default=0.0)      # 原 1688 采购成本
    head_haul_cost_cny = Column(Float, default=0.0)     # 已发生的空运头程费用
    relabeling_fee_cny = Column(Float, default=0.0)     # 南非本地换标操作费 (默认 0.0)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    store = relationship("Store")
    source_order = relationship("MakroOrder", foreign_keys=[source_order_id])
    reallocated_order = relationship("MakroOrder", foreign_keys=[reallocated_to_order_id])
