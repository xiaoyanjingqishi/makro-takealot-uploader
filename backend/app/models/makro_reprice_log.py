from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroRepriceLog(Base):
    __tablename__ = "makro_reprice_logs"

    id = Column(Integer, primary_key=True, index=True)
    piggyback_id = Column(Integer, ForeignKey("makro_piggyback_items.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_sku = Column(String(100), nullable=False, index=True)
    makro_product_id = Column(String(100), nullable=False, index=True) # FSN

    competitor_seller = Column(String(100), nullable=True) # 占位竞对卖家
    competitor_price = Column(Float, default=0.0)          # 竞对当前价
    old_price = Column(Float, default=0.0)                 # 本店调前售价
    new_price = Column(Float, default=0.0)                 # 本店调后售价
    
    # UNDER_CUT (降价跟进), REACHED_FLOOR (触底保本锁死), WINNING_HOLD (自己胜出维持), FAILED (执行异常), NO_CHANGE (无变动)
    action = Column(String(50), nullable=False, index=True)
    reason = Column(Text, nullable=True)                   # 详细调价原因

    created_at = Column(DateTime, default=datetime.now, index=True)

    piggyback_item = relationship("MakroPiggybackItem")
    store = relationship("Store")
