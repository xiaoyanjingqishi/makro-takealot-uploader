import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from pydantic import BaseModel
from ..database import get_db
from ..models.user import User
from ..models.store import Store
from ..models.setting import SystemSetting
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.makro_reprice_log import MakroRepriceLog
from ..schemas.piggyback import RepriceLogResponse
from ..services.auto_reprice_service import AutoRepriceService
from ..services.auto_reprice_scheduler import auto_reprice_scheduler
from ..services.task_manager import task_manager
from ..utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reprice", tags=["Makro 智能跟价系统"])

class BatchRepriceRequest(BaseModel):
    ids: Optional[List[int]] = None
    store_id: Optional[int] = None
    force: Optional[bool] = False

class FullCruiseRequest(BaseModel):
    store_id: Optional[int] = None
    concurrency: Optional[int] = None

@router.get("/logs")
def get_reprice_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store_id: Optional[int] = Query(None),
    seller_sku: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取自动跟价历史审计日志 (分页与多维筛选)
    """
    query = db.query(MakroRepriceLog)
    if store_id:
        query = query.filter(MakroRepriceLog.store_id == store_id)
    if seller_sku:
        query = query.filter(MakroRepriceLog.seller_sku.ilike(f"%{seller_sku.strip()}%"))
    if action:
        query = query.filter(MakroRepriceLog.action == action.strip().upper())

    total = query.count()
    logs = query.order_by(desc(MakroRepriceLog.created_at)).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for l in logs:
        items.append({
            "id": l.id,
            "piggyback_id": l.piggyback_id,
            "store_id": l.store_id,
            "seller_sku": l.seller_sku,
            "makro_product_id": l.makro_product_id,
            "competitor_seller": l.competitor_seller,
            "competitor_price": l.competitor_price,
            "old_price": l.old_price,
            "new_price": l.new_price,
            "action": l.action,
            "reason": l.reason,
            "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else None
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    }

@router.post("/trigger-item/{item_id}")
def trigger_single_item_reprice(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    立即对单件商品执行一次自动跟价巡检
    """
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到指定的跟品商品")

    res = AutoRepriceService.reprice_single_item(item, db, force=True)
    return {
        "success": res.get("status") == "SUCCESS",
        "result": res
    }

