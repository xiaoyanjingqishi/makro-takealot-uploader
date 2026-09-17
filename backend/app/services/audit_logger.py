import json
import logging
from datetime import datetime
from typing import Optional, Any
from sqlalchemy.orm import Session
from ..models.task import TaskLog
from ..database import SessionLocal

logger = logging.getLogger("audit_logger")

def record_audit_log(
    task_type: str,
    status: str = "SUCCESS",
    message: str = "",
    product_id: Optional[int] = None,
    request_id: Optional[str] = None,
    detail_logs: Optional[Any] = None,
    db: Optional[Session] = None
) -> Optional[TaskLog]:
    """
    统一记录全系统操作日志
    :param task_type: COLLECT, CLEAN, BATCH_CLEAN, COMPLIANCE, BATCH_COMPLIANCE, BATCH_PRICE, BATCH_DELETE, SUBMIT_LISTING, BATCH_PUBLISH, SETTINGS_UPDATE
    :param status: SUCCESS, FAILED, RUNNING, WARNING
    :param message: 操作描述
    :param product_id: 关联商品 ID
    :param request_id: 关联 Makro RequestId 或其他凭据
    :param detail_logs: 详细数据回执 (dict, list 或 str)
    :param db: 外部传入的 DB Session，若无则自动创建独立连接
    """
    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        detail_str = None
        if detail_logs is not None:
            if isinstance(detail_logs, (dict, list)):
                detail_str = json.dumps(detail_logs, ensure_ascii=False)
            else:
                detail_str = str(detail_logs)

        now = datetime.now()
        log_entry = TaskLog(
            product_id=product_id,
            task_type=task_type,
            status=status,
            request_id=request_id,
            message=(message or "")[:500],
            detail_logs=detail_str,
            created_at=now,
            finished_at=now if status in ["SUCCESS", "FAILED"] else None
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        return log_entry
    except Exception as e:
        logger.error(f"记录操作日志失败: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        if close_session:
            db.close()
