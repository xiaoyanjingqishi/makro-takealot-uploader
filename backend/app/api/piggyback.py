import uuid
import time
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..database import get_db
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.makro_listing import MakroListing
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
    PiggybackItemResponse,
    ArbitratePiggybackComplianceRequest,
    AbandonPiggybackRequest,
    BatchAbandonPiggybackRequest,
    BatchRestorePiggybackRequest
)
from ..services.makro_scraper_service import MakroScraperService
from ..services.makro_piggyback_service import MakroPiggybackService
from ..services.makro_portal_service import MakroPortalService
from ..services.piggyback_collect_service import piggyback_collect_service
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
from ..utils.auth import get_current_user, get_optional_current_user


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/piggyback", tags=["Makro跟品与合规挂靠"])

from ..services.piggyback import PiggybackService, PiggybackPricingCalculator

_generate_piggyback_sku = PiggybackService.generate_piggyback_sku
_get_target_store = PiggybackService.get_target_store
_format_piggyback_item = PiggybackService.format_piggyback_item


@router.post("/collect", summary="采集单个 Makro 商品并秒级加入跟品池 (后台静默拉取详情)")
def collect_single_piggyback(
    req: CollectPiggybackRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    effective_user_id = current_user.id if current_user else req.user_id
    op_name = (current_user.nickname or current_user.username) if current_user else (req.collector_username or "插件采集助手")
    if not current_user and req.user_id:
        u = db.query(User).filter(User.id == req.user_id).first()
        if u:
            op_name = u.nickname or u.username
    store = _get_target_store(db, req.store_id)

    # 1. 快速提取并规整 FSN 与 Item ID (纯正则运算，0 网络耗时)
    ext_fsn, ext_itm = MakroScraperService.extract_identifiers(req.url_or_fsn)
    fsn = (ext_fsn or "").upper()
    item_id = ext_itm or req.item_id
    if not fsn and req.url_or_fsn:
        raw_clean = req.url_or_fsn.strip()
        if len(raw_clean) >= 12 and "/" not in raw_clean:
            fsn = raw_clean.upper()

    if not fsn:
        raise HTTPException(status_code=400, detail="未能识别有效的 Makro 商品标识 (FSN 编码或商品 URL)")

    # 2. 弃用黑名单排重拦截 (防止侵权商品或废弃品被重复采集)
    abandoned_item = db.query(MakroPiggybackItem).filter(
        MakroPiggybackItem.makro_product_id == fsn,
        MakroPiggybackItem.is_abandoned == True
    ).first()
    if abandoned_item:
        reason_desc = abandoned_item.abandoned_reason or "侵权风险/手工弃用"
        raise HTTPException(
            status_code=400,
            detail=f"【弃用黑名单拦截】该商品 (FSN: {fsn}) 已被弃用（原因: {reason_desc}），禁止重复采集！"
        )

    # 3. 检查是否已在当前店铺或全局跟品池中
    existing = db.query(MakroPiggybackItem).filter(
        MakroPiggybackItem.store_id == store.id,
        MakroPiggybackItem.makro_product_id == fsn
    ).first()
    if not existing and req.store_id is None:
        existing = db.query(MakroPiggybackItem).filter(
            MakroPiggybackItem.makro_product_id == fsn
        ).first()

    if existing:
        if req.variant_name:
            existing.variant_name = req.variant_name
        if req.variant_attributes:
            existing.variant_attributes = req.variant_attributes
        db.commit()
        db.refresh(existing)
        return {
            "success": True,
            "already_exists": True,
            "message": f"该商品 (FSN: {fsn}) 已在跟品库中，无需重复入库",
            "item": _format_piggyback_item(existing)
        }

    # 4. 秒级创建骨架占位记录 (status=FETCHING，毫秒级直接落库)
    sku = _generate_piggyback_sku()
    placeholder_title = f"[数据获取中] {fsn}"
    canonical_url = MakroScraperService.format_canonical_makro_url(fsn, item_id)

    new_item = MakroPiggybackItem(
        store_id=store.id,
        user_id=effective_user_id,
        makro_product_id=fsn,
        item_id=item_id,
        makro_url=canonical_url,
        title=placeholder_title,
        title_zh=placeholder_title,
        brand=getattr(store, "default_brand", "Generic") or "Generic",
        vertical="general",
        image_url="",
        original_price=0.0,
        original_mrp=0.0,
        original_seller="",
        seller_count=1,
        seller_sku=sku,
        target_price=0.0,
        target_mrp=0.0,
        min_price_floor=float(req.min_price_floor or 0.0),
        max_price_ceiling=float(req.max_price_ceiling or 0.0),
        auto_reprice=req.auto_reprice if req.auto_reprice is not None else True,
        variant_attributes=req.variant_attributes,
        variant_name=req.variant_name,
        price_strategy=req.price_strategy or "MINUS_15",
        inventory=500,
        lead_time_days=14,
        is_abandoned=False,
        compliance_status="PENDING_CHECK",
        status="FETCHING"
    )
    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    # 5. 立即推入后台专用并发工作池进行静默抓取
    piggyback_collect_service.enqueue_items([new_item.id])

    record_audit_log(
        task_type="PIGGYBACK_COLLECT",
        status="SUCCESS",
        message=f"Makro跟品秒级入库: FSN {new_item.makro_product_id} (已排队静默抓取)",
        detail_logs={"fsn": new_item.makro_product_id, "sku": new_item.seller_sku, "store_id": new_item.store_id},
        user_id=effective_user_id,
        operator_name=op_name,
        db=db
    )

    return {
        "success": True,
        "already_exists": False,
        "message": f"商品 (FSN: {fsn}) 已秒级入库！后台正静默拉取完整数据...",
        "item": _format_piggyback_item(new_item)
    }


@router.post("/batch-collect", summary="批量采集 Makro 链接、FSN 或搜索页/变体富数据入库")
def batch_collect_piggyback(
    req: BatchCollectPiggybackRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    effective_user_id = current_user.id if current_user else req.user_id
    op_name = (current_user.nickname or current_user.username) if current_user else (req.collector_username or "插件批量采集")
    if not current_user and req.user_id:
        u = db.query(User).filter(User.id == req.user_id).first()
        if u:
            op_name = u.nickname or u.username

    store = _get_target_store(db, req.store_id)
    success_count = 0
    skipped_existing_count = 0
    failed_items = []

    # 1. 规整待处理商品，提取目标标识并进行批内去重
    targets = []
    seen_target_keys = set()
    all_fsns = set()

    # 1.1 处理富数据 (插件整页 / 变体采集)
    if req.rich_items:
        for rit in req.rich_items:
            fsn = (rit.get("fsn") or rit.get("makro_product_id") or rit.get("pid") or "").strip().upper()
            raw_url = rit.get("url") or ""
            if not fsn and raw_url:
                ext_fsn, ext_itm = MakroScraperService.extract_identifiers(raw_url)
                fsn = (ext_fsn or "").upper()
                if not rit.get("item_id") and ext_itm:
                    rit["item_id"] = ext_itm
            
            dedup_key = fsn or raw_url
            if not dedup_key or dedup_key in seen_target_keys:
                continue
            seen_target_keys.add(dedup_key)
            if fsn:
                all_fsns.add(fsn)
            targets.append({
                "type": "rich",
                "fsn": fsn,
                "item_id": rit.get("item_id"),
                "raw": raw_url or fsn,
                "payload": rit
            })

    # 1.2 处理纯字符串链接或 FSN 列表
    if req.items:
        for raw in req.items:
            clean_text = raw.strip()
            if not clean_text:
                continue
            ext_fsn, ext_itm = MakroScraperService.extract_identifiers(clean_text)
            fsn = (ext_fsn or "").upper()
            if not fsn and "/" not in clean_text:
                fsn = clean_text.upper()
            dedup_key = fsn or clean_text
            if dedup_key in seen_target_keys:
                continue
            seen_target_keys.add(dedup_key)
            if fsn:
                all_fsns.add(fsn)
            targets.append({
                "type": "raw",
                "fsn": fsn,
                "item_id": ext_itm,
                "raw": clean_text,
                "payload": None
            })

    # 2. 前置内存级快速排重与黑名单拦截 (单次 SQL 批量查询，耗时 1ms)
    abandoned_map = {}
    existing_map = {}

    if all_fsns:
        # 2.1 弃用黑名单批量检测
        abandoned_records = db.query(MakroPiggybackItem.makro_product_id, MakroPiggybackItem.abandoned_reason).filter(
            MakroPiggybackItem.makro_product_id.in_(list(all_fsns)),
            MakroPiggybackItem.is_abandoned == True
        ).all()
        abandoned_map = {rec[0]: (rec[1] or "侵权违规拦截") for rec in abandoned_records}

        # 2.2 已在库商品批量检测
        existing_query = db.query(MakroPiggybackItem).filter(
            MakroPiggybackItem.makro_product_id.in_(list(all_fsns))
        )
        if store and req.store_id is not None:
            existing_records = existing_query.filter(MakroPiggybackItem.store_id == store.id).all()
        else:
            existing_records = existing_query.all()
        existing_map = {item.makro_product_id: item for item in existing_records}

    # 3. 本地分流：已在库或黑名单商品直接处理，完全免除外部网络请求
    to_fetch_targets = []
    for t in targets:
        target_fsn = t.get("fsn")
        # 黑名单拦截
        if target_fsn and target_fsn in abandoned_map:
            failed_items.append({"item": target_fsn, "error": f"已在弃用黑名单中 ({abandoned_map[target_fsn]})，禁止重复采集"})
            continue
        
        # 已在库商品快速跳过 / 增量更新变体信息 (0 外部网络开销)
        if target_fsn and target_fsn in existing_map:
            existing = existing_map[target_fsn]
            if t["type"] == "rich":
                rit = t["payload"]
                if rit.get("variant_name"):
                    existing.variant_name = rit.get("variant_name")
                if rit.get("variant_attributes"):
                    va = rit.get("variant_attributes")
                    existing.variant_attributes = json.dumps(va, ensure_ascii=False) if isinstance(va, (dict, list)) else str(va)
                if rit.get("item_id") and not existing.item_id:
                    existing.item_id = rit.get("item_id")
            skipped_existing_count += 1
            continue

        to_fetch_targets.append(t)

    # 4. 毫秒级批量创建骨架占位记录 (status=FETCHING，秒级直接落库)
    new_items_to_enqueue = []
    for tgt in to_fetch_targets:
        fsn = tgt.get("fsn")
        raw_input = tgt.get("raw")
        item_id = tgt.get("item_id")
        if not fsn and raw_input:
            ext_fsn, ext_itm = MakroScraperService.extract_identifiers(raw_input)
            fsn = (ext_fsn or "").upper()
            item_id = item_id or ext_itm
            if not fsn and "/" not in raw_input.strip():
                fsn = raw_input.strip().upper()

        if not fsn:
            failed_items.append({"item": raw_input, "error": "未能提取商品 FSN 编码"})
            continue

        if fsn in abandoned_map:
            failed_items.append({"item": fsn, "error": f"已在弃用黑名单中 ({abandoned_map[fsn]})，禁止重复采集"})
            continue

        if fsn in existing_map:
            skipped_existing_count += 1
            continue

        sku = _generate_piggyback_sku()
        canonical_url = MakroScraperService.format_canonical_makro_url(fsn, item_id)
        placeholder_title = f"[数据获取中] {fsn}"

        variant_name = ""
        variant_attributes = None
        if tgt["type"] == "rich" and tgt["payload"]:
            variant_name = tgt["payload"].get("variant_name") or ""
            variant_attributes = tgt["payload"].get("variant_attributes")
            if isinstance(variant_attributes, (dict, list)):
                variant_attributes = json.dumps(variant_attributes, ensure_ascii=False)

        new_item = MakroPiggybackItem(
            store_id=store.id,
            user_id=effective_user_id,
            makro_product_id=fsn,
            item_id=item_id,
            makro_url=canonical_url,
            title=placeholder_title,
            title_zh=placeholder_title,
            brand=getattr(store, "default_brand", "Generic") or "Generic",
            vertical="general",
            image_url="",
            original_price=0.0,
            original_mrp=0.0,
            original_seller="",
            seller_count=1,
            seller_sku=sku,
            target_price=0.0,
            target_mrp=0.0,
            min_price_floor=float(req.min_price_floor or 0.0),
            price_strategy=req.price_strategy or "MINUS_15",
            auto_reprice=True,
            variant_name=variant_name,
            variant_attributes=variant_attributes,
            inventory=500,
            lead_time_days=14,
            is_abandoned=False,
            compliance_status="PENDING_CHECK",
            status="FETCHING"
        )
        db.add(new_item)
        existing_map[fsn] = new_item  # 批内排重保护
        new_items_to_enqueue.append(new_item)
        success_count += 1

    db.commit()

    # 5. 立即推入后台专用并发工作池进行静默抓取
    if new_items_to_enqueue:
        enqueued_ids = [it.id for it in new_items_to_enqueue]
        piggyback_collect_service.enqueue_items(enqueued_ids)

    total_requested = (len(req.items) if req.items else 0) + (len(req.rich_items) if req.rich_items else 0)
    audit_msg = f"批量跟品秒级入库: 成功建立骨架 {success_count} 件 (已启动后台静默拉取)，自动跳过已在库 {skipped_existing_count} 件" + (f"，失败 {len(failed_items)} 件" if failed_items else "")
    record_audit_log(
        task_type="PIGGYBACK_COLLECT",
        status="SUCCESS" if (success_count > 0 or skipped_existing_count > 0) else "FAILED",
        message=audit_msg,
        detail_logs={"total": total_requested, "new_added": success_count, "skipped_existing": skipped_existing_count, "failed": len(failed_items)},
        user_id=effective_user_id,
        operator_name=op_name,
        db=db
    )
    return {
        "success": True,
        "total_requested": total_requested,
        "success_count": success_count,
        "skipped_existing_count": skipped_existing_count,
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "message": f"⚡ 已成功秒级入库 {success_count} 件商品！后台正在多线程静默拉取完整数据..."
    }


@router.post("/{item_id}/retry-fetch", summary="重新触发单个商品的后台静默数据抓取")
def retry_fetch_single_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="跟品商品不存在")
    success = piggyback_collect_service.retry_item(item_id)
    return {"success": success, "message": "已重新加入后台静默拉取队列"}


@router.post("/batch-retry-fetch", summary="一键批量重新拉取所有失败商品的数据")
def batch_retry_fetch_items(
    store_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    count = piggyback_collect_service.retry_failed_items(store_id=store_id)
    return {"success": True, "count": count, "message": f"已将 {count} 件失败商品重新加入静默拉取队列"}


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

    abandoned_count = query.filter(MakroPiggybackItem.is_abandoned == True).count()
    unabandoned = query.filter((MakroPiggybackItem.is_abandoned == False) | (MakroPiggybackItem.is_abandoned == None))

    total_count = unabandoned.count()
    active_query = unabandoned.filter(MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]))
    active_count = active_query.count()

    winning_count = active_query.filter(MakroPiggybackItem.buybox_status == "WINNING").count()
    losing_count = active_query.filter(MakroPiggybackItem.buybox_status == "LOSING").count()
    floor_hit_count = active_query.filter(MakroPiggybackItem.buybox_status == "FLOOR_HIT").count()
    missing_floor_count = active_query.filter(
        (MakroPiggybackItem.min_price_floor == None) | (MakroPiggybackItem.min_price_floor <= 0)
    ).count()

    staging_count = unabandoned.filter(MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"])).count()
    blocked_count = unabandoned.filter(
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
        "blocked_count": blocked_count,
        "abandoned_count": abandoned_count
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
            "store_id": it.store_id,
            "store_name": it.store.name if it.store else f"店铺#{it.store_id}",
            "status": it.status,
            "seller_sku": it.seller_sku,
            "target_price": it.target_price,
            "buybox_status": it.buybox_status or "UNKNOWN",
            "auto_reprice": it.auto_reprice,
            "min_price_floor": it.min_price_floor or 0.0,
            "is_abandoned": bool(it.is_abandoned),
            "abandoned_reason": it.abandoned_reason or ""
        }

    return {"exists": res}

@router.get("/ids", summary="获取当前过滤条件下的全部跟品商品 ID (支持跨页一键全选)")
def list_piggyback_ids(
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

    if current_user and current_user.role != "ADMIN":
        query = query.filter(MakroPiggybackItem.user_id == current_user.id)
    elif user_id is not None:
        query = query.filter(MakroPiggybackItem.user_id == user_id)

    if isinstance(store_id, int):
        query = query.filter(MakroPiggybackItem.store_id == store_id)

    if stage == "ABANDONED":
        query = query.filter(MakroPiggybackItem.is_abandoned == True)
    else:
        query = query.filter(
            (MakroPiggybackItem.is_abandoned == False) | (MakroPiggybackItem.is_abandoned == None)
        )
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

    ids = [item[0] for item in query.with_entities(MakroPiggybackItem.id).order_by(MakroPiggybackItem.id.desc()).all()]
    return {
        "success": True,
        "ids": ids,
        "total": len(ids)
    }

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

    # 全局与当前阶段总数统计 (用于顶部统计卡片与风控按钮动态徽章)
    stat_query = db.query(MakroPiggybackItem)
    if current_user and current_user.role != "ADMIN":
        stat_query = stat_query.filter(MakroPiggybackItem.user_id == current_user.id)
    elif user_id is not None:
        stat_query = stat_query.filter(MakroPiggybackItem.user_id == user_id)
    if isinstance(store_id, int):
        stat_query = stat_query.filter(MakroPiggybackItem.store_id == store_id)

    abandoned_count = stat_query.filter(MakroPiggybackItem.is_abandoned == True).count()
    unabandoned_stat = stat_query.filter(
        (MakroPiggybackItem.is_abandoned == False) | (MakroPiggybackItem.is_abandoned == None)
    )

    total_all = unabandoned_stat.count()
    pending_count = unabandoned_stat.filter(MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"])).count()
    active_count = unabandoned_stat.filter(MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"])).count()
    failed_count = unabandoned_stat.filter(MakroPiggybackItem.status == "FAILED").count()

    # 计算与当前阶段漏斗匹配的合规风控分布 (让待处理池、在售监控各阶段的风控数字与当前池子总数精准契合)
    stage_stat_query = stat_query
    if stage == "ABANDONED":
        stage_stat_query = stage_stat_query.filter(MakroPiggybackItem.is_abandoned == True)
    else:
        stage_stat_query = stage_stat_query.filter(
            (MakroPiggybackItem.is_abandoned == False) | (MakroPiggybackItem.is_abandoned == None)
        )
        if stage == "STAGING":
            stage_stat_query = stage_stat_query.filter(MakroPiggybackItem.status.in_(["PENDING", "SUBMITTING"]))
        elif stage == "ACTIVE_MONITOR":
            stage_stat_query = stage_stat_query.filter(MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]))
        elif stage == "BLOCKED_FAILED":
            stage_stat_query = stage_stat_query.filter(
                (MakroPiggybackItem.status == "FAILED") |
                (MakroPiggybackItem.compliance_status.in_(["PROHIBITED", "RISK"]))
            )

    pending_check_count = stage_stat_query.filter(MakroPiggybackItem.compliance_status == "PENDING_CHECK").count()
    safe_count = stage_stat_query.filter(MakroPiggybackItem.compliance_status == "SAFE").count()
    risk_count = stage_stat_query.filter(MakroPiggybackItem.compliance_status == "RISK").count()
    prohibited_count = stage_stat_query.filter(MakroPiggybackItem.compliance_status == "PROHIBITED").count()

    # 应用阶段漏斗筛选 (STAGING, ACTIVE_MONITOR, BLOCKED_FAILED, ABANDONED)
    if stage == "ABANDONED":
        query = query.filter(MakroPiggybackItem.is_abandoned == True)
    else:
        # 常规阶段默认排除已弃用商品
        query = query.filter(
            (MakroPiggybackItem.is_abandoned == False) | (MakroPiggybackItem.is_abandoned == None)
        )
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
            "prohibited_count": prohibited_count,
            "abandoned_count": abandoned_count
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

    # 强一致性校验与自愈: Selling Price 严禁大于 Base Price (MRP)
    if item.target_price and (not item.target_mrp or item.target_price > item.target_mrp):
        item.target_mrp = round(max(float(item.target_price) * 1.5, float(item.target_price) + 30.0), 2)
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
        # 若未指定 target_price，根据新公式自动重新计算售价
        if req.target_price is None and (item.original_price or item.target_price or 0.0) > 0:
            base_p = item.original_price or item.target_price or 0.0
            new_tp, new_tm = MakroPiggybackService.calculate_price(
                original_price=base_p,
                strategy=req.price_strategy,
                min_floor=item.min_price_floor or 0.0,
                original_mrp=item.original_mrp or 0.0
            )
            if abs(new_tp - (item.target_price or 0.0)) >= 0.01:
                item.target_price = new_tp
                item.target_mrp = new_tm
                price_changed = True
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

@router.post("/batch-check-compliance", summary="批量执行 AI 侵权与合规检测 (多线程并发加速与实时进度)")
def batch_check_compliance(
    req: BatchCheckComplianceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    if not items:
        return {"success": True, "total_checked": 0, "message": "未找到需要检测的商品"}

    u_id = current_user.id if current_user else None
    op_name = (current_user.nickname or current_user.username) if current_user else "系统"

    # 读取管理员配置的跟品质检并发线程数 (默认 100 并发，支持 1~100)
    cfg_workers = 100
    try:
        setting_concurrency = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_compliance_concurrency").first()
        if setting_concurrency and setting_concurrency.value:
            cfg_workers = int(setting_concurrency.value)
    except Exception:
        cfg_workers = 100

    concurrency = max(1, min(100, cfg_workers, len(items)))

    task = task_manager.create_task(
        task_type="PIGGYBACK_COMPLIANCE",
        name=f"批量跟品AI合规质检 ({concurrency}线程并发 · 共 {len(items)} 件)",
        total=len(items),
        product_ids=req.ids
    )
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        import time
        import random
        from ..database import SessionLocal

        done = 0
        success_cnt = 0
        risk_or_fail = 0
        progress_lock = threading.Lock()
        results = {}

        def _check_one(it_id: int):
            nonlocal done, success_cnt, risk_or_fail
            if tm.is_cancelled(tid):
                return

            max_retries = 3
            last_err = None
            for attempt in range(max_retries + 1):
                if tm.is_cancelled(tid):
                    return
                thread_db = SessionLocal()
                try:
                    it = thread_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == it_id).first()
                    if not it:
                        return
                    r = MakroPiggybackService.check_compliance_for_item(it, thread_db)

                    # 若因大模型服务抖动或网络超时返回了异常，且仍在重试次数内，则退避重试
                    if r.get("compliance_status") == "RISK" and "合规检测接口异常" in r.get("summary", ""):
                        if attempt < max_retries and not tm.is_cancelled(tid):
                            time.sleep(0.8 * (attempt + 1) + random.uniform(0.1, 0.4))
                            continue

                    with progress_lock:
                        done += 1
                        status = it.compliance_status
                        results[it.id] = {"status": status, "summary": r.get("summary")}
                        if status == "SAFE":
                            success_cnt += 1
                        else:
                            risk_or_fail += 1
                        retry_tip = f" (第{attempt}次重试成功)" if attempt > 0 else ""
                        tm.update_progress(
                            tid,
                            current=done,
                            current_title=f"[{status}] {it.title[:30]}{retry_tip}",
                            success_inc=1 if status == "SAFE" else 0,
                            fail_inc=1 if status != "SAFE" else 0
                        )
                    return
                except Exception as e:
                    last_err = e
                    if attempt < max_retries and not tm.is_cancelled(tid):
                        time.sleep(0.8 * (attempt + 1) + random.uniform(0.1, 0.4))
                        continue
                finally:
                    thread_db.close()

            # 3 次重试全部耗尽后仍异常
            with progress_lock:
                done += 1
                risk_or_fail += 1
                results[it_id] = {"status": "ERROR", "error": f"重试3次后仍失败: {str(last_err)}"}
                tm.update_progress(
                    tid,
                    current=done,
                    current_title=f"检测异常(已重试3次): {str(last_err)[:25]}",
                    fail_inc=1,
                    error=str(last_err)
                )

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_check_one, it.id) for it in items]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    logger.error(f"批量合规检测子任务异常: {e}")

        audit_db = SessionLocal()
        try:
            tm.finish_task(
                tid,
                status="SUCCESS",
                message=f"跟品合规检测完成: 共 {len(items)} 件 ({concurrency}线程并发, 安全 {success_cnt} 件, 风险/拦截 {risk_or_fail} 件)",
                success_count=success_cnt,
                fail_count=risk_or_fail
            )
            record_audit_log(
                task_type="PIGGYBACK_COMPLIANCE",
                status="SUCCESS",
                message=f"批量跟品合规检测: 完成 {len(items)} 件商品排查 ({concurrency}线程并发, 安全 {success_cnt}, 风险 {risk_or_fail})",
                detail_logs={"total": len(items), "concurrency": concurrency, "results": results},
                user_id=u_id,
                operator_name=op_name,
                db=audit_db
            )
        finally:
            audit_db.close()

    task_manager.start_task(task_id, _worker)

    return {
        "success": True,
        "task_id": task_id,
        "task_name": task["name"],
        "concurrency": concurrency,
        "total_requested": len(items),
        "message": f"已成功启动 {len(items)} 件商品的异步合规检测任务 ({concurrency}线程并发)"
    }