@router.post("/trigger-full-cruise")
def trigger_full_cruise_reprice(
    req: Optional[FullCruiseRequest] = None,
    store_id: Optional[int] = Query(None),
    concurrency: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    全量立刻发起后台平稳巡检巡航任务：
    抓取买家前台最新在售竞对与 Buybox 归属，对已开启自动跟价的在售商品执行智能调价，未开启的商品全面刷新 Buybox 状态并同步最新竞品情报。
    并发线程数优先读取系统全局设置中管理员配置的【跟品巡检并发线程数】。
    """
    target_store_id = None
    if req and req.store_id is not None:
        target_store_id = req.store_id
    elif isinstance(store_id, int):
        target_store_id = store_id

    # 优先从系统配置读取管理员自定义的并发线程数
    cfg_item = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_cruise_concurrency").first()
    system_concurrency = 3
    if cfg_item and cfg_item.value:
        try:
            system_concurrency = int(cfg_item.value)
        except Exception:
            pass

    # 若请求显式传入有效数值则优先采用，否则使用系统配置值 (支持管理员自定义 1~100)
    raw_concurrency = None
    if req and req.concurrency is not None and req.concurrency > 0:
        raw_concurrency = req.concurrency
    elif isinstance(concurrency, int) and concurrency > 0:
        raw_concurrency = concurrency

    chosen_concurrency = raw_concurrency if raw_concurrency is not None else system_concurrency
    target_concurrency = max(1, min(chosen_concurrency, 100))

    store_name = "全店铺"
    if target_store_id:
        store = db.query(Store).filter(Store.id == target_store_id).first()
        if not store:
            raise HTTPException(status_code=404, detail=f"指定的店铺 ID {target_store_id} 不存在")
        store_name = store.name

    # 查询目标在售已发布商品
    query = db.query(MakroPiggybackItem.id).filter(
        MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]),
        MakroPiggybackItem.makro_product_id.notlike("TEST_FSN_%")
    )
    if target_store_id:
        query = query.filter(MakroPiggybackItem.store_id == target_store_id)

    item_ids = [it[0] for it in query.all()]
    total = len(item_ids)

    if total == 0:
        return {
            "success": True,
            "message": f"【{store_name}】暂无符合巡检条件的在售跟品商品",
            "total": 0,
            "task_id": None
        }

    # 创建标准后台异步批处理任务 (在名称中显式标明执行线程数)
    task = task_manager.create_task(
        task_type="FULL_CRUISE_INSPECTION",
        name=f"全量巡检巡航 [{store_name}] ({total} 件 · {target_concurrency}线程)",
        total=total,
        product_ids=item_ids
    )
    task_id = task["id"]

    # 启动后台守护任务线程
    task_manager.start_task(
        task_id,
        lambda tm, tid: AutoRepriceService.run_full_cruise_task(
            tm=tm,
            task_id=tid,
            item_ids=item_ids,
            concurrency=target_concurrency
        )
    )

    return {
        "success": True,
        "message": f"已在后台启动【{store_name}】全量巡检巡航任务 (共 {total} 件)",
        "task_id": task_id,
        "total": total,
        "store_id": target_store_id,
        "store_name": store_name
    }

@router.post("/trigger-batch")
def trigger_batch_reprice(
    req: Optional[BatchRepriceRequest] = None,
    ids: Optional[List[int]] = Query(None),
    store_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    批量立即触发自动跟价 (支持多选商品 ID 或全店铺触发)
    """
    target_ids = (req.ids if req and req.ids else ids) or []
    target_store_id = (req.store_id if req and req.store_id is not None else store_id)

    if target_ids and len(target_ids) > 0:
        items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(target_ids)).all()
        results = []
        success_cnt = 0
        blocked_cnt = 0
        undercut_cnt = 0
        winning_hold_cnt = 0
        floor_cnt = 0
        failed_cnt = 0
        for it in items:
            r = AutoRepriceService.reprice_single_item(it, db, force=True)
            act = r.get("action")
            if r.get("status") == "SUCCESS":
                success_cnt += 1
            elif r.get("status") == "BLOCKED" or act == "BLOCKED":
                blocked_cnt += 1
            else:
                failed_cnt += 1

            if act == "UNDER_CUT":
                undercut_cnt += 1
            elif act == "WINNING_HOLD":
                winning_hold_cnt += 1
            elif act == "REACHED_FLOOR":
                floor_cnt += 1
            results.append(r)
        return {
            "total": len(items),
            "success_count": success_cnt,
            "blocked_count": blocked_cnt,
            "undercut_count": undercut_cnt,
            "winning_hold_count": winning_hold_cnt,
            "floor_count": floor_cnt,
            "failed_count": failed_cnt,
            "details": results
        }
    
    # 全店铺触发
    store_query = db.query(Store).filter(Store.is_active == True)
    if target_store_id:
        store_query = store_query.filter(Store.id == target_store_id)
    stores = store_query.all()

    summary = {
        "total_stores": len(stores),
        "total_items": 0,
        "success_count": 0,
        "blocked_count": 0,
        "undercut_count": 0,
        "winning_hold_count": 0,
        "floor_count": 0,
        "failed_count": 0
    }

    for s in stores:
        res = AutoRepriceService.run_reprice_for_store(s, db)
        summary["total_items"] += res.get("total_items", 0)
        summary["success_count"] += res.get("success_count", 0)
        summary["blocked_count"] += res.get("blocked_count", 0)
        summary["undercut_count"] += res.get("undercut_count", 0)
        summary["winning_hold_count"] += res.get("winning_hold_count", 0)
        summary["floor_count"] += res.get("floor_count", 0)
        summary["failed_count"] += res.get("failed_count", 0)

    return summary

@router.put("/config/{item_id}")
def update_reprice_config(
    item_id: int,
    auto_reprice: Optional[bool] = None,
    min_price_floor: Optional[float] = None,
    max_price_ceiling: Optional[float] = None,
    price_strategy: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    修改指定跟品的自动跟价开关与安全保护参数
    """
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="跟品商品不存在")

    if auto_reprice is not None:
        item.auto_reprice = auto_reprice
    if min_price_floor is not None:
        item.min_price_floor = max(float(min_price_floor), 0.0)
    if max_price_ceiling is not None:
        item.max_price_ceiling = max(float(max_price_ceiling), 0.0)
    if price_strategy is not None:
        item.price_strategy = price_strategy

    db.commit()
    db.refresh(item)
    return {
        "success": True,
        "item_id": item.id,
        "auto_reprice": item.auto_reprice,
        "min_price_floor": item.min_price_floor,
        "max_price_ceiling": item.max_price_ceiling,
        "price_strategy": item.price_strategy
    }

@router.get("/status")
def get_reprice_scheduler_status(
    current_user: User = Depends(get_current_user)
):
    """
    获取后台自动跟价调度引擎的运行状态
    """
    return {
        "running": auto_reprice_scheduler.running,
        "interval_minutes": auto_reprice_scheduler.interval_seconds // 60,
        "last_run_at": auto_reprice_scheduler.last_run_at.strftime("%Y-%m-%d %H:%M:%S") if auto_reprice_scheduler.last_run_at else None,
        "last_summary": auto_reprice_scheduler.last_summary
    }

@router.post("/scheduler/toggle")
def toggle_reprice_scheduler(
    enabled: bool,
    current_user: User = Depends(get_current_user)
):
    """
    手动启停后台跟价调度引擎
    """
    if enabled:
        auto_reprice_scheduler.start()
    else:
        auto_reprice_scheduler.stop()
    return {"running": auto_reprice_scheduler.running}
