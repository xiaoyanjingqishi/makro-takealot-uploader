import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from ..database import get_db, SessionLocal
from ..models.user import User, UserStore
from ..models.store import Store
from ..models.makro_listing import MakroListing
from ..services.makro_portal_service import MakroPortalService
from ..services.unified_pricing_service import UnifiedPricingService
from ..services.task_manager import task_manager, TaskManager
from ..utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/store-products", tags=["店铺在线商品管理"])

class SingleProductPriceAdjustRequest(BaseModel):
    store_id: int
    sku_id: Optional[str] = None
    listing_id: Optional[int] = None
    new_price: float
    sync_makro: bool = True
    force: bool = False

class BatchProductPriceAdjustRequest(BaseModel):
    store_id: int
    sku_ids: Optional[List[str]] = None
    listing_ids: Optional[List[int]] = None
    mode: str = "FIXED"  # "FIXED", "INCREASE_AMOUNT", "DECREASE_AMOUNT", "INCREASE_PERCENT", "DECREASE_PERCENT"
    value: float
    sync_makro: bool = True
    force: bool = False

class UpdateInventoryRequest(BaseModel):

    store_id: int
    sku_id: str
    product_id: Optional[str] = None
    inventory: Optional[int] = None
    new_inventory: Optional[int] = None
    location_id: Optional[str] = None

class BatchInventoryRequest(BaseModel):
    store_id: int
    items: List[Dict[str, Any]]  # [{"sku_id": "...", "product_id": "...", "inventory": 999}]
    location_id: Optional[str] = None

class BatchUpdateInventoryActionRequest(BaseModel):
    store_id: int
    new_inventory: int
    sku_ids: Optional[List[str]] = None
    select_all: Optional[bool] = False
    status: Optional[str] = None
    location_id: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None

class DeactivateProductsRequest(BaseModel):
    store_id: int
    sku_ids: Optional[List[str]] = None
    select_all: Optional[bool] = False
    filters: Optional[Dict[str, Any]] = None
    reason_category: Optional[str] = "MANUAL_OFFLINE"  # OUT_OF_STOCK, INFRINGING_RISK, UNPROFITABLE, COMPETITOR_WAR, MANUAL_OFFLINE
    note: Optional[str] = None
    location_id: Optional[str] = None

class ActivateProductsRequest(BaseModel):
    store_id: int
    sku_ids: Optional[List[str]] = None
    select_all: Optional[bool] = False
    filters: Optional[Dict[str, Any]] = None
    recovery_inventory: Optional[int] = 500
    location_id: Optional[str] = None

def _verify_store_access(user: User, store_id: int, db: Session) -> Store:
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if user.role != "ADMIN":
        perm = db.query(UserStore).filter(UserStore.user_id == user.id, UserStore.store_id == store_id).first()
        if not perm:
            raise HTTPException(status_code=403, detail="无权访问该店铺的数据")
    return store

