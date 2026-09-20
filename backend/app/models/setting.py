from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from ..database import Base

class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True, nullable=False)
    value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
