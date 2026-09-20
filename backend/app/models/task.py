from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from datetime import datetime
from ..database import Base

class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    task_type = Column(String(50), nullable=False)  # CLEAN, UPLOAD_IMAGES, SUBMIT_LISTING
    status = Column(String(50), default="RUNNING")  # RUNNING, SUCCESS, FAILED
    request_id = Column(String(100), nullable=True)
    message = Column(String(500), nullable=True)
    detail_logs = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)