def _build_store_listings_query(store_id: int, filters: Optional[Dict[str, Any]], db: Session):
    query = db.query(MakroListing).filter(MakroListing.store_id == store_id)
    if not filters:
        return query

    target_state = filters.get("status") or filters.get("internal_state") or "ALL"
    if target_state and target_state != "ALL":
        query = query.filter(MakroListing.internal_state == target_state)

    search = filters.get("search")
    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                MakroListing.title.ilike(s),
                MakroListing.sku_id.ilike(s),
                MakroListing.product_id.ilike(s),
                MakroListing.listing_id.ilike(s),
                MakroListing.brand.ilike(s)
            )
        )

    # 1. 售价区间过滤 (SSP)
    min_price = filters.get("min_price")
    if min_price is not None and str(min_price).strip() != "":
        try:
            query = query.filter(MakroListing.ssp >= float(min_price))
        except Exception:
            pass
    max_price = filters.get("max_price")
    if max_price is not None and str(max_price).strip() != "":
        try:
            query = query.filter(MakroListing.ssp <= float(max_price))
        except Exception:
            pass

    # 2. 库存状态与区间过滤 (Inventory)
    inventory_status = filters.get("inventory_status")
    if inventory_status and inventory_status != "ALL":
        if inventory_status == "IN_STOCK":
            query = query.filter(MakroListing.inventory > 10)
        elif inventory_status == "LOW_STOCK":
            query = query.filter(MakroListing.inventory > 0, MakroListing.inventory <= 10)
        elif inventory_status == "OUT_OF_STOCK":
            query = query.filter(or_(MakroListing.inventory <= 0, MakroListing.inventory.is_(None)))

    min_inventory = filters.get("min_inventory")
    if min_inventory is not None and str(min_inventory).strip() != "":
        try:
            query = query.filter(MakroListing.inventory >= int(min_inventory))
        except Exception:
            pass
    max_inventory = filters.get("max_inventory")
    if max_inventory is not None and str(max_inventory).strip() != "":
        try:
            query = query.filter(MakroListing.inventory <= int(max_inventory))
        except Exception:
            pass

    # 3. 类目过滤 (Vertical)
    vertical = filters.get("vertical")
    if vertical and vertical != "ALL":
        query = query.filter(MakroListing.vertical == vertical)

    # 4. 同步时间跨度过滤 (synced_at)
    date_range = filters.get("date_range")
    sync_start = filters.get("sync_start")
    sync_end = filters.get("sync_end")
    now = datetime.now()
    if date_range and date_range != "ALL":
        if date_range == "today":
            start_t = now.replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(MakroListing.synced_at >= start_t)
        elif date_range == "3d":
            query = query.filter(MakroListing.synced_at >= now - timedelta(days=3))
        elif date_range == "7d":
            query = query.filter(MakroListing.synced_at >= now - timedelta(days=7))
        elif date_range == "30d":
            query = query.filter(MakroListing.synced_at >= now - timedelta(days=30))
    else:
        if sync_start and str(sync_start).strip():
            try:
                s_str = str(sync_start).strip()
                s_dt = datetime.strptime(s_str, "%Y-%m-%d") if len(s_str) == 10 else datetime.strptime(s_str, "%Y-%m-%d %H:%M:%S")
                query = query.filter(MakroListing.synced_at >= s_dt)
            except Exception as e:
                logger.warning(f"Invalid sync_start format: {sync_start}: {e}")
        if sync_end and str(sync_end).strip():
            try:
                e_str = str(sync_end).strip()
                e_dt = (datetime.strptime(e_str, "%Y-%m-%d") + timedelta(days=1)) if len(e_str) == 10 else datetime.strptime(e_str, "%Y-%m-%d %H:%M:%S")
                query = query.filter(MakroListing.synced_at <= e_dt)
            except Exception as e:
                logger.warning(f"Invalid sync_end format: {sync_end}: {e}")

    return query

