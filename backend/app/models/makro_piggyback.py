from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroPiggybackItem(Base):
    __tablename__ = "makro_piggyback_items"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Makro 目标原商品信息 (来自 searchProduct / 前台链接)
    makro_product_id = Column(String(100), nullable=False, index=True)  # FSN (如 GSPHPVTNMFHDAWV4)
    item_id = Column(String(100), nullable=True)                        # 目录 Item ID (如 itmdda5c11c09523)
    makro_url = Column(String(500), nullable=True)
    title = Column(String(500), nullable=False)
    title_zh = Column(String(500), nullable=True)  # 智能中文翻译
    brand = Column(String(100), nullable=True)
    vertical = Column(String(100), nullable=True)
    image_url = Column(Text, nullable=True)
    barcode = Column(String(100), nullable=True)
    model_number = Column(String(200), nullable=True)

    # 竞品与原链接价格及跟卖情报
    original_price = Column(Float, default=0.0)      # 原链接当前在售售价 (SSP)
    original_mrp = Column(Float, default=0.0)        # 原链接划线零售价 (MRP)
    original_seller = Column(String(100), nullable=True) # 当前 Buybox 占位卖家名称
    seller_count = Column(Integer, default=1)        # 当前在售/跟卖商家总数

    # 本店跟品设定
    seller_sku = Column(String(100), nullable=False, index=True)  # 本店自定义 SKU (如 GP2026100101)
    target_price = Column(Float, default=0.0)       # 本店跟品售价
    target_mrp = Column(Float, default=0.0)         # 本店跟品划线价
    min_price_floor = Column(Float, default=0.0)    # 保本底价
    price_strategy = Column(String(50), default="MINUS_1") # MINUS_1, PERCENT_2, MANUAL
    inventory = Column(Integer, default=99)         # 默认上架库存
    lead_time_days = Column(Integer, default=14)    # 发货时效 (SLA，官方默认 14 天)
    # 变体规格与多属性管理
    variant_attributes = Column(Text, nullable=True)   # JSON: {"Packaging Type": "Pack of 10"}
    variant_name = Column(String(200), nullable=True)  # 变体展示名 (如 "Pack of 10")

    # 包装与物流规格 (官方挂靠必须)
    weight = Column(Float, default=0.5)
    length = Column(Float, default=15.0)
    breadth = Column(Float, default=10.0)
    height = Column(Float, default=5.0)

    # 自动跟价控制
    auto_reprice = Column(Boolean, default=True)       # 是否开启自动跟价
    max_price_ceiling = Column(Float, default=0.0)     # 最高保护价
    last_reprice_at = Column(DateTime, nullable=True)  # 最近一次跟价巡检时间
    last_reprice_result = Column(String(200), nullable=True) # 最近一次跟价巡检执行摘要
    buybox_status = Column(String(50), default="UNKNOWN", index=True) # WINNING, LOSING, FLOOR_HIT, NO_COMPETITOR, UNKNOWN
    last_competitor_price = Column(Float, nullable=True) # 最近一次竞对报价

    # AI 侵权与合规检测状态与详情
    # PENDING_CHECK (待检测), SAFE (合规安全), RISK (黄线风险), PROHIBITED (红线禁售), DISPUTED (分歧待仲裁)
    compliance_status = Column(String(50), default="PENDING_CHECK", index=True)
    compliance_details = Column(Text, nullable=True)  # JSON 格式存储双 AI 审查原因与细节

    # 跟品发布状态: PENDING (待提交), SUBMITTING (提交中), ACTIVE (跟品成功), FAILED (跟品失败)
    status = Column(String(50), default="PENDING", index=True)
    makro_listing_id = Column(String(100), nullable=True) # 挂靠成功后的 Listing ID (如 LSTGSPHPVTN...)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    store = relationship("Store")
    creator = relationship("User")
    user = relationship("User", overlaps="creator")
