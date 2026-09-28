from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroAuditListing(Base):
    __tablename__ = "makro_audit_listings"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_id = Column(String(100), nullable=False, index=True)

    sku_id = Column(String(100), nullable=True, index=True)
    request_id = Column(String(100), nullable=False, index=True)  # REQ ID
    txn_id = Column(String(100), nullable=True)                  # TXN ID
    vertical = Column(String(100), nullable=True, index=True)     # 类目代码
    vertical_display_name = Column(String(100), nullable=True)

    # 审核状态 (业务标准化): QC_IN_PROGRESS, QC_PASSED, DRAFT, QC_FAILED
    state = Column(String(50), nullable=False, index=True)
    raw_state = Column(String(100), nullable=True)               # 官方原生状态代码 (如 FSN_CREATION_COMPLETED)

    # 商品基础属性
    title = Column(String(500), nullable=True)
    brand = Column(String(100), nullable=True)
    image_url = Column(Text, nullable=True)
    fsp = Column(Float, default=0.0)  # Flipkart Selling Price
    mrp = Column(Float, default=0.0)  # Max Retail Price

    # 错误与驳回诊断
    has_errors = Column(Boolean, default=False, index=True)
    error_summary = Column(Text, nullable=True)  # 格式化后的简要中文错误标签 JSON 列表
    error_details = Column(Text, nullable=True)  # 官方原始完整错误详情 JSON
    delete_allowed = Column(Boolean, default=False)

    # 官方时间戳 (毫秒转换)
    official_created_on = Column(BigInteger, nullable=True)
    official_last_modified = Column(BigInteger, nullable=True)

    # 本地选品箱双向关联 (根据 sku_id 或对应关系反查)
    local_product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)

    synced_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("store_id", "request_id", "sku_id", name="uq_audit_store_req_sku"),
    )

    store = relationship("Store")
    local_product = relationship("Product")
