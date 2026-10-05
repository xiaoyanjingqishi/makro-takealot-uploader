import uuid
import time
import json
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
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
    BatchSetStoreRequest,
    BatchDeletePiggybackRequest,
    BatchSetFloorRequest,
    CheckExistenceRequest,
    PiggybackItemResponse
)
from ..services.makro_scraper_service import MakroScraperService
from ..services.makro_piggyback_service import MakroPiggybackService
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
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

    op_name = None
    if getattr(item, "creator", None):
        op_name = item.creator.nickname or item.creator.username
    elif getattr(item, "user", None):
        op_name = item.user.nickname or item.user.username

    return {
        "id": item.id,
        "store_id": item.store_id,
        "store_name": item.store.name if item.store else "未知店铺",
        "user_id": item.user_id,
        "operator_name": op_name,
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
        "max_price_ceiling": item.max_price_ceiling or 0.0,
        "auto_reprice": item.auto_reprice if item.auto_reprice is not None else True,
        "last_reprice_at": item.last_reprice_at.strftime("%Y-%m-%d %H:%M:%S") if item.last_reprice_at else None,
        "last_reprice_result": item.last_reprice_result or "",
        "buybox_status": item.buybox_status or "UNKNOWN",
        "last_competitor_price": item.last_competitor_price,
        "price_strategy": item.price_strategy or "MINUS_1",
        "inventory": item.inventory or 99,
        "lead_time_days": item.lead_time_days or 14,
        "variant_attributes": item.variant_attributes,
        "variant_name": item.variant_name or "",
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
        existing.variant_attributes = req.variant_attributes or existing.variant_attributes
        existing.variant_name = req.variant_name or existing.variant_name
        if req.auto_reprice is not None:
            existing.auto_reprice = req.auto_reprice
        if req.max_price_ceiling is not None:
            existing.max_price_ceiling = req.max_price_ceiling
        item = existing
    else:
        sku = _generate_piggyback_sku()
        # 若为变体，在标题与货号中附加变体信息
        title_zh = data.get("title_zh")
        if req.variant_name and title_zh:
            title_zh = f"{title_zh} ({req.variant_name})"

        item = MakroPiggybackItem(
            store_id=store.id,
            user_id=current_user.id if current_user else None,
            makro_product_id=fsn,
            item_id=item_id,
            makro_url=data.get("makro_url"),
            title=data.get("title"),
            title_zh=title_zh,
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
            max_price_ceiling=req.max_price_ceiling or 0.0,
            auto_reprice=req.auto_reprice if req.auto_reprice is not None else True,
            variant_attributes=req.variant_attributes,
            variant_name=req.variant_name,
            price_strategy=req.price_strategy or "MINUS_1",
            inventory=99,
            lead_time_days=14,
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

    record_audit_log(
        task_type="PIGGYBACK_COLLECT",
        status="SUCCESS",
        message=f"Makro跟品采集: {item.title[:35]} (FSN: {item.makro_product_id})",
        detail_logs={"fsn": item.makro_product_id, "title": item.title, "sku": item.seller_sku, "store_id": item.store_id},
        user_id=current_user.id,
        operator_name=current_user.nickname or current_user.username,
        db=db
    )

    return {
        "success": True,
        "message": f"成功采集商品「{item.title[:30]}...」入库",
        "item": _format_piggyback_item(item)
    }


@router.post("/batch-collect", summary="批量采集 Makro 链接、FSN 或搜索页/变体富数据入库")
def batch_collect_piggyback(
    req: BatchCollectPiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _get_target_store(db, req.store_id)
    success_count = 0
    failed_items = []

    # 1. 优先处理来自插件扩展的结构化数据 (搜索页批量采集 / 多变体采集，后端直接发起官方与前台权威抓取)
    if req.rich_items and len(req.rich_items) > 0:
        for rit in req.rich_items:
            try:
                fsn = (rit.get("fsn") or rit.get("makro_product_id") or rit.get("pid") or "").strip().upper()
                if not fsn:
                    continue
                item_id = rit.get("item_id")
                
                # 后端直接调用 MakroScraperService 获取权威价格、MRP、标题与主图
                data = MakroScraperService.resolve_piggyback_product(fsn, store, client_data={"item_id": item_id})

                target_fsn = data["makro_product_id"]
                target_item_id = item_id or data.get("item_id")
                raw_url = data.get("makro_url") or MakroScraperService.format_canonical_makro_url(target_fsn, target_item_id)
                title = data.get("title") or fsn
                title_zh = data.get("title_zh") or title
                image_url = data.get("image_url") or ""
                seller_name = data.get("original_seller") or ""
                seller_count = int(data.get("seller_count") or 1)
                real_price = float(data.get("original_price") or 0.0)
                real_mrp = float(data.get("original_mrp") or (real_price * 1.5 if real_price > 0 else 0.0))

                variant_name = rit.get("variant_name") or ""
                variant_attributes = rit.get("variant_attributes")
                if isinstance(variant_attributes, (dict, list)):
                    variant_attributes = json.dumps(variant_attributes, ensure_ascii=False)

                target_p, target_m = MakroPiggybackService.calculate_price(
                    original_price=real_price,
                    strategy=req.price_strategy or "MINUS_1",
                    min_floor=req.min_price_floor or 0.0,
                    original_mrp=real_mrp
                )

                existing = db.query(MakroPiggybackItem).filter(
                    MakroPiggybackItem.store_id == store.id,
                    MakroPiggybackItem.makro_product_id == target_fsn
                ).first()

                if existing:
                    existing.item_id = target_item_id or existing.item_id
                    existing.makro_url = raw_url
                    existing.title = title or existing.title
                    existing.title_zh = title_zh or existing.title_zh
                    existing.image_url = image_url or existing.image_url
                    existing.original_price = real_price or existing.original_price
                    existing.original_mrp = real_mrp or existing.original_mrp
                    existing.original_seller = seller_name or existing.original_seller
                    existing.seller_count = seller_count or existing.seller_count
                    existing.target_price = target_p
                    existing.target_mrp = target_m
                    if variant_name:
                        existing.variant_name = variant_name
                    if variant_attributes:
                        existing.variant_attributes = variant_attributes
                else:
                    sku = _generate_piggyback_sku()
                    if variant_name and not title_zh.endswith(f"({variant_name})"):
                        title_zh = f"{title_zh} ({variant_name})"

                    new_item = MakroPiggybackItem(
                        store_id=store.id,
                        user_id=current_user.id if current_user else None,
                        makro_product_id=target_fsn,
                        item_id=target_item_id,
                        makro_url=raw_url,
                        title=title,
                        title_zh=title_zh,
                        brand=data.get("brand") or getattr(store, "default_brand", "Generic") or "Generic",
                        vertical=data.get("vertical") or "general",
                        image_url=image_url,
                        original_price=real_price,
                        original_mrp=real_mrp,
                        original_seller=seller_name,
                        seller_count=seller_count,
                        seller_sku=sku,
                        target_price=target_p,
                        target_mrp=target_m,
                        min_price_floor=req.min_price_floor or 0.0,
                        price_strategy=req.price_strategy or "MINUS_1",
                        auto_reprice=True,
                        variant_name=variant_name,
                        variant_attributes=variant_attributes,
                        inventory=99,
                        lead_time_days=14,
                        compliance_status="PENDING_CHECK",
                        status="PENDING"
                    )
                    db.add(new_item)
                success_count += 1
            except Exception as re_err:
                failed_items.append({"item": str(rit.get("fsn") or rit.get("title")), "error": str(re_err)})

    # 2. 处理纯字符串链接或 FSN 列表
    if req.items and len(req.items) > 0:
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
                        auto_reprice=True,
                        inventory=99,
                        lead_time_days=14,
                        compliance_status="PENDING_CHECK",
                        status="PENDING"
                    )
                    db.add(new_item)
                success_count += 1
            except Exception as e:
                failed_items.append({"item": clean_text, "error": str(e)})

    db.commit()
    total_requested = (len(req.items) if req.items else 0) + (len(req.rich_items) if req.rich_items else 0)
    record_audit_log(
        task_type="PIGGYBACK_COLLECT",
        status="SUCCESS" if success_count > 0 else "FAILED",
        message=f"批量跟品采集: 成功 {success_count}/{total_requested} 件商品入库",
        detail_logs={"total": total_requested, "success": success_count, "failed": len(failed_items)},
        user_id=current_user.id,
        operator_name=current_user.nickname or current_user.username,
        db=db
    )
    return {
        "success": True,
        "total_requested": total_requested,
        "success_count": success_count,
        "failed_count": len(failed_items),
        "failed_items": failed_items
    }


@router.get("/kpi-stats", summary="获取跟品与跟价运营驾驶舱 6 大核心 KPI 统计")
def get_piggyback_kpi_stats(
    store_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(MakroPiggybackItem)
    if current_user and current_user.role != "ADMIN":
        query = query.filter(MakroPiggybackItem.user_id == current_user.id)
    if isinstance(store_id, int):
        query = query.filter(MakroPiggybackItem.store_id == store_id)

    total_count = query.count()
    active_query = query.filter(MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]))
    active_count = active_query.count()

    winning_count = active_query.filter(MakroPiggybackItem.buybox_status == "WINNING").count()
    losing_count = active_query.filter(MakroPiggybackItem.buybox_status == "LOSING").count()
    floor_hit_count = active_query.filter(MakroPiggybackItem.buybox_status == "FLOOR_HIT").count()
    missing_floor_count = active_query.filter(
        (MakroPiggybackItem.min_price_floor == None) | (MakroPiggybackItem.min_price_floor <= 0)
    ).count()

    staging_count = query.filter(MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"])).count()
    blocked_count = query.filter(
        (MakroPiggybackItem.status == "FAILED") |
        (MakroPiggybackItem.compliance_status.in_(["PROHIBITED", "RISK"]))
    ).count()

    return {
        "total_count": total_count,
        "active_count": active_count,
        "winning_count": winning_count,
        "losing_count": losing_count,
        "floor_hit_count": floor_hit_count,
        "missing_floor_count": missing_floor_count,
        "staging_count": staging_count,
        "blocked_count": blocked_count
    }

@router.post("/batch-set-floor", summary="批量公式设置保本底价并智能联动跟价")
def batch_set_floor(
    req: BatchSetFloorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="请至少选择一件商品")

    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    updated_count = 0
    mode = (req.mode or "PERCENT").upper()
    val = float(req.value or 0.0)

    for item in items:
        base_p = float(item.original_price or item.target_price or 0.0)
        if mode == "PERCENT":
            new_floor = round(base_p * (val / 100.0), 2)
        elif mode == "OFFSET":
            new_floor = max(round(base_p - val, 2), 1.0)
        elif mode == "FIXED":
            new_floor = max(round(val, 2), 1.0)
        else:
            new_floor = round(base_p * 0.7, 2)

        item.min_price_floor = new_floor
        # 保护当前售价不得低于新设定的底价
        if item.target_price and item.target_price < new_floor:
            item.target_price = new_floor
        if req.auto_enable_reprice:
            item.auto_reprice = True
        updated_count += 1

    db.commit()
    record_audit_log(
        task_type="PIGGYBACK_BATCH_FLOOR",
        status="SUCCESS",
        message=f"批量设置跟品底价: 成功修改 {updated_count} 件商品底价 (模式: {mode}, 参数: {val})",
        detail_logs={"total": len(req.ids), "updated_count": updated_count, "mode": mode, "value": val},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {
        "success": True,
        "total_requested": len(req.ids),
        "updated_count": updated_count
    }


@router.post("/check-existence", summary="核验一组 FSN/PID 是否已存在于跟品库中 (用于扩展排重感知)")
def check_piggyback_existence(
    req: CheckExistenceRequest,
    db: Session = Depends(get_db)
):
    if not req.fsns:
        return {"exists": {}}

    fsn_list = [f.strip().upper() for f in req.fsns if f and f.strip()]
    if not fsn_list:
        return {"exists": {}}

    query = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.makro_product_id.in_(fsn_list))
    if isinstance(req.store_id, int):
        query = query.filter(MakroPiggybackItem.store_id == req.store_id)

    matched = query.all()
    res = {}
    for it in matched:
        res[it.makro_product_id] = {
            "id": it.id,
            "status": it.status,
            "seller_sku": it.seller_sku,
            "target_price": it.target_price,
            "buybox_status": it.buybox_status or "UNKNOWN",
            "auto_reprice": it.auto_reprice,
            "min_price_floor": it.min_price_floor or 0.0
        }

    return {"exists": res}

@router.get("/items", summary="获取跟品池商品列表与多维统计")
def list_piggyback_items(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    buybox_status: Optional[str] = None,
    compliance_status: Optional[str] = None,
    store_id: Optional[int] = None,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(MakroPiggybackItem)

    # 员工权限隔离与管理员指定采品人过滤
    if current_user and current_user.role != "ADMIN":
        query = query.filter(MakroPiggybackItem.user_id == current_user.id)
    elif user_id is not None:
        query = query.filter(MakroPiggybackItem.user_id == user_id)

    if isinstance(store_id, int):
        query = query.filter(MakroPiggybackItem.store_id == store_id)

    # 全局总数统计 (用于顶部统计卡片)
    stat_query = db.query(MakroPiggybackItem)
    if current_user and current_user.role != "ADMIN":
        stat_query = stat_query.filter(MakroPiggybackItem.user_id == current_user.id)
    elif user_id is not None:
        stat_query = stat_query.filter(MakroPiggybackItem.user_id == user_id)
    if isinstance(store_id, int):
        stat_query = stat_query.filter(MakroPiggybackItem.store_id == store_id)

    total_all = stat_query.count()
    pending_count = stat_query.filter(MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"])).count()
    active_count = stat_query.filter(MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"])).count()
    failed_count = stat_query.filter(MakroPiggybackItem.status == "FAILED").count()
    pending_check_count = stat_query.filter(MakroPiggybackItem.compliance_status == "PENDING_CHECK").count()
    safe_count = stat_query.filter(MakroPiggybackItem.compliance_status == "SAFE").count()
    risk_count = stat_query.filter(MakroPiggybackItem.compliance_status == "RISK").count()
    prohibited_count = stat_query.filter(MakroPiggybackItem.compliance_status == "PROHIBITED").count()

    # 应用阶段漏斗筛选 (STAGING, ACTIVE_MONITOR, BLOCKED_FAILED)
    if stage == "STAGING":
        query = query.filter(MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"]))
    elif stage == "ACTIVE_MONITOR":
        query = query.filter(MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]))
    elif stage == "BLOCKED_FAILED":
        query = query.filter(
            (MakroPiggybackItem.status == "FAILED") |
            (MakroPiggybackItem.compliance_status.in_(["PROHIBITED", "RISK"]))
        )
    elif status and status != "ALL":
        query = query.filter(MakroPiggybackItem.status == status)

    # 应用 Buybox 战况与缺底价筛选
    if buybox_status and buybox_status != "ALL":
        if buybox_status == "MISSING_FLOOR":
            query = query.filter(
                (MakroPiggybackItem.min_price_floor == None) | (MakroPiggybackItem.min_price_floor <= 0)
            )
        else:
            query = query.filter(MakroPiggybackItem.buybox_status == buybox_status)

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
    items = (
        query.options(
            joinedload(MakroPiggybackItem.creator),
            joinedload(MakroPiggybackItem.store)
        )
        .order_by(MakroPiggybackItem.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "total": filtered_total,
        "page": page,
        "page_size": page_size,
        "stats": {
            "total_all": total_all,
            "pending_count": pending_count,
            "active_count": active_count,
            "failed_count": failed_count,
            "pending_check_count": pending_check_count,
            "safe_count": safe_count,
            "risk_count": risk_count,
            "prohibited_count": prohibited_count
        },
        "items": [_format_piggyback_item(it) for it in items]
    }

@router.put("/items/{item_id}", summary="修改单件跟品商品参数 (支持售价与底价极速保存并联动推送)")
def update_piggyback_item(
    item_id: int,
    req: PiggybackItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    old_price = item.target_price
    price_changed = False

    if req.seller_sku is not None:
        item.seller_sku = req.seller_sku.strip()
    if req.target_price is not None:
        new_price = float(req.target_price)
        if abs(new_price - (old_price or 0.0)) >= 0.01:
            price_changed = True
        item.target_price = new_price
    if req.target_mrp is not None:
        item.target_mrp = float(req.target_mrp)
    if req.min_price_floor is not None:
        item.min_price_floor = float(req.min_price_floor)
        # 若当前售价低于新底价，自动抬升至底价
        if item.target_price and item.target_price < item.min_price_floor:
            item.target_price = item.min_price_floor
            price_changed = True
    if req.max_price_ceiling is not None:
        item.max_price_ceiling = float(req.max_price_ceiling)
    if req.auto_reprice is not None:
        item.auto_reprice = req.auto_reprice
    if req.variant_name is not None:
        item.variant_name = req.variant_name.strip()
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

    # 若已在售且售价实质变动，尝试联动官方 API 同步新售价
    push_msg = None
    if price_changed and item.status in ["ACTIVE", "PUBLISHED"]:
        try:
            from ..services.auto_reprice_service import AutoRepriceService
            store = item.store or _get_target_store(db, item.store_id)
            AutoRepriceService._push_price_to_makro(item, store, item.target_price)
            item.last_reprice_at = datetime.now()
            item.last_reprice_result = f"MANUAL_SYNC: 手动改价为 R{item.target_price} 并成功同步官方"
            push_msg = "已同步推送至 Makro 官方 Listing"
        except Exception as e:
            logger.warning(f"手动改价同步 Makro 官方失败 [{item.seller_sku}]: {e}")
            push_msg = f"本地保存成功，但官方同步失败: {str(e)[:100]}"

    db.commit()
    db.refresh(item)
    if price_changed or req.min_price_floor is not None or req.auto_reprice is not None:
        record_audit_log(
            task_type="REPRICE_UPDATE",
            status="SUCCESS",
            message=f"更新跟品定价: {item.title[:35]} (售价: R{item.target_price}, 底价: R{item.min_price_floor})",
            detail_logs={"item_id": item.id, "target_price": item.target_price, "floor": item.min_price_floor, "auto_reprice": item.auto_reprice},
            user_id=current_user.id,
            operator_name=current_user.nickname or current_user.username,
            db=db
        )
    return {"success": True, "item": _format_piggyback_item(item), "sync_message": push_msg}

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
    record_audit_log(
        task_type="PIGGYBACK_COMPLIANCE",
        status="SUCCESS",
        message=f"跟品合规检测: {item.title[:35]} -> 状态={item.compliance_status}",
        detail_logs={"item_id": item.id, "compliance_status": item.compliance_status, "details": res},
        user_id=current_user.id,
        operator_name=current_user.nickname or current_user.username,
        db=db
    )
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

    record_audit_log(
        task_type="PIGGYBACK_COMPLIANCE",
        status="SUCCESS",
        message=f"批量跟品合规检测: 完成 {len(items)} 件商品排查",
        detail_logs={"total": len(items), "results": results},
        user_id=current_user.id,
        operator_name=current_user.nickname or current_user.username,
        db=db
    )

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
    store_id: Optional[int] = Query(None, description="指定挂靠目标店铺 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    if store_id:
        store = db.query(Store).filter(Store.id == store_id).first()
        if not store:
            raise HTTPException(status_code=400, detail=f"指定的店铺 ID {store_id} 不存在")
        item.store_id = store.id
    else:
        store = item.store or _get_target_store(db, item.store_id)
    try:
        res = MakroPiggybackService.publish_piggyback_listing(item, store, db)
        record_audit_log(
            task_type="PIGGYBACK_PUBLISH",
            status="SUCCESS",
            message=f"跟品挂靠上架成功: {item.title[:35]} (SKU: {item.seller_sku}, 店铺: {store.name})",
            detail_logs={"item_id": item.id, "sku": item.seller_sku, "store_id": store.id, "store_name": store.name, "result": res},
            user_id=current_user.id,
            operator_name=current_user.nickname or current_user.username,
            db=db
        )
        return {"success": True, "result": res}
    except Exception as e:
        record_audit_log(
            task_type="PIGGYBACK_PUBLISH",
            status="FAILED",
            message=f"跟品挂靠上架失败: {item.title[:35]} (原因: {str(e)})",
            detail_logs={"item_id": item.id, "sku": item.seller_sku, "store_id": store.id if store else item.store_id, "error": str(e)},
            user_id=current_user.id,
            operator_name=current_user.nickname or current_user.username,
            db=db
        )
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/batch-publish", summary="批量异步执行 Makro 挂靠跟品")
def batch_publish_piggyback(
    req: BatchPublishPiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    if not items:
        raise HTTPException(status_code=400, detail="未指定合法的跟品商品")

    target_store = None
    if req.store_id:
        target_store = db.query(Store).filter(Store.id == req.store_id).first()
        if not target_store:
            raise HTTPException(status_code=400, detail=f"指定的跟品目标店铺 ID {req.store_id} 不存在")

    u_id = current_user.id
    op_name = current_user.nickname or current_user.username
    store_desc = f"至 [{target_store.name}]" if target_store else "至各所属店铺"

    # 创建中台标准异步任务 (与 cleaner/makro 批处理标准完全对齐)
    task = task_manager.create_task(
        task_type="MAKRO_PIGGYBACK",
        name=f"批量跟品挂靠 {store_desc} ({len(items)} 件)",
        total=len(items),
        product_ids=req.ids
    )
    task_id = task["id"]
    target_store_id = target_store.id if target_store else None

    def _worker(tm: TaskManager, tid: str):
        from ..database import SessionLocal
        local_db = SessionLocal()
        try:
            done = 0
            errs = 0
            for idx, pid in enumerate(req.ids):
                if tm.is_cancelled(tid):
                    break
                p_item = local_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == pid).first()
                if not p_item:
                    continue

                if target_store_id:
                    p_store = local_db.query(Store).filter(Store.id == target_store_id).first()
                    p_item.store_id = target_store_id
                    try:
                        local_db.commit()
                    except Exception as ce:
                        local_db.rollback()
                        logger.warning(f"批量跟品变更店铺失败 [{pid}]: {ce}")
                else:
                    p_store = p_item.store or local_db.query(Store).filter(Store.id == p_item.store_id).first()
                if not p_store:
                    p_store = _get_target_store(local_db)

                try:
                    MakroPiggybackService.publish_piggyback_listing(p_item, p_store, local_db)
                    done += 1
                    tm.update_progress(
                        tid,
                        current=done + errs,
                        current_title=f"已成功挂靠: {p_item.title[:35]}",
                        success_inc=1
                    )
                except Exception as ex:
                    errs += 1
                    logger.error(f"批量跟品 ID {pid} 挂靠异常: {ex}")
                    tm.update_progress(
                        tid,
                        current=done + errs,
                        current_title=f"挂靠失败: {p_item.title[:35]}",
                        fail_inc=1,
                        error=str(ex)
                    )

            status = "CANCELLED" if tm.is_cancelled(tid) else ("SUCCESS" if done > 0 else "FAILED")
            msg = f"批量挂靠完成: 成功 {done} 件, 失败 {errs} 件"
            tm.finish_task(tid, status=status, message=msg)
            record_audit_log(
                task_type="PIGGYBACK_PUBLISH",
                status=status,
                message=msg,
                detail_logs={"total": len(req.ids), "success": done, "failed": errs, "target_store_id": target_store_id, "item_ids": req.ids[:50]},
                user_id=u_id,
                operator_name=op_name,
                db=local_db
            )
        finally:
            local_db.close()

    task_manager.start_task(task_id, _worker)

    return {
        "success": True,
        "task_id": task_id,
        "message": f"已成功启动 {len(items)} 件商品的异步批量挂靠任务 ({store_desc})"
    }

@router.post("/batch-set-store", summary="批量修改选定跟品商品的所属店铺")
def batch_set_store(
    req: BatchSetStoreRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="请至少选择一件商品")
    store = db.query(Store).filter(Store.id == req.store_id).first()
    if not store:
        raise HTTPException(status_code=400, detail=f"指定的店铺 ID {req.store_id} 不存在")

    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    updated_count = 0
    for it in items:
        it.store_id = store.id
        updated_count += 1
    db.commit()

    record_audit_log(
        task_type="PIGGYBACK_BATCH_STORE",
        status="SUCCESS",
        message=f"批量转移跟品店铺: 成功将 {updated_count} 件商品归属转移至店铺 [{store.name}]",
        detail_logs={"total": len(req.ids), "updated_count": updated_count, "store_id": store.id, "store_name": store.name},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {
        "success": True,
        "total_requested": len(req.ids),
        "updated_count": updated_count,
        "store_id": store.id,
        "store_name": store.name
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
