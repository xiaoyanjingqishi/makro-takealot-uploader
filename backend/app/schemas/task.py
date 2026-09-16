from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TaskLogResponse(BaseModel):
    id: int
    product_id: Optional[int] = None
    task_type: str
    status: str
    request_id: Optional[str] = None
    message: Optional[str] = None
    detail_logs: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True
