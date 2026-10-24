from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroPurchaseOrder(Base):
    __tablename__ = "makro_purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String(100), unique=True, nullable=False, index=True)  # 如 PO20261014-001
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(200), nullable=True)  # 批次名称/备注，如 "10月14日南非空运第1批"
    status = Column(String(50), default="PLANNING", index=True)  # PLANNING, PURCHASED, SHIPPED, COMPLETED

    # 货代与空运追踪
    forwarder_name = Column(String(100), nullable=True)  # 货代公司名称 (如: 顺丰国际, 易达, 速派)
    air_waybill_no = Column(String(100), nullable=True, index=True)  # 货代空运主提单号 / 批次单号

    # 批次费用 (元)
    domestic_logistics_fee = Column(Float, default=0.0)  # PO国内快递寄往货代运费 (RMB)
    head_haul_fee = Column(Float, default=0.0)       # 头程物流运费 (RMB)
    logistics_loss_fee = Column(Float, default=0.0)  # 物流损耗费 (RMB)

    # 包装箱规尺寸与实测整箱重量
    box_length_cm = Column(Float, default=0.0)       # 外箱长 (cm)
    box_width_cm = Column(Float, default=0.0)        # 外箱宽 (cm)
    box_height_cm = Column(Float, default=0.0)       # 外箱高 (cm)
    package_weight_kg = Column(Float, default=0.0)   # 打包实测整箱毛重 (kg)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    store = relationship("Store")
    orders = relationship("MakroOrder", back_populates="purchase_order")