@router.get("/verticals", summary="获取店铺内所有在线商品的类目列表")
def list_store_product_verticals(
    store_id: int = Query(..., description="店铺 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    rows = db.query(
        MakroListing.vertical,
        MakroListing.vertical_display_name,
        func.count(MakroListing.id).label("count")
    ).filter(
        MakroListing.store_id == store.id,
        MakroListing.vertical.isnot(None),
        MakroListing.vertical != ""
    ).group_by(
        MakroListing.vertical,
        MakroListing.vertical_display_name
    ).order_by(
        func.count(MakroListing.id).desc()
    ).all()

    verticals = []
    for r in rows:
        verticals.append({
            "code": r.vertical,
            "name": r.vertical_display_name or r.vertical,
            "count": r.count
        })

    return {"verticals": verticals}

@router.get("/list", summary="查询店铺商品列表 (支持多维过滤、排序与搜索)")
def list_store_products(
    store_id: int = Query(..., description="店铺 ID"),
    internal_state: Optional[str] = Query(None, description="状态: ACTIVE, READY_FOR_ACTIVATION, INACTIVE, INACTIVATED_BY_FLIPKART, ALL"),
    status: Optional[str] = Query(None, description="兼容别名: ACTIVE, INACTIVE, ALL 等"),
    search: Optional[str] = Query(None, description="搜索标题、SKU、FSN"),
    min_price: Optional[float] = Query(None, description="最低售价"),
    max_price: Optional[float] = Query(None, description="最高售价"),
    inventory_status: Optional[str] = Query(None, description="库存状态: ALL, IN_STOCK, LOW_STOCK, OUT_OF_STOCK"),
    min_inventory: Optional[int] = Query(None, description="最小库存"),
    max_inventory: Optional[int] = Query(None, description="最大库存"),
    vertical: Optional[str] = Query(None, description="类目"),
    sync_start: Optional[str] = Query(None, description="同步起始时间"),
    sync_end: Optional[str] = Query(None, description="同步截止时间"),
    date_range: Optional[str] = Query(None, description="快捷时间范围: today, 3d, 7d, 30d, ALL"),
    sort_by: Optional[str] = Query("SYNCED_DESC", description="排序规则: SYNCED_DESC, SYNCED_ASC, PRICE_ASC, PRICE_DESC, INVENTORY_ASC, INVENTORY_DESC"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)

    filters = {
        "status": status,
        "internal_state": internal_state,
        "search": search,
        "min_price": min_price,
        "max_price": max_price,
        "inventory_status": inventory_status,
        "min_inventory": min_inventory,
        "max_inventory": max_inventory,
        "vertical": vertical,
        "sync_start": sync_start,
        "sync_end": sync_end,
        "date_range": date_range,
    }
    query = _build_store_listings_query(store.id, filters, db)

    # 排序规则
    if sort_by == "PRICE_ASC":
        query = query.order_by(MakroListing.ssp.asc().nullslast(), MakroListing.id.desc())
    elif sort_by == "PRICE_DESC":
        query = query.order_by(MakroListing.ssp.desc().nullslast(), MakroListing.id.desc())
    elif sort_by == "INVENTORY_ASC":
        query = query.order_by(MakroListing.inventory.asc().nullslast(), MakroListing.id.desc())
    elif sort_by == "INVENTORY_DESC":
        query = query.order_by(MakroListing.inventory.desc().nullslast(), MakroListing.id.desc())
    elif sort_by == "SYNCED_ASC":
        query = query.order_by(MakroListing.synced_at.asc().nullslast(), MakroListing.id.desc())
    else:  # 默认 SYNCED_DESC
        query = query.order_by(MakroListing.synced_at.desc().nullslast(), MakroListing.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    # 统计各状态计数
    state_counts = {}
    for st in ["ACTIVE", "READY_FOR_ACTIVATION", "INACTIVE", "INACTIVATED_BY_FLIPKART", "ARCHIVED"]:
        state_counts[st] = db.query(MakroListing).filter(MakroListing.store_id == store.id, MakroListing.internal_state == st).count()

    results = []
    for it in items:
        deact_reasons = []
        if it.deactivation_reasons:
            try:
                deact_reasons = json.loads(it.deactivation_reasons)
            except Exception:
                pass

        # 提取关联的跟价监控状态并做真伪合规校验
        rep = it.reprice_monitor[0] if isinstance(it.reprice_monitor, list) and it.reprice_monitor else it.reprice_monitor
        rep_info = None
        if rep:
            rep_comp_st = rep.compliance_status or "PENDING_CHECK"
            if (not rep.compliance_details or rep.compliance_details in ["", "{}", "null"]) and rep_comp_st in ["SAFE", "RISK"]:
                rep_comp_st = "PENDING_CHECK"
            rep_info = {
                "id": rep.id,
                "auto_reprice": rep.auto_reprice,
                "monitor_status": rep.monitor_status,
                "source_type": rep.source_type,
                "buybox_status": rep.buybox_status,
                "min_price_floor": rep.min_price_floor,
                "price_strategy": rep.price_strategy,
                "compliance_status": rep_comp_st,
                "target_fsn": rep.target_fsn,
                "target_makro_url": rep.target_makro_url
            }

        results.append({
            "id": it.id,
            "store_id": it.store_id,
            "seller_id": it.seller_id,
            "sku_id": it.sku_id,
            "product_id": it.product_id,
            "listing_id": it.listing_id,
            "title": it.title,
            "brand": it.brand,
            "vertical": it.vertical,
            "vertical_display_name": it.vertical_display_name,
            "image_url": it.image_url,
            "internal_state": it.internal_state,
            "ssp": it.ssp,
            "mrp": it.mrp,
            "inventory": it.inventory,
            "deactivation_reasons": deact_reasons,
            "deactivation_reason_category": getattr(it, "deactivation_reason_category", None),
            "deactivation_note": getattr(it, "deactivation_note", None),
            "deactivated_at": it.deactivated_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(it, "deactivated_at", None) else "",
            "local_product_id": it.local_product_id,
            "reprice": rep_info,
            "synced_at": it.synced_at.strftime("%Y-%m-%d %H:%M:%S") if it.synced_at else ""
        })

    return {
        "store": {"id": store.id, "name": store.name, "seller_id": store.seller_id},
        "total": total,
        "page": page,
        "page_size": page_size,
        "state_counts": state_counts,
        "items": results
    }

@router.post("/sync", summary="从 Makro 官方网关同步店铺在线商品")
def sync_store_products(
    store_id: int = Query(..., description="店铺 ID"),
    concurrency: int = Query(8, ge=1, le=20, description="并发抓取线程数"),
    async_task: bool = Query(True, description="是否后台异步执行 (默认 True)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    if not async_task:
        try:
            res = MakroPortalService.sync_store_listings(store, db, concurrency=concurrency)
            return {
                "success": True,
                "message": f"店铺 '{store.name}' 商品同步完成，共同步 {res['total_synced']} 件商品",
                "state_counts": res.get("state_counts", {})
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"同步商品失败: {str(e)}")

    task = task_manager.create_task(
        task_type="SYNC_STORE_LISTINGS",
        name=f"同步官方商品 [{store.name}] ({concurrency}并发)",
        total=0
    )
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        thread_db = SessionLocal()
        try:
            def _progress(cur: int, tot: int, msg: str):
                tm.update_progress(tid, current=cur, total=tot, current_title=msg)

            res = MakroPortalService.sync_store_listings(
                store=store,
                db=thread_db,
                concurrency=concurrency,
                progress_callback=_progress,
                is_cancelled_fn=lambda: tm.is_cancelled(tid)
            )
            tm.finish_task(
                tid,
                status="SUCCESS",
                message=f"店铺 '{store.name}' 商品同步完成，共同步 {res['total_synced']} 件商品",
                success_count=res.get("total_synced", 0)
            )
        except Exception as ex:
            logger.error(f"同步店铺 [{store.name}] 官方商品后台任务异常: {ex}", exc_info=True)
            tm.finish_task(
                tid,
                status="FAILED",
                message=f"同步失败: {str(ex)}"
            )
        finally:
            thread_db.close()

    task_manager.start_task(task_id, _worker)
    return {
        "success": True,
        "task_id": task_id,
        "message": f"已在后台启动店铺 '{store.name}' 官方商品多线程同步任务",
        "concurrency": concurrency
    }

@router.post("/adjust-price", summary="在店铺商品中直接调整售价并全端联动")
def adjust_store_product_price(
    req: SingleProductPriceAdjustRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    店铺商品列表单品直接修改售价：
    1. 校验底价防穿
    2. 推送 Makro 官方网关
    3. 级联同步 MakroListing, StoreProductReprice, MakroPiggybackItem
    """
    store = _verify_store_access(current_user, req.store_id, db)
    res = UnifiedPricingService.adjust_single_price(
        store=store,
        db=db,
        new_price=req.new_price,
        listing_id=req.listing_id,
        seller_sku=req.sku_id,
        sync_makro=req.sync_makro,
        force=req.force,
        operator=current_user.username
    )
    if not res.get("success") and not res.get("below_floor"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@router.post("/batch-adjust-price", summary="在店铺商品中批量调整售价并全端联动")
def batch_adjust_store_product_prices(
    req: BatchProductPriceAdjustRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    店铺商品列表批量调整选中商品售价：
    支持统一固定价、按金额增减、按百分比增减
    """
    store = _verify_store_access(current_user, req.store_id, db)
    query = db.query(MakroListing).filter(MakroListing.store_id == store.id)
    if req.listing_ids:
        query = query.filter(MakroListing.id.in_(req.listing_ids))
    elif req.sku_ids:
        query = query.filter(MakroListing.sku_id.in_(req.sku_ids))
    else:
        raise HTTPException(status_code=400, detail="请至少选择一个商品进行调价")

    listings = query.all()
    if not listings:
        raise HTTPException(status_code=404, detail="未找到选中的商品 Listing")

    items_payload = []
    for l in listings:
        curr_p = float(l.ssp or 0.0)
        if req.mode == "FIXED":
            calc_p = round(req.value, 2)
        elif req.mode == "INCREASE_AMOUNT":
            calc_p = round(curr_p + req.value, 2)
        elif req.mode == "DECREASE_AMOUNT":
            calc_p = max(1.0, round(curr_p - req.value, 2))
        elif req.mode == "INCREASE_PERCENT":
            calc_p = round(curr_p * (1.0 + req.value / 100.0), 2)
        elif req.mode == "DECREASE_PERCENT":
            calc_p = max(1.0, round(curr_p * (1.0 - req.value / 100.0), 2))
        else:
            calc_p = round(req.value, 2)

        items_payload.append({
            "listing_id": l.id,
            "seller_sku": l.sku_id,
            "new_price": calc_p
        })

    return UnifiedPricingService.batch_adjust_prices(
        store=store,
        db=db,
        items=items_payload,
        sync_makro=req.sync_makro,
        force=req.force,
        operator=current_user.username
    )

@router.post("/update-inventory", summary="行内快速修改商品库存并回写 Makro")

def update_product_inventory(
    req: UpdateInventoryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, req.store_id, db)
    qty = req.inventory if req.inventory is not None else req.new_inventory
    if qty is None:
        raise HTTPException(status_code=400, detail="必须提供库存数值 (inventory 或 new_inventory)")

    prod_id = req.product_id
    local_item = (
        db.query(MakroListing)
        .filter(MakroListing.store_id == store.id, MakroListing.sku_id == req.sku_id)
        .first()
    )
    if not prod_id and local_item:
        prod_id = local_item.product_id
    if not prod_id:
        raise HTTPException(status_code=400, detail=f"未找到 SKU {req.sku_id} 的对应 Product ID，无法调用更新")

    try:
        # 调用 Makro 官方接口
        gateway_res = MakroPortalService.update_inventory(
            store=store,
            sku_id=req.sku_id,
            product_id=prod_id,
            new_inventory=qty,
            location_id=req.location_id
        )

        # 同步更新本地数据库 (MakroListing 与 MakroPiggybackItem)
        if local_item:
            local_item.inventory = qty
            from ..models.makro_piggyback import MakroPiggybackItem
            db.query(MakroPiggybackItem).filter(
                MakroPiggybackItem.store_id == store.id,
                MakroPiggybackItem.seller_sku == req.sku_id
            ).update({MakroPiggybackItem.inventory: qty}, synchronize_session=False)
            db.commit()

        return {
            "success": True,
            "message": f"SKU {req.sku_id} 库存已成功修改为 {qty}",
            "inventory": qty,
            "new_inventory": qty,
            "gateway_response": gateway_res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"修改库存失败: {str(e)}")

@router.post("/batch-update-inventory", summary="批量修改商品库存 (支持勾选列表或全选全店所有商品)")
def batch_update_product_inventory(
    req: BatchUpdateInventoryActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, req.store_id, db)
    if req.new_inventory is None or req.new_inventory < 0:
        raise HTTPException(status_code=400, detail="请提供有效的库存数值 (>= 0)")

    # 1. 确定要更新的商品集合
    if req.select_all:
        filter_dict = dict(req.filters or {})
        if req.status and not filter_dict.get("status"):
            filter_dict["status"] = req.status
        query = _build_store_listings_query(store.id, filter_dict, db)
        listings = query.all()
    else:
        if not req.sku_ids:
            raise HTTPException(status_code=400, detail="未选择任何商品进行批量修改")
        listings = db.query(MakroListing).filter(
            MakroListing.store_id == store.id,
            MakroListing.sku_id.in_(req.sku_ids)
        ).all()

    if not listings:
        raise HTTPException(status_code=400, detail="未找到符合修改条件的商品")

    items_to_update = []
    for it in listings:
        if it.sku_id:
            items_to_update.append({
                "sku_id": it.sku_id,
                "product_id": it.product_id or "",
                "inventory": req.new_inventory
            })

    try:
        gateway_res = MakroPortalService.batch_update_inventory(
            store=store,
            items=items_to_update,
            location_id=req.location_id
        )

        success_count = gateway_res.get("success_count", 0)
        failed_count = gateway_res.get("failed_count", 0)

        # 针对 Makro 回写成功的 SKU，批量更新本地数据库 (MakroListing 与 MakroPiggybackItem)
        success_skus = [sku for sku, res in gateway_res.get("results", {}).items() if res.get("status") == "SUCCESS"]
        if success_skus:
            (
                db.query(MakroListing)
                .filter(MakroListing.store_id == store.id, MakroListing.sku_id.in_(success_skus))
                .update({MakroListing.inventory: req.new_inventory}, synchronize_session=False)
            )
            from ..models.makro_piggyback import MakroPiggybackItem
            (
                db.query(MakroPiggybackItem)
                .filter(MakroPiggybackItem.store_id == store.id, MakroPiggybackItem.seller_sku.in_(success_skus))
                .update({MakroPiggybackItem.inventory: req.new_inventory}, synchronize_session=False)
            )
            db.commit()

        is_success = success_count > 0
        if is_success:
            msg = f"批量修改完成：成功 {success_count} 件" + (f"，失败 {failed_count} 件" if failed_count > 0 else "")
        else:
            first_err = next((r.get("detail") for r in gateway_res.get("results", {}).values() if r.get("detail")), "官方网关未确认成功")
            msg = f"批量修改失败：0/{len(items_to_update)} 件成功，原因: {first_err}"

        return {
            "success": is_success,
            "total_selected": len(items_to_update),
            "success_count": success_count,
            "failed_count": failed_count,
            "message": msg,
            "details": gateway_res.get("results", {})
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量修改库存失败: {str(e)}")

@router.get("/in-progress", summary="查询在途审核中的商品进度与驳回原因")
def get_in_progress_listings(
    store_id: int = Query(..., description="店铺 ID"),
    page_no: int = Query(0, ge=0),
    page_size: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    try:
        data = MakroPortalService.fetch_in_progress_listings(store, page_no=page_no, page_size=page_size)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询在途审核异常: {str(e)}")

@router.post("/deactivate", summary="批量下架店铺商品 (支持选择下架原因并清零库存)")
def deactivate_store_products(
    req: DeactivateProductsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, req.store_id, db)
    target_skus = req.sku_ids or []
    if req.select_all:
        filter_dict = dict(req.filters or {})
        q = _build_store_listings_query(store.id, filter_dict, db)
        target_skus = [r[0] for r in q.with_entities(MakroListing.sku_id).filter(MakroListing.sku_id.isnot(None), MakroListing.sku_id != "").all()]

    if not target_skus:
        raise HTTPException(status_code=400, detail="未选择任何需要下架的商品")

    try:
        res = MakroPortalService.deactivate_store_listings(
            store=store,
            sku_ids=target_skus,
            reason_category=req.reason_category or "MANUAL_OFFLINE",
            note=req.note,
            db=db,
            location_id=req.location_id
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量下架失败: {str(e)}")

@router.post("/activate", summary="批量激活上架店铺商品 (支持指定恢复库存并唤醒监控)")
def activate_store_products(
    req: ActivateProductsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, req.store_id, db)
    target_skus = req.sku_ids or []
    if req.select_all:
        filter_dict = dict(req.filters or {})
        q = _build_store_listings_query(store.id, filter_dict, db)
        target_skus = [r[0] for r in q.with_entities(MakroListing.sku_id).filter(MakroListing.sku_id.isnot(None), MakroListing.sku_id != "").all()]

    if not target_skus:
        raise HTTPException(status_code=400, detail="未选择任何需要激活上架的商品")

    try:
        res = MakroPortalService.activate_store_listings(
            store=store,
            sku_ids=target_skus,
            recovery_inventory=req.recovery_inventory or 500,
            db=db,
            location_id=req.location_id
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量上架失败: {str(e)}")
