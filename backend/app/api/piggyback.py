import uuid
import time
import json
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime

from ..database import get_db
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.store import Store
from ..models.user import User
from ..schemas.piggyback import (
    CollectPiggybackRequest,
    BatchCollectPiggybackRequest,
    PiggybackItemUpdate,
    BatchApplyPricingRequest,
    BatchCheckComplianceRequest,
    BatchPublishPiggybackRequest,
    BatchDeletePiggybackRequest,
    PiggybackItemResponse
)
from ..services.makro_scraper_service import MakroScraperService
from ..services.makro_piggyback_service import MakroPiggybackService
from ..services.task_manager import task_manager, TaskManager
from ..utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/piggyback", tags=["Makro跟品与合规挂靠"])

def _generate_piggyback_sku() -> str:
    """自动生成规范且不重复的跟品 SKU，如 GP2609301234"""
    ts = datetime.now().strftime("%y%m%d%H%M%S")
    rand_suffix = str(uuid.uuid4().hex[:4]).upper()
    return f"GP{ts}{rand_suffix}"

def _get_target_store(db: Session, store_id: Optional[int] = None) -> Store:
    if store_id:
        store = db.query(Store).filter(Store.id == store_id).first()
        if store:
            return store
    # 默认取 active 且 default 的店铺，或首个店铺
    store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()
    if not store:
        store = db.query(Store).filter(Store.is_active == True).first()
    if not store:
        raise HTTPException(status_code=400, detail="系统未找到任何可用店铺，请先在多店铺管理中添加店铺凭据。")
    return store

def _format_piggyback_item(item: MakroPiggybackItem) -> dict:
    comp_details = None
    if item.compliance_details:
        try:
            comp_details = json.loads(item.compliance_details)
        except Exception:
            pass

    return {
        "id": item.id,
        "store_id": item.store_id,
        "store_name": item.store.name if item.store else "未知店铺",
        "user_id": item.user_id,
        "makro_product_id": item.makro_product_id,
        "item_id": item.item_id,
        "makro_url": item.makro_url or MakroScraperService.format_canonical_makro_url(item.makro_product_id, item.item_id),
        "title": item.title,
        "title_zh": item.title_zh,
        "brand": item.brand,
        "vertical": item.vertical,
        "image_url": item.image_url,
        "barcode": item.barcode,
        "model_number": item.model_number,
        "original_price": item.original_price or 0.0,
        "original_mrp": item.original_mrp or 0.0,
        "original_seller": item.original_seller or "",
        "seller_count": item.seller_count or 1,
        "seller_sku": item.seller_sku,
        "target_price": item.target_price or 0.0,
        "target_mrp": item.target_mrp or 0.0,
        "min_price_floor": item.min_price_floor or 0.0,
        "price_strategy": item.price_strategy or "MINUS_1",
        "inventory": item.inventory or 99,
        "lead_time_days": item.lead_time_days or 14,
        "weight": item.weight or 0.5,
        "length": item.length or 15.0,
        "breadth": item.breadth or 10.0,
        "height": item.height or 5.0,
        "compliance_status": item.compliance_status or "PENDING_CHECK",
        "compliance_details": comp_details,
        "status": item.status or "PENDING",
        "makro_listing_id": item.makro_listing_id,
        "error_message": item.error_message,
        "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else None,
        "updated_at": item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else None
    }

