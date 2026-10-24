from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base

class MakroOrderStatusLog(Base):
    """
    订单状态流转与操作审计历史日志表
    记录每次状态变更的前后对比、操作人、备注及具体北京时间
    """
    __tablename__ = "makro_order_status_logs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("makro_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    order_sn = Column(String(100), nullable=False, index=True)  # 订单号冗余，加速检索与排查
    change_type = Column(String(50), default="FULFILLMENT", index=True)  # FULFILLMENT, OFFICIAL_STATUS, PO_ASSIGN, WEIGHT_RECORD
    from_status = Column(String(100), nullable=True)
    to_status = Column(String(100), nullable=False)
    operator = Column(String(100), default="SYSTEM")  # 操作人账号或系统进程
    note = Column(String(255), nullable=True)         # 变更说明 (如 PO 批次号、异常退回原因等)
    created_at = Column(DateTime, default=datetime.now, index=True)  # 发生时的具体北京时间

    @property
    def old_value(self):
        return self.from_status

    @property
    def new_value(self):
        return self.to_status

    @property
    def notes(self):
        return self.note

    def __init__(self, **kwargs):
        if "old_value" in kwargs and "from_status" not in kwargs:
            kwargs["from_status"] = kwargs.pop("old_value")
        if "new_value" in kwargs and "to_status" not in kwargs:
            kwargs["to_status"] = kwargs.pop("new_value")
        if "notes" in kwargs and "note" not in kwargs:
            kwargs["note"] = kwargs.pop("notes")
        super().__init__(**kwargs)