@router.post("/arbitrate/{item_id}", summary="人工终审仲裁跟品合规判定")
def arbitrate_piggyback_compliance(
    item_id: int,
    req: ArbitratePiggybackComplianceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    human = req.human_verdict.upper()
    if human not in ["SAFE", "RISK", "PROHIBITED"]:
        raise HTTPException(status_code=400, detail="裁决状态必须为 SAFE, RISK 或 PROHIBITED")

    details = {}
    if item.compliance_details:
        try:
            details = json.loads(item.compliance_details)
        except Exception:
            details = {}

    qwen_verdict = details.get("qwen_verdict", {})
    deepseek_verdict = details.get("deepseek_verdict", {})
    qwen_status = qwen_verdict.get("overall_risk", "SAFE")
    deepseek_status = deepseek_verdict.get("overall_risk", "SAFE")
    has_dual = details.get("dual_ai_mode", False)

    # 智能归因分析
    if has_dual:
        if qwen_status == human and deepseek_status == human:
            attribution = "CONSENSUS_AFFIRMED"
        elif qwen_status != human and deepseek_status == human:
            attribution = "QWEN_FALSE_POSITIVE" if (qwen_status in ["RISK", "PROHIBITED"] and human == "SAFE") else "QWEN_FALSE_NEGATIVE"
        elif deepseek_status != human and qwen_status == human:
            attribution = "DEEPSEEK_FALSE_POSITIVE" if (deepseek_status in ["RISK", "PROHIBITED"] and human == "SAFE") else "DEEPSEEK_FALSE_NEGATIVE"
        else:
            attribution = "BOTH_MISJUDGED"
    else:
        attribution = "SINGLE_AI_AFFIRMED" if qwen_status == human else "SINGLE_AI_OVERRULED"

    # 更新商品合规状态与细节快照
    item.compliance_status = human
    details["compliance_status"] = human
    details["is_disputed"] = False
    details["human_arbitration"] = {
        "human_verdict": human,
        "notes": req.human_notes or "",
        "error_attribution": attribution,
        "arbitrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator": current_user.nickname or current_user.username if current_user else "admin"
    }
    item.compliance_details = json.dumps(details, ensure_ascii=False)
    db.commit()
    db.refresh(item)

    record_audit_log(
        task_type="PIGGYBACK_ARBITRATION",
        status="SUCCESS",
        message=f"跟品商品 (ID {item.id}) 人工终审裁定为 [{human}], 归因标注=[{attribution}]",
        detail_logs={
            "item_id": item.id,
            "human_verdict": human,
            "attribution": attribution,
            "notes": req.human_notes
        },
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )

    return {
        "success": True,
        "item_id": item.id,
        "compliance_status": item.compliance_status,
        "compliance_details": details
    }


@router.post("/batch-apply-pricing", summary="批量应用智能比价策略")
def batch_apply_pricing(
    req: BatchApplyPricingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    updated_count = 0
    synced_count = 0

    from ..services.auto_reprice_service import AutoRepriceService

    for it in items:
        floor = req.min_price_floor if req.min_price_floor is not None else it.min_price_floor
        old_tp = it.target_price or 0.0
        base_p = it.original_price or it.target_price or 0.0
        tp, tm = MakroPiggybackService.calculate_price(
            original_price=base_p,
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

        # 若勾选了即时同步且已在售，向官方推送新价格
        if req.sync_to_makro and it.status in ["ACTIVE", "PUBLISHED"] and abs(tp - old_tp) >= 0.01:
            try:
                store = it.store or _get_target_store(db, it.store_id)
                AutoRepriceService._push_price_to_makro(it, store, tp)
                it.last_reprice_at = datetime.now()
                it.last_reprice_result = f"STRATEGY_SYNC: 批量公式切换为 {req.price_strategy}，同步售价 R{tp}"
                synced_count += 1
            except Exception as push_err:
                logger.warning(f"批量调价同步官方失败 [{it.seller_sku}]: {push_err}")

    db.commit()
    record_audit_log(
        task_type="BATCH_PRICING",
        status="SUCCESS",
        message=f"批量更新跟价公式: {updated_count} 件商品策略改为 [{req.price_strategy}], 实时同步官方 {synced_count} 件",
        detail_logs={"total": len(req.ids), "strategy": req.price_strategy, "updated": updated_count, "synced": synced_count},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {"success": True, "updated_count": updated_count, "synced_count": synced_count}

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
    items = db.query(MakroPiggybackItem).filter(
        MakroPiggybackItem.id.in_(req.ids),
        MakroPiggybackItem.compliance_status != "PROHIBITED"
    ).all()
    if not items:
        raise HTTPException(status_code=400, detail="选中的商品均为侵权禁售品或不存在，已被系统安全拦截")

    valid_ids = [it.id for it in items]
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
        product_ids=valid_ids
    )
    task_id = task["id"]
    target_store_id = target_store.id if target_store else None

    def _worker(tm: TaskManager, tid: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        from ..database import SessionLocal
        from ..models.setting import SystemSetting

        init_db = SessionLocal()
        try:
            setting_concurrency = init_db.query(SystemSetting).filter(SystemSetting.key == "publish_concurrency").first()
            try:
                cfg_workers = int(setting_concurrency.value) if (setting_concurrency and setting_concurrency.value) else 2
            except Exception:
                cfg_workers = 2
        finally:
            init_db.close()

        concurrency = max(1, min(5, cfg_workers, len(req.ids)))
        done = 0
        errs = 0
        progress_lock = threading.Lock()

        def _publish_one(pid: int):
            nonlocal done, errs
            if tm.is_cancelled(tid):
                return
            thread_db = SessionLocal()
            try:
                p_item = thread_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == pid).first()
                if not p_item:
                    return

                if target_store_id:
                    p_store = thread_db.query(Store).filter(Store.id == target_store_id).first()
                    p_item.store_id = target_store_id
                    try:
                        thread_db.commit()
                    except Exception as ce:
                        thread_db.rollback()
                        logger.warning(f"批量跟品变更店铺失败 [{pid}]: {ce}")
                else:
                    p_store = p_item.store or thread_db.query(Store).filter(Store.id == p_item.store_id).first()
                if not p_store:
                    p_store = _get_target_store(thread_db)

                try:
                    MakroPiggybackService.publish_piggyback_listing(p_item, p_store, thread_db)
                    with progress_lock:
                        done += 1
                        tm.update_progress(
                            tid,
                            current=done + errs,
                            current_title=f"已成功挂靠: {p_item.title[:35]}",
                            success_inc=1
                        )
                except Exception as ex:
                    with progress_lock:
                        errs += 1
                        logger.error(f"批量跟品 ID {pid} 挂靠异常: {ex}")
                        tm.update_progress(
                            tid,
                            current=done + errs,
                            current_title=f"挂靠失败: {p_item.title[:35]}",
                            fail_inc=1,
                            error=str(ex)
                        )
            finally:
                thread_db.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_publish_one, pid) for pid in valid_ids]
            for f in as_completed(futures):
                if tm.is_cancelled(tid):
                    break
                try:
                    f.result()
                except Exception as e:
                    logger.error(f"批量跟品挂靠子任务异常: {e}")

        audit_db = SessionLocal()
        try:
            status = "CANCELLED" if tm.is_cancelled(tid) else ("SUCCESS" if done > 0 else "FAILED")
            msg = f"批量挂靠完成: 成功 {done} 件, 失败 {errs} 件 ({concurrency} 线程并发)"
            tm.finish_task(tid, status=status, message=msg)
            record_audit_log(
                task_type="PIGGYBACK_PUBLISH",
                status=status,
                message=msg,
                detail_logs={"total": len(valid_ids), "concurrency": concurrency, "success": done, "failed": errs, "target_store_id": target_store_id, "item_ids": valid_ids[:50]},
                user_id=u_id,
                operator_name=op_name,
                db=audit_db
            )
        finally:
            audit_db.close()

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
    if not req.ids:
        return {"success": True, "deleted_count": 0}
    
    del_count = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).delete(synchronize_session=False)
    db.commit()
    record_audit_log(
        task_type="BATCH_DELETE",
        status="SUCCESS",
        message=f"批量彻底删除 {del_count} 件跟品库商品",
        detail_logs={"ids": req.ids[:20]},
        user_id=current_user.id,
        operator_name=current_user.nickname or current_user.username,
        db=db
    )
    return {"success": True, "deleted_count": del_count}

