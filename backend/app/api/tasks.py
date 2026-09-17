from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.task import TaskLog
from ..schemas.task import TaskLogResponse
from ..services.task_manager import task_manager

router = APIRouter(prefix="/tasks", tags=["任务监控与操作日志"])

@router.get("/active", summary="获取当前运行中或刚结束的所有后台任务 (供刷新恢复及多任务并发监控)")
def get_active_tasks() -> Dict[str, Any]:
    tasks = task_manager.get_active_tasks()
    return {
        "tasks": tasks,
        "task": tasks[-1] if tasks else None
    }

@router.get("/{task_id}/status", summary="查询特定后台任务实时状态与进度")
def get_task_status(task_id: str) -> Dict[str, Any]:
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已被清理")
    return {"task": task}

@router.post("/{task_id}/cancel", summary="取消运行中的后台任务")
def cancel_task(task_id: str) -> Dict[str, Any]:
    ok = task_manager.cancel_task(task_id)
    return {
        "success": ok,
        "message": "已向后台任务发送终止指令" if ok else "任务不存在或不在运行状态"
    }

@router.get("", response_model=List[TaskLogResponse], summary="获取系统所有操作与执行任务日志")
def list_task_logs(
    limit: int = Query(100, ge=1, le=500),
    task_type: Optional[str] = Query(None, description="任务/操作类型: COLLECT, CLEAN, BATCH_CLEAN, COMPLIANCE, BATCH_COMPLIANCE, BATCH_PRICE, BATCH_DELETE, SUBMIT_LISTING, BATCH_PUBLISH"),
    status: Optional[str] = Query(None, description="状态: RUNNING, SUCCESS, FAILED, CANCELLED"),
    product_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, description="搜索描述或凭据"),
    db: Session = Depends(get_db)
):
    query = db.query(TaskLog)
    if task_type:
        query = query.filter(TaskLog.task_type == task_type)
    if status:
        query = query.filter(TaskLog.status == status)
    if product_id:
        query = query.filter(TaskLog.product_id == product_id)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            TaskLog.message.ilike(s) |
            TaskLog.request_id.ilike(s) |
            TaskLog.task_type.ilike(s)
        )
    logs = query.order_by(TaskLog.id.desc()).limit(limit).all()
    return logs
