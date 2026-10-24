from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class StoreProductReprice(Base):
    __tablename__ = "store_product_reprices"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    # 核心锚点：关联官方在线商品表 (1对1)
    listing_id = Column(Integer, ForeignKey("makro_listings.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    seller_sku = Column(String(100), nullable=False, index=True)
    makro_product_id = Column(String(100), nullable=False, index=True)  # FSN

    # 1. 商品来源标识
    # PIGGYBACK (跟品挂靠), SELF_PUBLISHED (选品箱自建上品), STORE_SYNCED (店铺历史同步老品)
    source_type = Column(String(50), default="PIGGYBACK", nullable=False, index=True)
    source_piggyback_id = Column(Integer, ForeignKey("makro_piggyback_items.id", ondelete="SET NULL"), nullable=True, index=True)

    # 竞品与巡检目标
    target_fsn = Column(String(100), nullable=True)        # 目标竞对 FSN
    target_makro_url = Column(String(500), nullable=True)  # 目标竞品前台链接

    # 跟价策略与防穿底线控制
    auto_reprice = Column(Boolean, default=True, index=True)  # 自动跟价开关
    price_strategy = Column(String(50), default="MINUS_1")    # MINUS_15, MINUS_1, PERCENT_2, MANUAL
    min_price_floor = Column(Float, default=0.0)             # 保本底价 (必填风控)
    max_price_ceiling = Column(Float, default=0.0)           # 最高保护价

    # 实时巡检状态与 Buybox 情报
    buybox_status = Column(String(50), default="UNKNOWN", index=True)  # WINNING, LOSING, FLOOR_HIT, NO_COMPETITOR, BLOCKED, UNKNOWN
    current_competitor_price = Column(Float, nullable=True)           # 最近抓取的竞对售价
    current_competitor_seller = Column(String(100), nullable=True)    # 最近占位竞对店名
    seller_count = Column(Integer, default=1)                         # 在售竞对商家总数

    # 2. 侵权与合规长效复检状态
    # SAFE (合规安全), RISK (黄线风险), PROHIBITED (红线禁售), PENDING_CHECK (待检测)
    compliance_status = Column(String(50), default="PENDING_CHECK", index=True)
    compliance_details = Column(Text, nullable=True)                  # JSON: 审查模型、判定理由与命中关键词
    last_compliance_check_at = Column(DateTime, nullable=True)        # 最近一次合规复检时间

    # 监控生命周期状态
    # ACTIVE (监控中), PAUSED (已暂停), OFFLINE_SUSPENDED (商品已下架/停更), PENDING_CONFIG (待补齐竞对), BLOCKED (侵权熔断)
    monitor_status = Column(String(50), default="ACTIVE", index=True)

    last_reprice_at = Column(DateTime, nullable=True)                 # 最近一次巡检时间
    last_reprice_result = Column(String(255), nullable=True)          # 最近一次巡检摘要

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关系映射
    store = relationship("Store")
    listing = relationship("MakroListing", backref="reprice_monitor")
    source_piggyback = relationship("MakroPiggybackItem")
