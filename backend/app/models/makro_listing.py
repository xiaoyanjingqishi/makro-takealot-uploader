from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroListing(Base):
    __tablename__ = "makro_listings"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_id = Column(String(100), nullable=False, index=True)

    sku_id = Column(String(100), nullable=False, index=True)  # 卖家 SKU，如 H202607232134
    product_id = Column(String(100), nullable=True, index=True)  # FSN，如 PMPHAF9GAFSFWMHM
    listing_id = Column(String(100), nullable=True, index=True)  # LSTPMPHAF...

    title = Column(String(500), nullable=True)
    brand = Column(String(100), nullable=True)
    vertical = Column(String(100), nullable=True)
    vertical_display_name = Column(String(100), nullable=True)
    image_url = Column(Text, nullable=True)

    # ACTIVE, INACTIVE, READY_FOR_ACTIVATION, INACTIVATED_BY_FLIPKART, ARCHIVED
    internal_state = Column(String(50), nullable=False, index=True)
    ssp = Column(Float, default=0.0)  # Selling Price
    mrp = Column(Float, default=0.0)  # Max Retail Price
    inventory = Column(Integer, default=0)  # 当前库存数量

    # 包装与物流规格
    length = Column(Float, nullable=True)
    breadth = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)

    # 驳回或下架原因 (JSON 存储)
    deactivation_reasons = Column(Text, nullable=True)
    archival_reasons = Column(Text, nullable=True)

    # 本地选品双向关联 (若本地选品箱有此 SKU)
    local_product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)

    synced_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("store_id", "sku_id", name="uq_store_sku"),
    )

    store = relationship("Store", back_populates="online_listings")
    local_product = relationship("Product", back_populates="online_listings")