@router.post("/items/{item_id}/abandon", summary="弃用单件跟品商品 (移入弃用黑名单，防重复采集，联动下架)")
def abandon_piggyback_item(
    item_id: int,
    req: AbandonPiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    delist_msg = ""
    # 若商品已在线上架在售，联动调用 Makro 网关修改状态为 Inactive 并清空库存
    if item.status in ["ACTIVE", "PUBLISHED"]:
        try:
            store = item.store or _get_target_store(db, item.store_id)
            if store and store.seller_id and store.cookie:
                MakroPiggybackService.deactivate_piggyback_listing(
                    item=item,
                    store=store,
                    db=db
                )
                delist_msg = "，且已在 Makro 店铺后台修改为 Inactive 下架并清零库存"
        except Exception as delist_err:
            logger.warning(f"跟品商品 [{item.seller_sku}] 弃用下架官方接口调用异常: {delist_err}")
            delist_msg = f" (注意: 店铺后台自动下架提示: {str(delist_err)[:60]})"

    item.is_abandoned = True
    item.abandoned_reason = req.reason or "侵权违规拦截/手工弃用"
    item.abandoned_at = datetime.now()
    item.auto_reprice = False  # 弃用商品自动关闭自动巡检跟价
    if item.status in ["ACTIVE", "PUBLISHED"]:
        item.status = "INACTIVE"
    db.commit()

    record_audit_log(
        task_type="PIGGYBACK_ABANDON",
        status="SUCCESS",
        message=f"跟品商品已弃用{delist_msg}: {item.title[:35]} (FSN: {item.makro_product_id})，原因: {item.abandoned_reason}",
        detail_logs={"item_id": item.id, "fsn": item.makro_product_id, "reason": item.abandoned_reason},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {"success": True, "message": f"商品已移入弃用黑名单{delist_msg}", "item": _format_piggyback_item(item)}

@router.post("/batch-abandon", summary="批量弃用跟品商品 (防重复采集，联动下架)")
def batch_abandon_piggyback(
    req: BatchAbandonPiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="请至少选择一件商品")

    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    count = 0
    delisted_count = 0
    now = datetime.now()
    reason_txt = req.reason or "批量弃用/侵权拦截"

    # 按店铺聚合在线商品，批量调用官方下架接口 (20个一组聚合分批)
    from collections import defaultdict
    store_active_items = defaultdict(list)
    for it in items:
        if it.status in ["ACTIVE", "PUBLISHED"]:
            s_id = it.store_id
            if s_id:
                store_active_items[s_id].append(it)

    for store_id, s_items in store_active_items.items():
        try:
            store = _get_target_store(db, store_id)
            if store and store.seller_id and store.cookie:
                res = MakroPiggybackService.batch_deactivate_piggyback_listings(
                    items=s_items,
                    store=store,
                    db=db,
                    chunk_size=20
                )
                delisted_count += res.get("success_count", 0)
        except Exception as e:
            logger.warning(f"店铺 ID {store_id} 批量下架跟品异常: {e}")

    for it in items:
        it.is_abandoned = True
        it.abandoned_reason = reason_txt
        it.abandoned_at = now
        it.auto_reprice = False
        if it.status in ["ACTIVE", "PUBLISHED"]:
            it.status = "INACTIVE"
        count += 1

    db.commit()

    delist_note = f"，其中 {delisted_count} 件在线商品已同步在 Makro 店铺后台修改为 Inactive 并清零库存" if delisted_count > 0 else ""
    record_audit_log(
        task_type="PIGGYBACK_ABANDON",
        status="SUCCESS",
        message=f"批量弃用跟品: 成功将 {count} 件商品移入弃用黑名单{delist_note} (原因: {reason_txt})",
        detail_logs={"ids": req.ids, "count": count, "delisted_count": delisted_count, "reason": reason_txt},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {"success": True, "abandoned_count": count, "delisted_count": delisted_count, "message": f"成功弃用 {count} 件商品{delist_note}"}

@router.post("/items/{item_id}/restore", summary="恢复已弃用商品 (移回待处理池)")
def restore_piggyback_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟品商品")

    item.is_abandoned = False
    item.abandoned_reason = None
    item.abandoned_at = None
    if item.status == "INACTIVE":
        item.status = "PENDING"
    db.commit()

    record_audit_log(
        task_type="PIGGYBACK_RESTORE",
        status="SUCCESS",
        message=f"恢复已弃用跟品: {item.title[:35]} (FSN: {item.makro_product_id})",
        detail_logs={"item_id": item.id, "fsn": item.makro_product_id},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {"success": True, "message": "商品已成功恢复", "item": _format_piggyback_item(item)}

@router.post("/batch-restore", summary="批量恢复已弃用商品")
def batch_restore_piggyback(
    req: BatchRestorePiggybackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="请至少选择一件商品")

    items = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id.in_(req.ids)).all()
    count = 0
    for it in items:
        it.is_abandoned = False
        it.abandoned_reason = None
        it.abandoned_at = None
        if it.status == "INACTIVE":
            it.status = "PENDING"
        count += 1
    db.commit()

    record_audit_log(
        task_type="PIGGYBACK_RESTORE",
        status="SUCCESS",
        message=f"批量恢复跟品: 成功恢复 {count} 件商品至活跃池",
        detail_logs={"ids": req.ids, "count": count},
        user_id=current_user.id if current_user else None,
        operator_name=(current_user.nickname or current_user.username) if current_user else None,
        db=db
    )
    return {"success": True, "restored_count": count}