@router.post("/collect", summary="采集单个 Makro 商品并加入跟品池")
def collect_single_piggyback(
    req: CollectPiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _get_target_store(db, req.store_id)
    client_data = {
        "item_id": req.item_id,
        "title": req.title,
        "price": req.price,
        "mrp": req.mrp,
        "image_url": req.image_url,
        "seller_name": req.seller_name,
        "seller_count": req.seller_count,
        "fsn": req.url_or_fsn
    }
    try:
        data = MakroScraperService.resolve_piggyback_product(req.url_or_fsn, store, client_data=client_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    fsn = data["makro_product_id"]
    item_id = data.get("item_id")

    # 检查是否已在当前店铺的跟品池中
    existing = db.query(MakroPiggybackItem).filter(
        MakroPiggybackItem.store_id == store.id,
        MakroPiggybackItem.makro_product_id == fsn
    ).first()

    target_p, target_m = MakroPiggybackService.calculate_price(
        original_price=data.get("original_price", 0.0),
        strategy=req.price_strategy or "MINUS_1",
        min_floor=req.min_price_floor or 0.0,
        original_mrp=data.get("original_mrp", 0.0)
    )

    if existing:
        # 更新参数
        existing.item_id = item_id or existing.item_id
        existing.makro_url = data.get("makro_url", existing.makro_url)
        existing.title = data.get("title", existing.title)
        existing.title_zh = data.get("title_zh", existing.title_zh)
        existing.brand = data.get("brand", existing.brand)
        existing.vertical = data.get("vertical", existing.vertical)
        existing.image_url = data.get("image_url", existing.image_url)
        existing.original_price = data.get("original_price", existing.original_price)
        existing.original_mrp = data.get("original_mrp", existing.original_mrp)
        existing.original_seller = data.get("original_seller", existing.original_seller)
        existing.seller_count = data.get("seller_count", existing.seller_count or 1)
        existing.target_price = target_p
        existing.target_mrp = target_m
        existing.min_price_floor = req.min_price_floor or existing.min_price_floor
        existing.price_strategy = req.price_strategy or existing.price_strategy
        item = existing
    else:
        sku = _generate_piggyback_sku()
        item = MakroPiggybackItem(
            store_id=store.id,
            user_id=current_user.id if current_user else None,
            makro_product_id=fsn,
            item_id=item_id,
            makro_url=data.get("makro_url"),
            title=data.get("title"),
            title_zh=data.get("title_zh"),
            brand=data.get("brand"),
            vertical=data.get("vertical"),
            image_url=data.get("image_url"),
            model_number=data.get("model_number"),
            barcode=data.get("barcode"),
            original_price=data.get("original_price", 0.0),
            original_mrp=data.get("original_mrp", 0.0),
            original_seller=data.get("original_seller"),
            seller_count=data.get("seller_count", 1),
            seller_sku=sku,
            target_price=target_p,
            target_mrp=target_m,
            min_price_floor=req.min_price_floor or 0.0,
            price_strategy=req.price_strategy or "MINUS_1",
            inventory=99,
            lead_time_days=14,
            location_id=store.default_location_id,
            compliance_status="PENDING_CHECK",
            status="PENDING"
        )
        db.add(item)

    db.commit()
    db.refresh(item)

    # 检查是否执行自动 AI 合规检测 (默认不自动检测，借鉴选品箱模式手动批量触发)
    should_auto_compliance = False
    if req.auto_compliance is not None:
        should_auto_compliance = req.auto_compliance
    else:
        from ..models.setting import SystemSetting
        setting_rec = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_auto_compliance").first()
        if setting_rec and setting_rec.value:
            should_auto_compliance = str(setting_rec.value).lower() in ["true", "1", "yes"]

    if should_auto_compliance:
        try:
            MakroPiggybackService.check_compliance_for_item(item, db)
        except Exception as comp_err:
            logger.warning(f"采集后自动合规检测跳过: {comp_err}")

    return {
        "success": True,
        "message": f"成功采集商品「{item.title[:30]}...」入库",
        "item": _format_piggyback_item(item)
    }

@router.post("/batch-collect", summary="批量采集 Makro 链接或 FSN 入库")
def batch_collect_piggyback(
    req: BatchCollectPiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _get_target_store(db, req.store_id)
    success_count = 0
    failed_items = []

    for raw in req.items:
        clean_text = raw.strip()
        if not clean_text:
            continue
        try:
            data = MakroScraperService.resolve_piggyback_product(clean_text, store)
            fsn = data["makro_product_id"]
            item_id = data.get("item_id")

            target_p, target_m = MakroPiggybackService.calculate_price(
                original_price=data.get("original_price", 0.0),
                strategy=req.price_strategy or "MINUS_1",
                min_floor=req.min_price_floor or 0.0,
                original_mrp=data.get("original_mrp", 0.0)
            )

            existing = db.query(MakroPiggybackItem).filter(
                MakroPiggybackItem.store_id == store.id,
                MakroPiggybackItem.makro_product_id == fsn
            ).first()

            if existing:
                existing.item_id = item_id or existing.item_id
                existing.makro_url = data.get("makro_url", existing.makro_url)
                existing.image_url = data.get("image_url") or existing.image_url
                existing.original_price = data.get("original_price", existing.original_price)
                existing.original_mrp = data.get("original_mrp", existing.original_mrp)
                existing.original_seller = data.get("original_seller") or existing.original_seller
                existing.seller_count = data.get("seller_count") or existing.seller_count or 1
                existing.target_price = target_p
                existing.target_mrp = target_m
            else:
                sku = _generate_piggyback_sku()
                new_item = MakroPiggybackItem(
                    store_id=store.id,
                    user_id=current_user.id if current_user else None,
                    makro_product_id=fsn,
                    item_id=item_id,
                    makro_url=data.get("makro_url"),
                    title=data.get("title"),
                    title_zh=data.get("title_zh"),
                    brand=data.get("brand"),
                    vertical=data.get("vertical"),
                    image_url=data.get("image_url"),
                    model_number=data.get("model_number"),
                    barcode=data.get("barcode"),
                    original_price=data.get("original_price", 0.0),
                    original_mrp=data.get("original_mrp", 0.0),
                    original_seller=data.get("original_seller"),
                    seller_count=data.get("seller_count", 1),
                    seller_sku=sku,
                    target_price=target_p,
                    target_mrp=target_m,
                    min_price_floor=req.min_price_floor or 0.0,
                    price_strategy=req.price_strategy or "MINUS_1",
                    inventory=99,
                    lead_time_days=14,
                    location_id=store.default_location_id,
                    compliance_status="PENDING_CHECK",
                    status="PENDING"
                )
                db.add(new_item)
            success_count += 1
        except Exception as e:
            failed_items.append({"item": clean_text, "error": str(e)})

    db.commit()
    return {
        "success": True,
        "total_requested": len(req.items),
        "success_count": success_count,
        "failed_count": len(failed_items),
        "failed_items": failed_items
    }

@router.get("/items", summary="获取跟品池商品列表与多维统计")
def list_piggyback_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="ALL, PENDING, ACTIVE, FAILED"),
    compliance_status: Optional[str] = Query(None, description="ALL, SAFE, RISK, PROHIBITED, PENDING_CHECK"),
    store_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(MakroPiggybackItem)

    # 员工权限隔离
    if current_user and current_user.role != "ADMIN":
        query = query.filter(MakroPiggybackItem.user_id == current_user.id)

    if store_id:
        query = query.filter(MakroPiggybackItem.store_id == store_id)

    # 全局总数统计 (用于顶部统计卡片)
    stat_query = db.query(MakroPiggybackItem)
    if current_user and current_user.role != "ADMIN":
        stat_query = stat_query.filter(MakroPiggybackItem.user_id == current_user.id)
    if store_id:
        stat_query = stat_query.filter(MakroPiggybackItem.store_id == store_id)

    total_all = stat_query.count()
    pending_count = stat_query.filter(MakroPiggybackItem.status == "PENDING").count()
    active_count = stat_query.filter(MakroPiggybackItem.status == "ACTIVE").count()
    failed_count = stat_query.filter(MakroPiggybackItem.status == "FAILED").count()
    safe_count = stat_query.filter(MakroPiggybackItem.compliance_status == "SAFE").count()
    risk_count = stat_query.filter(MakroPiggybackItem.compliance_status == "RISK").count()
    prohibited_count = stat_query.filter(MakroPiggybackItem.compliance_status == "PROHIBITED").count()

    # 应用筛选条件
    if status and status != "ALL":
        query = query.filter(MakroPiggybackItem.status == status)

    if compliance_status and compliance_status != "ALL":
        query = query.filter(MakroPiggybackItem.compliance_status == compliance_status)

    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            (MakroPiggybackItem.title.ilike(s)) |
            (MakroPiggybackItem.title_zh.ilike(s)) |
            (MakroPiggybackItem.makro_product_id.ilike(s)) |
            (MakroPiggybackItem.seller_sku.ilike(s)) |
            (MakroPiggybackItem.brand.ilike(s))
        )

    filtered_total = query.count()
    items = query.order_by(MakroPiggybackItem.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": filtered_total,
        "page": page,
        "page_size": page_size,
        "stats": {
            "total_all": total_all,
            "pending_count": pending_count,
            "active_count": active_count,
            "failed_count": failed_count,
            "safe_count": safe_count,
            "risk_count": risk_count,
            "prohibited_count": prohibited_count
        },
        "items": [_format_piggyback_item(it) for it in items]
    }

@router.put("/items/{item_id}", summary="修改单件跟品商品参数")
def update_piggyback_item(
    item_id: int,
    req: PiggybackItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    if req.seller_sku is not None:
        item.seller_sku = req.seller_sku.strip()
    if req.target_price is not None:
        item.target_price = float(req.target_price)
    if req.target_mrp is not None:
        item.target_mrp = float(req.target_mrp)
    if req.min_price_floor is not None:
        item.min_price_floor = float(req.min_price_floor)
    if req.price_strategy is not None:
        item.price_strategy = req.price_strategy
    if req.inventory is not None:
        item.inventory = int(req.inventory)
    if req.lead_time_days is not None:
        item.lead_time_days = int(req.lead_time_days)
    if req.store_id is not None:
        item.store_id = int(req.store_id)
    if req.weight is not None:
        item.weight = float(req.weight)
    if req.length is not None:
        item.length = float(req.length)
    if req.breadth is not None:
        item.breadth = float(req.breadth)
    if req.height is not None:
        item.height = float(req.height)

    db.commit()
    db.refresh(item)
    return {"success": True, "item": _format_piggyback_item(item)}

@router.post("/check-compliance/{item_id}", summary="对单件商品执行 AI 侵权与合规检测")
def check_single_compliance(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    res = MakroPiggybackService.check_compliance_for_item(item, db)
    return {
        "success": True,
        "compliance_status": item.compliance_status,
        "details": res
    }

@router.post("/batch-check-compliance", summary="批量执行 AI 侵权与合规检测")
def batch_check_compliance(
    req: BatchCheckComplianceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    results = {}
    for it in items:
        try:
            r = MakroPiggybackService.check_compliance_for_item(it, db)
            results[it.id] = {"status": it.compliance_status, "summary": r.get("summary")}
        except Exception as e:
            results[it.id] = {"status": "ERROR", "error": str(e)}

    return {
        "success": True,
        "total_checked": len(items),
        "results": results
    }

@router.post("/batch-apply-pricing", summary="批量应用智能比价策略")
def batch_apply_pricing(
    req: BatchApplyPricingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    updated_count = 0

    for it in items:
        floor = req.min_price_floor if req.min_price_floor is not None else it.min_price_floor
        tp, tm = MakroPiggybackService.calculate_price(
            original_price=it.original_price,
            strategy=req.price_strategy,
            min_floor=floor,
            original_mrp=it.original_mrp,
            custom_delta=req.custom_delta
        )
        it.target_price = tp
        it.target_mrp = tm
        it.price_strategy = req.price_strategy
        if req.min_price_floor is not None:
            it.min_price_floor = req.min_price_floor
        updated_count += 1

    db.commit()
    return {"success": True, "updated_count": updated_count}

@router.post("/publish/{item_id}", summary="单品执行 Makro 官方挂靠跟品")
def publish_single_piggyback(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    store = item.store or _get_target_store(db, item.store_id)
    try:
        res = MakroPiggybackService.publish_piggyback_listing(item, store, db)
        return {"success": True, "result": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/batch-publish", summary="批量异步执行 Makro 挂靠跟品")
def batch_publish_piggyback(
    req: BatchPublishPiggybackRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    if not items:
        raise HTTPException(status_code=400, detail="未指定合法的跟品商品")

    # 创建任务
    task_id = str(uuid.uuid4())
    task_manager.create_task(
        task_id=task_id,
        task_type="MAKRO_PIGGYBACK",
        title=f"批量跟品挂靠 ({len(items)} 件)",
        total=len(items)
    )

    def _worker(t_id: str, p_ids: List[int]):
        from ..database import SessionLocal
        with SessionLocal() as worker_db:
            done = 0
            errs = 0
            for pid in p_ids:
                p_item = worker_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == pid).first()
                if not p_item:
                    continue
                p_store = p_item.store or worker_db.query(Store).filter(Store.id == p_item.store_id).first()
                try:
                    MakroPiggybackService.publish_piggyback_listing(p_item, p_store, worker_db)
                    done += 1
                except Exception as ex:
                    errs += 1
                    logger.error(f"批量跟品 ID {pid} 挂靠异常: {ex}")
                task_manager.update_task_progress(t_id, done + errs, f"已处理 {done + errs}/{len(p_ids)} 件 (成功 {done}, 失败 {errs})")
            task_manager.complete_task(t_id, f"批量挂靠完成: 成功 {done} 件, 失败 {errs} 件")

    background_tasks.add_task(_worker, task_id, req.ids)

    return {
        "success": True,
        "task_id": task_id,
        "message": f"已成功启动 {len(items)} 件商品的异步挂靠任务"
    }

@router.delete("/items/{item_id}", summary="删除单件跟品商品")
def delete_piggyback_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    db.delete(item)
    db.commit()
    return {"success": True, "message": "删除成功"}

@router.post("/batch-delete", summary="批量删除跟品商品")
def batch_delete_piggyback(
    req: BatchDeletePiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).delete(synchronize_session=False)
    db.commit()
    return {"success": True, "deleted_count": len(req.ids)}
