from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.task import TaskLog
from ..schemas.task import TaskLogResponse

router = APIRouter(prefix="/tasks", tags=["任务监控"])

@router.get("", response_model=List[TaskLogResponse], summary="获取最近执行任务日志")
def list_task_logs(
    limit: int = Query(50, ge=1, le=200),
    product_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(TaskLog)
    if product_id:
        query = query.filter(TaskLog.product_id == product_id)
    logs = query.order_by(TaskLog.id.desc()).limit(limit).all()
    return logs
