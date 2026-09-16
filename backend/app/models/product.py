from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    takealot_id = Column(String(100), index=True, nullable=True)
    takealot_url = Column(String(500), nullable=True)
    takealot_title = Column(String(500), nullable=False)
    takealot_price = Column(Float, default=0.0)
    takealot_brand = Column(String(100), nullable=True)
    takealot_category = Column(String(255), nullable=True)
    takealot_description = Column(Text, nullable=True)
    takealot_specs = Column(Text, nullable=True)  # JSON 字符串
    raw_images = Column(Text, nullable=True)      # JSON 字符串 (URL 列表)

    # 状态: PENDING_CLEAN, CLEANED, SUBMITTED, QC_PENDING, ACTIVE, FAILED
    status = Column(String(50), default="PENDING_CLEAN", index=True)

    # AI 清洗与规范化后的 Makro 属性
    makro_vertical = Column(String(100), default="bath_towel", index=True)
    makro_title = Column(String(500), nullable=True)
    makro_description = Column(Text, nullable=True)
    makro_brand = Column(String(100), default="Beishi")
    makro_selling_price = Column(Float, default=0.0)
    makro_mrp = Column(Float, default=0.0)
    
    # 结构化字段 (JSON 文本存储)
    makro_catalog_attributes = Column(Text, nullable=True)  # 16+ 项类目属性
    makro_listing_attributes = Column(Text, nullable=True)  # 销售属性
    makro_package_dimensions = Column(Text, nullable=True)  # 包装长宽高重量
    makro_images = Column(Text, nullable=True)              # 上传至 Makro CDN 后的图片 URL 映射
    
    # 变体分组与平台凭据
    group_code = Column(String(100), index=True, nullable=True)
    makro_request_id = Column(String(100), nullable=True)
    makro_submit_error = Column(Text, nullable=True)

    # AI 侵权与合规检测: PENDING_CHECK, SAFE, RISK, PROHIBITED
    compliance_status = Column(String(50), default="PENDING_CHECK", index=True)
    compliance_details = Column(Text, nullable=True)  # 存储结构化检测结果与首图分析 JSON

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联变体
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    sku_id = Column(String(100), index=True, nullable=False)
    takealot_variant_id = Column(String(100), nullable=True)
    
    # 变体专属标题与特定规格 (支持 1TB/2TB容量、包装数量、线长尺寸等)
    variant_title = Column(String(500), nullable=True)
    variant_attributes = Column(Text, nullable=True) # JSON: {"capacity": "1TB", "colour": "Gold"}
    specs = Column(Text, nullable=True) # JSON: {"Device Storage Capacity": "1000GB"}
    barcode = Column(String(100), nullable=True)
    
    # 变体通用维度
    size = Column(String(50), nullable=True)
    colour = Column(String(50), nullable=True)
    brand_colour = Column(String(50), nullable=True)
    pack_of = Column(String(50), default="1")
    
    # 定价
    takealot_price = Column(Float, default=0.0)
    makro_selling_price = Column(Float, default=0.0)
    makro_mrp = Column(Float, default=0.0)

    # 变体图片列表 (JSON 文本)
    images = Column(Text, nullable=True)
    makro_image_urls = Column(Text, nullable=True)
    
    # 上品状态
    status = Column(String(50), default="PENDING")
    makro_sku_id = Column(String(100), nullable=True)
    makro_request_id = Column(String(100), nullable=True)
    makro_submit_error = Column(Text, nullable=True)

    product = relationship("Product", back_populates="variants")

