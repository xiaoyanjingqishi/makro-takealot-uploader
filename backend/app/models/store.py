from sqlalchemy import Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # 店铺名称，如 "Makro 旗舰店"
    seller_id = Column(String(100), nullable=False, index=True)  # Makro Seller ID
    fk_csrf_token = Column(String(255), nullable=True)  # CSRF Token
    cookie = Column(Text, nullable=True)  # 登录态 Cookie
    default_brand = Column(String(100), default="Beishi")  # 店铺默认上架品牌
    is_active = Column(Boolean, default=True)  # 是否启用
    is_default = Column(Boolean, default=False)  # 是否为默认店铺
    notes = Column(String(255), nullable=True)  # 备注说明
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    listings = relationship("ProductStoreListing", back_populates="store", cascade="all, delete-orphan")

class ProductStoreListing(Base):
    __tablename__ = "product_store_listings"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(50), default="PENDING", index=True)  # PENDING, SUBMITTED, ACTIVE, FAILED
    makro_sku_id = Column(String(100), nullable=True)
    makro_request_id = Column(String(100), nullable=True)
    makro_submit_error = Column(Text, nullable=True)
    selling_price = Column(Float, nullable=True)
    mrp = Column(Float, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("product_id", "store_id", name="uq_product_store"),
    )

    product = relationship("Product", back_populates="store_listings")
    store = relationship("Store", back_populates="listings")
