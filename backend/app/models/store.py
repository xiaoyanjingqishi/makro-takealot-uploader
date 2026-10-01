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
    default_location_id = Column(String(100), nullable=True)  # 默认仓库 Location ID (如 LOC8fe01...)
    default_brand = Column(String(100), default="Beishi")  # 店铺默认上架品牌
    is_active = Column(Boolean, default=True)  # 是否启用
    is_default = Column(Boolean, default=False)  # 是否为默认店铺
    notes = Column(String(255), nullable=True)  # 备注说明
    
    # 自动化登录凭据与多邮箱配置
    login_email = Column(String(150), nullable=True)  # Makro 登录邮箱
    login_password = Column(String(150), nullable=True)  # Makro 登录密码
    imap_server = Column(String(100), nullable=True)  # IMAP 服务器地址 (如 imap.gmail.com, imap.163.com)
    imap_port = Column(Integer, default=993)  # IMAP 端口 (默认 993)
    imap_user = Column(String(150), nullable=True)  # IMAP 邮箱账号 (若空则默认同 login_email)
    imap_password = Column(String(150), nullable=True)  # 邮箱应用专用密码 / 客户端授权码
    last_auto_login_at = Column(DateTime, nullable=True)  # 上次全自动保活登录时间
    last_auto_login_status = Column(String(255), nullable=True)  # 上次全自动保活登录状态 (SUCCESS / FAILED: ...)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    authorized_users = relationship("User", secondary="user_stores", back_populates="authorized_stores")
    listings = relationship("ProductStoreListing", back_populates="store", cascade="all, delete-orphan")
    online_listings = relationship("MakroListing", back_populates="store", cascade="all, delete-orphan")
    orders = relationship("MakroOrder", back_populates="store", cascade="all, delete-orphan")

class ProductStoreListing(Base):
    __tablename__ = "product_store_listings"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    status = Column(String(50), default="PENDING", index=True)  # PENDING, SUBMITTED, ACTIVE, FAILED
    brand = Column(String(100), nullable=True)  # 实际刊登品牌
    makro_sku_id = Column(String(100), nullable=True)
    makro_request_id = Column(String(100), nullable=True)
    makro_submit_error = Column(Text, nullable=True)
    selling_price = Column(Float, nullable=True)
    mrp = Column(Float, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint("product_id", "store_id", name="uq_product_store"),
    )

    product = relationship("Product", back_populates="store_listings")
    store = relationship("Store", back_populates="listings")
