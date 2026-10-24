import logging
import json
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_, func, and_, case

from pydantic import BaseModel
from ..database import get_db, SessionLocal
from ..models.user import User, UserStore
from ..models.store import Store
from ..models.setting import SystemSetting
from ..models.makro_listing import MakroListing
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.store_product_reprice import StoreProductReprice
from ..models.makro_reprice_log import MakroRepriceLog
from ..services.auto_reprice_service import AutoRepriceService
from ..services.auto_reprice_scheduler import auto_reprice_scheduler
from ..services.makro_portal_service import MakroPortalService
from ..services.compliance_service import ComplianceService
from ..services.unified_pricing_service import UnifiedPricingService
from ..services.task_manager import task_manager, TaskManager
from ..utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reprice", tags=["Makro 智能跟价系统"])

# =========================================================================
# 请求参数模型
# =========================================================================
class SingleRepriceAdjustRequest(BaseModel):
    id: Optional[int] = None
    listing_id: Optional[int] = None
    seller_sku: Optional[str] = None
    new_price: float
    sync_makro: bool = True
    force: bool = False

class BatchRepriceAdjustRequest(BaseModel):
    store_id: Optional[int] = None
    ids: List[int]
    mode: str = "FIXED"  # "FIXED", "INCREASE_AMOUNT", "DECREASE_AMOUNT", "INCREASE_PERCENT", "DECREASE_PERCENT"
    value: float
    sync_makro: bool = True
    force: bool = False

class BatchRepriceRequest(BaseModel):

    ids: Optional[List[int]] = None
    store_id: Optional[int] = None
    force: Optional[bool] = False

class FullCruiseRequest(BaseModel):
    store_id: Optional[int] = None
    concurrency: Optional[int] = None

class BatchStrategyRequest(BaseModel):
    ids: List[int]
    price_strategy: str

class BatchFloorRequest(BaseModel):
    ids: List[int]
    min_price_floor: Optional[float] = 0.0
    mode: Optional[str] = "FIXED"  # "FIXED" | "PERCENT"
    percent: Optional[float] = 75.0

class BatchToggleAutoRepriceRequest(BaseModel):
    ids: List[int]
    auto_reprice: bool

class BatchDelistRepriceRequest(BaseModel):
    store_id: Optional[int] = None
    ids: Optional[List[int]] = None
    select_all: Optional[bool] = False
    filters: Optional[Dict[str, Any]] = None
    reason_category: Optional[str] = "MANUAL_OFFLINE"
    note: Optional[str] = "跟价监控批量下架操作"

class UpdateItemConfigRequest(BaseModel):
    auto_reprice: Optional[bool] = None
    min_price_floor: Optional[float] = None
    max_price_ceiling: Optional[float] = None
    price_strategy: Optional[str] = None
    target_fsn: Optional[str] = None
    target_makro_url: Optional[str] = None

class BindCompetitorRequest(BaseModel):
    store_id: int
    listing_id: int
    target_fsn: str
    target_makro_url: Optional[str] = None
    min_price_floor: float = 0.0
    price_strategy: str = "MINUS_1"
    auto_reprice: bool = True

class BatchComplianceCheckRequest(BaseModel):
    ids: Optional[List[int]] = None
    store_id: Optional[int] = None

class RepriceArbitrationRequest(BaseModel):
    human_verdict: str  # SAFE, RISK, PROHIBITED
    human_notes: Optional[str] = ""

def _verify_store_access(user: User, store_id: int, db: Session) -> Store:
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if user.role != "ADMIN":
        perm = db.query(UserStore).filter(UserStore.user_id == user.id, UserStore.store_id == store_id).first()
        if not perm:
            raise HTTPException(status_code=403, detail="无权访问该店铺的数据")
    return store

# =========================================================================
# 1. 在售跟价监控列表大盘与 KPI 统计
# =========================================================================
_REPRICE_KPI_CACHE: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}
_REPRICE_KPI_CACHE_TTL: float = 15.0  # 15 秒短时防抖与复用缓存

def invalidate_reprice_kpi_cache():
    """清理在售调价 KPI 缓存"""
    _REPRICE_KPI_CACHE.clear()

@router.get("/list", summary="查询在售跟价监控商品列表与大盘 KPI 态势")
def list_reprice_monitor_items(
    store_id: Optional[int] = Query(None, description="店铺 ID"),
    source_type: Optional[str] = Query(None, description="来源: PIGGYBACK, SELF_PUBLISHED, STORE_SYNCED, ALL"),
    buybox_status: Optional[str] = Query(None, description="Buybox: WINNING, LOSING, FLOOR_HIT, NO_COMPETITOR, BLOCKED, UNKNOWN, ALL"),
    compliance_status: Optional[str] = Query(None, description="合规: SAFE, RISK, PROHIBITED, PENDING_CHECK, ALL"),
    auto_reprice: Optional[str] = Query(None, description="跟价开关: true, false, ALL"),
    monitor_status: Optional[str] = Query(None, description="监控状态: ACTIVE, PAUSED, OFFLINE_SUSPENDED, PENDING_CONFIG, BLOCKED, ALL"),
    search: Optional[str] = Query(None, description="搜索 SKU、标题、FSN、竞对店名"),
    min_my_price: Optional[float] = Query(None, description="最低我店售价 (ZAR)"),
    max_my_price: Optional[float] = Query(None, description="最高我店售价 (ZAR)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 店铺权限过滤
    accessible_stores = []
    if current_user.role == "ADMIN":
        accessible_stores = [s.id for s in db.query(Store.id).all()]
    else:
        accessible_stores = [p.store_id for p in db.query(UserStore.store_id).filter(UserStore.user_id == current_user.id).all()]

    if store_id:
        if store_id not in accessible_stores:
            raise HTTPException(status_code=403, detail="无权访问该店铺")
        target_store_ids = [store_id]
    else:
        target_store_ids = accessible_stores

    # 严格限定只查询官方在售 ACTIVE 商品
    base_query = db.query(StoreProductReprice).join(MakroListing, StoreProductReprice.listing_id == MakroListing.id).filter(
        StoreProductReprice.store_id.in_(target_store_ids),
        MakroListing.internal_state == "ACTIVE"
    )

    # 统计全局 KPI 计数 (针对选定店铺或全部授权店铺，严格仅计 ACTIVE 在售品)
    # 单条 SQL 极速条件聚合，取代 15 次独立的 JOIN count(*)，并使用 15s 短时缓存
    global _REPRICE_KPI_CACHE
    kpi_cache_key = tuple(sorted(target_store_ids))
    now_ts = time.time()
    cached_kpi = _REPRICE_KPI_CACHE.get(kpi_cache_key)
    if cached_kpi and (now_ts - cached_kpi[0] < _REPRICE_KPI_CACHE_TTL):
        kpi_dict = cached_kpi[1]
    else:
        kpi_query = db.query(StoreProductReprice).join(MakroListing, StoreProductReprice.listing_id == MakroListing.id).filter(
            StoreProductReprice.store_id.in_(target_store_ids),
            MakroListing.internal_state == "ACTIVE"
        )
        row = kpi_query.with_entities(
            func.count().label("total_monitored"),
            func.sum(case((and_(StoreProductReprice.auto_reprice == True, StoreProductReprice.monitor_status == "ACTIVE"), 1), else_=0)),
            func.sum(case((StoreProductReprice.buybox_status == "WINNING", 1), else_=0)),
            func.sum(case((StoreProductReprice.buybox_status == "LOSING", 1), else_=0)),
            func.sum(case((StoreProductReprice.buybox_status == "FLOOR_HIT", 1), else_=0)),
            func.sum(case((StoreProductReprice.buybox_status == "NO_COMPETITOR", 1), else_=0)),
            func.sum(case((or_(StoreProductReprice.monitor_status == "BLOCKED", StoreProductReprice.compliance_status == "PROHIBITED"), 1), else_=0)),
            func.sum(case((and_(StoreProductReprice.compliance_status == "SAFE", StoreProductReprice.compliance_details.isnot(None), StoreProductReprice.compliance_details != "", StoreProductReprice.compliance_details != "null", StoreProductReprice.compliance_details != "{}"), 1), else_=0)),
            func.sum(case((StoreProductReprice.compliance_status == "RISK", 1), else_=0)),
            func.sum(case((or_(StoreProductReprice.compliance_status == "PENDING_CHECK", StoreProductReprice.compliance_status.is_(None), StoreProductReprice.compliance_details.is_(None), StoreProductReprice.compliance_details == "", StoreProductReprice.compliance_details == "null", StoreProductReprice.compliance_details == "{}"), 1), else_=0)),
            func.sum(case((or_(StoreProductReprice.auto_reprice == False, StoreProductReprice.monitor_status == "PAUSED"), 1), else_=0)),
            func.sum(case((or_(StoreProductReprice.min_price_floor <= 0, StoreProductReprice.min_price_floor.is_(None)), 1), else_=0)),
            func.sum(case((StoreProductReprice.source_type == "PIGGYBACK", 1), else_=0)),
            func.sum(case((StoreProductReprice.source_type == "SELF_PUBLISHED", 1), else_=0)),
            func.sum(case((StoreProductReprice.source_type == "STORE_SYNCED", 1), else_=0)),
        ).first()

        kpi_dict = {
            "total_monitored": int(row[0] or 0) if row else 0,
            "auto_reprice_enabled": int(row[1] or 0) if row else 0,
            "winning_count": int(row[2] or 0) if row else 0,
            "losing_count": int(row[3] or 0) if row else 0,
            "floor_hit_count": int(row[4] or 0) if row else 0,
            "no_competitor_count": int(row[5] or 0) if row else 0,
            "blocked_count": int(row[6] or 0) if row else 0,
            "safe_count": int(row[7] or 0) if row else 0,
            "risk_count": int(row[8] or 0) if row else 0,
            "pending_check_count": int(row[9] or 0) if row else 0,
            "paused_count": int(row[10] or 0) if row else 0,
            "missing_floor_count": int(row[11] or 0) if row else 0,
            "piggyback_count": int(row[12] or 0) if row else 0,
            "self_published_count": int(row[13] or 0) if row else 0,
            "store_synced_count": int(row[14] or 0) if row else 0,
        }
        _REPRICE_KPI_CACHE[kpi_cache_key] = (now_ts, kpi_dict)

    total_monitored = kpi_dict["total_monitored"]
    auto_reprice_enabled = kpi_dict["auto_reprice_enabled"]
    winning_count = kpi_dict["winning_count"]
    losing_count = kpi_dict["losing_count"]
    floor_hit_count = kpi_dict["floor_hit_count"]
    no_competitor_count = kpi_dict["no_competitor_count"]
    blocked_count = kpi_dict["blocked_count"]
    safe_count = kpi_dict["safe_count"]
    risk_count = kpi_dict["risk_count"]
    pending_check_count = kpi_dict["pending_check_count"]
    paused_count = kpi_dict["paused_count"]
    missing_floor_count = kpi_dict["missing_floor_count"]
    piggyback_count = kpi_dict["piggyback_count"]
    self_published_count = kpi_dict["self_published_count"]
    store_synced_count = kpi_dict["store_synced_count"]

    # 应用多维筛选
    query = base_query
    if source_type and source_type != "ALL":
        query = query.filter(StoreProductReprice.source_type == source_type)

    if buybox_status and buybox_status != "ALL":
        if buybox_status == "MISSING_FLOOR":
            query = query.filter(or_(StoreProductReprice.min_price_floor <= 0, StoreProductReprice.min_price_floor.is_(None)))
        else:
            query = query.filter(StoreProductReprice.buybox_status == buybox_status)

    if compliance_status and compliance_status != "ALL":
        if compliance_status == "PENDING_CHECK":
            query = query.filter(
                or_(
                    StoreProductReprice.compliance_status == "PENDING_CHECK",
                    StoreProductReprice.compliance_status.is_(None),
                    StoreProductReprice.compliance_details.is_(None),
                    StoreProductReprice.compliance_details == "",
                    StoreProductReprice.compliance_details == "null",
                    StoreProductReprice.compliance_details == "{}"
                )
            )
        elif compliance_status == "SAFE":
            query = query.filter(
                StoreProductReprice.compliance_status == "SAFE",
                StoreProductReprice.compliance_details.isnot(None),
                StoreProductReprice.compliance_details != "",
                StoreProductReprice.compliance_details != "null",
                StoreProductReprice.compliance_details != "{}"
            )
        else:
            query = query.filter(StoreProductReprice.compliance_status == compliance_status)

    if auto_reprice and auto_reprice != "ALL":
        bool_val = auto_reprice.lower() in ["true", "1", "yes"]
        query = query.filter(StoreProductReprice.auto_reprice == bool_val)

    if monitor_status and monitor_status != "ALL":
        query = query.filter(StoreProductReprice.monitor_status == monitor_status)

    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                StoreProductReprice.seller_sku.ilike(s),
                StoreProductReprice.makro_product_id.ilike(s),
                StoreProductReprice.target_fsn.ilike(s),
                StoreProductReprice.current_competitor_seller.ilike(s),
                MakroListing.title.ilike(s),
                MakroListing.brand.ilike(s)
            )
        )

    if min_my_price is not None and isinstance(min_my_price, (int, float)):
        query = query.filter(MakroListing.ssp >= min_my_price)

    if max_my_price is not None and isinstance(max_my_price, (int, float)):
        query = query.filter(MakroListing.ssp <= max_my_price)

    total = query.count()
    items = (
        query.options(
            joinedload(StoreProductReprice.listing),
            joinedload(StoreProductReprice.source_piggyback)
        )
        .order_by(desc(StoreProductReprice.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    results = []
    for rep in items:
        lst = rep.listing

        # 若合规详情为空且为跟品挂靠来源，自动关联跟品表补齐 AI 检测详情
        raw_cd = rep.compliance_details
        if (not raw_cd or raw_cd in ["{}", "null", ""]) and (rep.source_type == "PIGGYBACK" or rep.source_piggyback_id):
            pb = rep.source_piggyback
            if not pb:
                pb = db.query(MakroPiggybackItem).filter(
                    MakroPiggybackItem.store_id == rep.store_id,
                    or_(
                        MakroPiggybackItem.seller_sku == rep.seller_sku,
                        MakroPiggybackItem.makro_product_id == rep.makro_product_id
                    )
                ).first()
            if pb and pb.compliance_details:
                raw_cd = pb.compliance_details
                if rep.compliance_status in [None, "SAFE", "PENDING_CHECK"] and pb.compliance_status:
                    rep.compliance_status = pb.compliance_status
                    rep.compliance_details = pb.compliance_details

        parsed_cd = None
        if raw_cd:
            if isinstance(raw_cd, dict):
                parsed_cd = raw_cd
            elif isinstance(raw_cd, str):
                try:
                    parsed_cd = json.loads(raw_cd)
                except Exception:
                    parsed_cd = {"summary": raw_cd}

        comp_st = rep.compliance_status or "PENDING_CHECK"
        if (not parsed_cd or parsed_cd == {}) and comp_st in ["SAFE", "RISK"]:
            comp_st = "PENDING_CHECK"

        results.append({
            "id": rep.id,
            "store_id": rep.store_id,
            "listing_id": rep.listing_id,
            "seller_sku": rep.seller_sku,
            "makro_product_id": rep.makro_product_id,
            "source_type": rep.source_type,
            "source_piggyback_id": rep.source_piggyback_id,

            # 关联的官方 Listing 属性
            "title": lst.title if lst else rep.seller_sku,
            "image_url": lst.image_url if lst else None,
            "brand": lst.brand if lst else None,
            "vertical": lst.vertical if lst else None,
            "ssp": float(lst.ssp or 0.0) if lst else 0.0,
            "mrp": float(lst.mrp or 0.0) if lst else 0.0,
            "inventory": lst.inventory if lst else 0,
            "internal_state": lst.internal_state if lst else "ACTIVE",

            # 竞品与巡检目标
            "target_fsn": rep.target_fsn or rep.makro_product_id,
            "target_makro_url": rep.target_makro_url,

            # 跟价控制策略
            "auto_reprice": rep.auto_reprice,
            "price_strategy": rep.price_strategy or "MINUS_1",
            "min_price_floor": float(rep.min_price_floor or 0.0),
            "max_price_ceiling": float(rep.max_price_ceiling or 0.0),

            # 实时 Buybox 与竞对情报
            "buybox_status": rep.buybox_status or "UNKNOWN",
            "current_competitor_price": rep.current_competitor_price,
            "current_competitor_seller": rep.current_competitor_seller,
            "seller_count": rep.seller_count or 1,

            # 合规风控与长效复检
            "compliance_status": comp_st,
            "compliance_details": parsed_cd,
            "last_compliance_check_at": rep.last_compliance_check_at.strftime("%Y-%m-%d %H:%M:%S") if rep.last_compliance_check_at else None,

            # 监控生命周期
            "monitor_status": rep.monitor_status,
            "last_reprice_at": rep.last_reprice_at.strftime("%Y-%m-%d %H:%M:%S") if rep.last_reprice_at else None,
            "last_reprice_result": rep.last_reprice_result,
            "created_at": rep.created_at.strftime("%Y-%m-%d %H:%M:%S") if rep.created_at else None
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "kpi": {
            "total_monitored": total_monitored,
            "auto_reprice_enabled": auto_reprice_enabled,
            "winning_count": winning_count,
            "losing_count": losing_count,
            "floor_hit_count": floor_hit_count,
            "no_competitor_count": no_competitor_count,
            "blocked_count": blocked_count,
            "safe_count": safe_count,
            "risk_count": risk_count,
            "pending_check_count": pending_check_count,
            "paused_count": paused_count,
            "missing_floor_count": missing_floor_count,
            "piggyback_count": piggyback_count,
            "self_published_count": self_published_count,
            "store_synced_count": store_synced_count
        },
        "items": results
    }


@router.get("/all-ids", summary="快速获取符合当前全部筛选条件的在售跟价商品 ID 列表 (用于跨页一键全选)")
def get_all_reprice_ids(
    store_id: Optional[int] = Query(None, description="店铺 ID"),
    source_type: Optional[str] = Query("ALL", description="商品来源类型: ALL, PIGGYBACK, SELF_PUBLISHED, STORE_SYNCED"),
    buybox_status: Optional[str] = Query("ALL", description="Buybox 状态"),
    compliance_status: Optional[str] = Query("ALL", description="合规状态"),
    auto_reprice: Optional[str] = Query("ALL", description="是否开启自动跟价"),
    monitor_status: Optional[str] = Query("ALL", description="监控状态"),
    search: Optional[str] = Query(None, description="搜索 SKU、标题、FSN、竞对店名"),
    min_my_price: Optional[float] = Query(None, description="最低我店售价 (ZAR)"),
    max_my_price: Optional[float] = Query(None, description="最高我店售价 (ZAR)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 店铺权限过滤
    accessible_stores = []
    if current_user.role == "ADMIN":
        accessible_stores = [s.id for s in db.query(Store.id).all()]
    else:
        accessible_stores = [p.store_id for p in db.query(UserStore.store_id).filter(UserStore.user_id == current_user.id).all()]

    if store_id:
        if store_id not in accessible_stores:
            raise HTTPException(status_code=403, detail="无权访问该店铺")
        target_store_ids = [store_id]
    else:
        target_store_ids = accessible_stores

    source_type = str(source_type) if isinstance(source_type, str) else "ALL"
    buybox_status = str(buybox_status) if isinstance(buybox_status, str) else "ALL"
    compliance_status = str(compliance_status) if isinstance(compliance_status, str) else "ALL"
    auto_reprice = str(auto_reprice) if isinstance(auto_reprice, str) else "ALL"
    monitor_status = str(monitor_status) if isinstance(monitor_status, str) else "ALL"
    search = str(search) if isinstance(search, str) else ""

    base_query = (
        db.query(StoreProductReprice.id)
        .join(MakroListing, StoreProductReprice.listing_id == MakroListing.id)
        .filter(StoreProductReprice.store_id.in_(target_store_ids))
    )

    query = base_query
    if source_type and source_type != "ALL":
        query = query.filter(StoreProductReprice.source_type == source_type)

    if buybox_status and buybox_status != "ALL":
        if buybox_status == "MISSING_FLOOR":
            query = query.filter(or_(StoreProductReprice.min_price_floor <= 0, StoreProductReprice.min_price_floor.is_(None)))
        else:
            query = query.filter(StoreProductReprice.buybox_status == buybox_status)

    if compliance_status and compliance_status != "ALL":
        if compliance_status == "PENDING_CHECK":
            query = query.filter(
                or_(
                    StoreProductReprice.compliance_status == "PENDING_CHECK",
                    StoreProductReprice.compliance_status.is_(None),
                    StoreProductReprice.compliance_details.is_(None),
                    StoreProductReprice.compliance_details == "",
                    StoreProductReprice.compliance_details == "null",
                    StoreProductReprice.compliance_details == "{}"
                )
            )
        elif compliance_status == "SAFE":
            query = query.filter(
                StoreProductReprice.compliance_status == "SAFE",
                StoreProductReprice.compliance_details.isnot(None),
                StoreProductReprice.compliance_details != "",
                StoreProductReprice.compliance_details != "null",
                StoreProductReprice.compliance_details != "{}"
            )
        else:
            query = query.filter(StoreProductReprice.compliance_status == compliance_status)

    if auto_reprice and auto_reprice != "ALL":
        bool_val = auto_reprice.lower() in ["true", "1", "yes"]
        query = query.filter(StoreProductReprice.auto_reprice == bool_val)

    if monitor_status and monitor_status != "ALL":
        query = query.filter(StoreProductReprice.monitor_status == monitor_status)

    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                StoreProductReprice.seller_sku.ilike(s),
                StoreProductReprice.makro_product_id.ilike(s),
                StoreProductReprice.target_fsn.ilike(s),
                StoreProductReprice.current_competitor_seller.ilike(s),
                MakroListing.title.ilike(s),
                MakroListing.brand.ilike(s)
            )
        )

    if min_my_price is not None and isinstance(min_my_price, (int, float)):
        query = query.filter(MakroListing.ssp >= min_my_price)

    if max_my_price is not None and isinstance(max_my_price, (int, float)):
        query = query.filter(MakroListing.ssp <= max_my_price)

    ids = [r[0] for r in query.order_by(desc(StoreProductReprice.id)).all()]
    return {
        "success": True,
        "ids": ids,
        "total": len(ids)
    }

# =========================================================================
# 2. 单品/批量/全店即时跟价巡检
# =========================================================================
@router.post("/trigger-item/{item_id}", summary="立即对单件商品执行自动跟价巡检")
def trigger_single_item_reprice(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        item = db.query(StoreProductReprice).filter(StoreProductReprice.id == item_id).first()
        if not item:
            # 兼容老跟品表
            old_item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
            if not old_item:
                raise HTTPException(status_code=404, detail="未找到指定的跟价监控商品")
            res = AutoRepriceService.reprice_single_item(old_item, db, force=True)
        else:
            res = AutoRepriceService.reprice_single_item(item, db, force=True)

        is_success = res.get("status") in ["SUCCESS", "BLOCKED"]
        curr_p = res.get("current_price") or res.get("new_price")
        return {
            "success": is_success,
            "action": res.get("action"),
            "current_price": curr_p,
            "new_price": res.get("new_price"),
            "old_price": res.get("old_price"),
            "buybox_status": res.get("buybox_status"),
            "competitor_price": res.get("competitor_price"),
            "competitor_seller": res.get("competitor_seller"),
            "reason": res.get("reason"),
            "result": res
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"即时单品跟价巡检异常 [ID: {item_id}]: {e}", exc_info=True)
        return {
            "success": False,
            "action": "FAILED",
            "reason": f"巡检内部异常: {str(e)}",
            "detail": f"巡检内部异常: {str(e)}",
            "result": {"status": "FAILED", "action": "FAILED", "reason": str(e)}
        }

@router.post("/trigger-batch", summary="批量立即触发自动跟价巡检 (异步调度并支持实时进度条与中断)")
def trigger_batch_reprice(
    req: BatchRepriceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    target_ids = req.ids or []
    if not target_ids:
        raise HTTPException(status_code=400, detail="请勾选需要执行跟价巡检的商品")

    # 优先在 StoreProductReprice 中匹配
    valid_reprice_ids = [it[0] for it in db.query(StoreProductReprice.id).filter(StoreProductReprice.id.in_(target_ids)).all()]
    is_reprice_monitor = bool(valid_reprice_ids)
    final_ids = valid_reprice_ids if is_reprice_monitor else [it[0] for it in db.query(MakroPiggybackItem.id).filter(MakroPiggybackItem.id.in_(target_ids)).all()]

    if not final_ids:
        raise HTTPException(status_code=404, detail="选中的商品在底册中未找到有效记录")

    cfg_item = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_cruise_concurrency").first()
    system_concurrency = 3
    if cfg_item and cfg_item.value:
        try:
            system_concurrency = int(cfg_item.value)
        except Exception:
            pass
    target_concurrency = max(1, min(system_concurrency, len(final_ids), 100))

    task = task_manager.create_task(
        task_type="FULL_CRUISE_INSPECTION",
        name=f"批量即时巡检 ({len(final_ids)} 件 · {target_concurrency}线程)",
        total=len(final_ids),
        product_ids=final_ids
    )
    task_id = task["id"]

    task_manager.start_task(
        task_id,
        lambda tm, tid: AutoRepriceService.run_full_cruise_task(
            tm=tm,
            task_id=tid,
            item_ids=final_ids,
            concurrency=target_concurrency,
            is_reprice_monitor=is_reprice_monitor
        )
    )

    return {
        "success": True,
        "message": f"已在后台启动 {len(final_ids)} 件商品的批量跟价巡检",
        "task_id": task_id,
        "total": len(final_ids),
        "concurrency": target_concurrency
    }

@router.post("/trigger-full-cruise", summary="全量立刻发起后台平稳巡检巡航任务")
def trigger_full_cruise_reprice(
    req: Optional[FullCruiseRequest] = None,
    store_id: Optional[int] = Query(None),
    concurrency: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    target_store_id = None
    if req and req.store_id is not None:
        target_store_id = req.store_id
    elif isinstance(store_id, int):
        target_store_id = store_id

    cfg_item = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_cruise_concurrency").first()
    system_concurrency = 3
    if cfg_item and cfg_item.value:
        try:
            system_concurrency = int(cfg_item.value)
        except Exception:
            pass

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

    # 优先在 StoreProductReprice 中查询在线监控商品 (按最久未巡检或从未巡检优先排序)
    query = db.query(StoreProductReprice.id).join(MakroListing, StoreProductReprice.listing_id == MakroListing.id).filter(
        StoreProductReprice.monitor_status == "ACTIVE",
        MakroListing.internal_state == "ACTIVE",
        StoreProductReprice.makro_product_id.notlike("TEST_FSN_%")
    )
    if target_store_id:
        query = query.filter(StoreProductReprice.store_id == target_store_id)

    query = query.order_by(
        StoreProductReprice.last_reprice_at.asc().nullsfirst(),
        StoreProductReprice.id.asc()
    )

    item_ids = [it[0] for it in query.all()]
    is_reprice_monitor = True

    if not item_ids:
        # 兜底老跟品表
        query_pb = db.query(MakroPiggybackItem.id).filter(
            MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]),
            MakroPiggybackItem.makro_product_id.notlike("TEST_FSN_%")
        )
        if target_store_id:
            query_pb = query_pb.filter(MakroPiggybackItem.store_id == target_store_id)
        query_pb = query_pb.order_by(
            MakroPiggybackItem.last_reprice_at.asc().nullsfirst(),
            MakroPiggybackItem.id.asc()
        )
        item_ids = [it[0] for it in query_pb.all()]
        is_reprice_monitor = False

    total = len(item_ids)
    if total == 0:
        return {
            "success": True,
            "message": f"【{store_name}】暂无符合巡检条件的在售跟价商品",
            "total": 0,
            "task_id": None
        }

    task = task_manager.create_task(
        task_type="FULL_CRUISE_INSPECTION",
        name=f"全量巡检巡航 [{store_name}] ({total} 件 · {target_concurrency}线程)",
        total=total,
        product_ids=item_ids
    )
    task_id = task["id"]

    task_manager.start_task(
        task_id,
        lambda tm, tid: AutoRepriceService.run_full_cruise_task(
            tm=tm,
            task_id=tid,
            item_ids=item_ids,
            concurrency=target_concurrency,
            is_reprice_monitor=is_reprice_monitor
        )
    )

    return {
        "success": True,
        "message": f"已在后台启动【{store_name}】全量平稳巡检任务 (共 {total} 件)",
        "task_id": task_id,
        "total": total,
        "store_id": target_store_id,
        "store_name": store_name
    }

# =========================================================================
# 3. 批量策略控制与底价风控修改
# =========================================================================
@router.post("/batch-strategy", summary="批量修改商品跟价公式策略")
def batch_update_strategy(
    req: BatchStrategyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="未选择商品")

    count = db.query(StoreProductReprice).filter(
        StoreProductReprice.id.in_(req.ids)
    ).update({StoreProductReprice.price_strategy: req.price_strategy}, synchronize_session=False)
    db.commit()

    return {"success": True, "updated_count": count, "price_strategy": req.price_strategy}

@router.post("/batch-floor", summary="批量修改商品保本底价")
def batch_update_floor(
    req: BatchFloorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="未选择商品")

    if req.mode == "PERCENT":
        pct = max(0.01, float(req.percent or 75.0)) / 100.0
        items = db.query(StoreProductReprice).filter(StoreProductReprice.id.in_(req.ids)).all()
        count = 0
        for it in items:
            lst = it.listing
            cur_price = float(lst.ssp or 0.0) if lst and lst.ssp else 0.0
            if cur_price <= 0 and lst and lst.mrp:
                cur_price = float(lst.mrp)
            floor = max(round(cur_price * pct, 2), 1.0) if cur_price > 0 else max(round(float(req.min_price_floor or 1.0), 2), 1.0)
            it.min_price_floor = floor
            count += 1
        db.commit()
        return {"success": True, "updated_count": count, "mode": "PERCENT", "percent": req.percent}
    else:
        if req.min_price_floor < 0:
            raise HTTPException(status_code=400, detail="保本底价不能小于 0")

        count = db.query(StoreProductReprice).filter(
            StoreProductReprice.id.in_(req.ids)
        ).update({StoreProductReprice.min_price_floor: round(req.min_price_floor, 2)}, synchronize_session=False)
        db.commit()

        return {"success": True, "updated_count": count, "mode": "FIXED", "min_price_floor": req.min_price_floor}

@router.post("/batch-toggle", summary="批量开启/暂停自动跟价开关")
def batch_toggle_auto_reprice(
    req: BatchToggleAutoRepriceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not req.ids:
        raise HTTPException(status_code=400, detail="未选择商品")

    # 若开启跟价，检查是否为违规熔断状态
    if req.auto_reprice:
        count = db.query(StoreProductReprice).filter(
            StoreProductReprice.id.in_(req.ids),
            StoreProductReprice.compliance_status != "PROHIBITED"
        ).update({
            StoreProductReprice.auto_reprice: True,
            StoreProductReprice.monitor_status: "ACTIVE"
        }, synchronize_session=False)
    else:
        count = db.query(StoreProductReprice).filter(
            StoreProductReprice.id.in_(req.ids)
        ).update({
            StoreProductReprice.auto_reprice: False,
            StoreProductReprice.monitor_status: "PAUSED"
        }, synchronize_session=False)

    db.commit()
    return {"success": True, "updated_count": count, "auto_reprice": req.auto_reprice}

@router.post("/adjust-price", summary="直接调整我店售价并全端三表同步")
def adjust_reprice_item_price(
    req: SingleRepriceAdjustRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    跟价监控中直接调整商品售价：
    1. 校验底价防穿
    2. 推送 Makro 官方网关
    3. 同步 MakroListing, StoreProductReprice, MakroPiggybackItem
    """
    store_id = None
    rep = None
    if req.id:
        rep = db.query(StoreProductReprice).filter(StoreProductReprice.id == req.id).first()
        if not rep:
            raise HTTPException(status_code=404, detail="跟价监控记录不存在")
        store_id = rep.store_id
    elif req.listing_id:
        lst = db.query(MakroListing).filter(MakroListing.id == req.listing_id).first()
        if not lst:
            raise HTTPException(status_code=404, detail="商品 Listing 不存在")
        store_id = lst.store_id
    elif req.seller_sku:
        lst = db.query(MakroListing).filter(MakroListing.sku_id == req.seller_sku).first()
        if not lst:
            raise HTTPException(status_code=404, detail="商品 Listing 不存在")
        store_id = lst.store_id
    else:
        raise HTTPException(status_code=400, detail="必须提供 id, listing_id 或 seller_sku")

    store = _verify_store_access(current_user, store_id, db)
    res = UnifiedPricingService.adjust_single_price(
        store=store,
        db=db,
        new_price=req.new_price,
        listing_id=req.listing_id or (rep.listing_id if rep else None),
        reprice_id=req.id,
        seller_sku=req.seller_sku or (rep.seller_sku if rep else None),
        sync_makro=req.sync_makro,
        force=req.force,
        operator=current_user.username
    )
    if not res.get("success") and not res.get("below_floor"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res

@router.post("/batch-adjust-price", summary="批量调整我店售价并全端三表同步")
def batch_adjust_reprice_prices(
    req: BatchRepriceAdjustRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    跟价监控中批量调整选中商品售价：
    支持统一固定价、按金额增减、按百分比增减
    """
    if not req.ids:
        raise HTTPException(status_code=400, detail="请至少选择一个商品进行调价")

    reps = db.query(StoreProductReprice).options(joinedload(StoreProductReprice.listing)).filter(
        StoreProductReprice.id.in_(req.ids)
    ).all()
    if not reps:
        raise HTTPException(status_code=404, detail="未找到选中的跟价记录")

    first_store_id = req.store_id or reps[0].store_id
    store = _verify_store_access(current_user, first_store_id, db)

    items_payload = []
    for r in reps:
        curr_p = float(r.listing.ssp if r.listing and r.listing.ssp else 0.0)
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
            "reprice_id": r.id,
            "listing_id": r.listing_id,
            "seller_sku": r.seller_sku,
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

@router.put("/config/{item_id}", summary="修改单件商品的跟价参数与竞对链接")

@router.post("/config/{item_id}", summary="修改单件商品的跟价参数与竞对链接")
def update_single_item_config(
    item_id: int,
    req: UpdateItemConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    item = db.query(StoreProductReprice).filter(StoreProductReprice.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="监控商品不存在")

    if req.auto_reprice is not None:
        if req.auto_reprice and item.compliance_status == "PROHIBITED":
            raise HTTPException(status_code=400, detail="该商品已被判定为红线侵权违禁品，系统彻底禁止开启跟价！")
        item.auto_reprice = req.auto_reprice
        if req.auto_reprice and item.monitor_status in ["PAUSED", "PENDING_CONFIG"]:
            item.monitor_status = "ACTIVE"
        elif not req.auto_reprice and item.monitor_status == "ACTIVE":
            item.monitor_status = "PAUSED"

    if req.min_price_floor is not None:
        item.min_price_floor = max(round(float(req.min_price_floor), 2), 0.0)

    if req.max_price_ceiling is not None:
        item.max_price_ceiling = max(round(float(req.max_price_ceiling), 2), 0.0)

    if req.price_strategy is not None:
        item.price_strategy = req.price_strategy

    if req.target_fsn is not None:
        item.target_fsn = req.target_fsn.strip().upper()
        if not item.target_makro_url:
            from ..services.makro_scraper_service import MakroScraperService
            item.target_makro_url = MakroScraperService.format_canonical_makro_url(item.target_fsn)

    if req.target_makro_url is not None:
        item.target_makro_url = req.target_makro_url.strip()

    db.commit()
    db.refresh(item)

    return {
        "success": True,
        "item_id": item.id,
        "auto_reprice": item.auto_reprice,
        "min_price_floor": item.min_price_floor,
        "max_price_ceiling": item.max_price_ceiling,
        "price_strategy": item.price_strategy,
        "monitor_status": item.monitor_status,
        "target_fsn": item.target_fsn,
        "target_makro_url": item.target_makro_url
    }

# =========================================================================
# 4. 在售侵权长效复检与安全熔断机制
# =========================================================================
@router.post("/compliance-check/{item_id}", summary="对在售监控商品发起单品侵权合规复检")
def check_single_reprice_compliance(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    item = db.query(StoreProductReprice).filter(StoreProductReprice.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="监控商品不存在")

    lst = item.listing
    compliance_service = ComplianceService.from_db(db)

    prod_dict = {
        "id": item.id,
        "title": lst.title if lst else item.seller_sku,
        "makro_title": lst.title if lst else item.seller_sku,
        "brand": lst.brand if lst and lst.brand else "Generic",
        "makro_brand": lst.brand if lst and lst.brand else "Generic",
        "category": lst.vertical if lst and lst.vertical else "general",
        "images": [lst.image_url] if lst and lst.image_url else [],
        "raw_images": [lst.image_url] if lst and lst.image_url else [],
        "is_piggyback": item.source_type == "PIGGYBACK"
    }

    try:
        res = compliance_service.check_product(prod_dict, check_image=bool(prod_dict.get("images")))
        status = res.get("status", "SAFE")

        item.compliance_status = status
        item.compliance_details = json.dumps(res, ensure_ascii=False)
        item.last_compliance_check_at = datetime.now()

        # 触发安全熔断
        if status == "PROHIBITED":
            item.monitor_status = "BLOCKED"
            item.auto_reprice = False
            item.last_reprice_result = "BLOCKED: 侵权复检命中禁售，强制熔断暂停调价"

        # ★★★ 双向同步给 MakroPiggybackItem ★★★
        try:
            pb = None
            if item.source_piggyback_id:
                pb = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item.source_piggyback_id).first()
            if not pb:
                pb = db.query(MakroPiggybackItem).filter(
                    MakroPiggybackItem.store_id == item.store_id,
                    or_(
                        MakroPiggybackItem.seller_sku == item.seller_sku,
                        MakroPiggybackItem.makro_product_id == item.makro_product_id
                    )
                ).first()
            if pb:
                pb.compliance_status = status
                pb.compliance_details = item.compliance_details
                if status == "PROHIBITED":
                    pb.is_abandoned = True
                    pb.abandoned_reason = "侵权复检命中禁售红线 (由跟价监控同步)"
                    pb.abandoned_at = datetime.now()
        except Exception as sync_err:
            logger.warning(f"跟价复检同步跟品表异常: {sync_err}")

        db.commit()
        return {
            "success": True,
            "status": status,
            "is_blocked": status == "PROHIBITED",
            "details": res
        }
    except Exception as e:
        logger.error(f"合规复检异常 [{item.seller_sku}]: {e}")
        raise HTTPException(status_code=500, detail=f"合规复检异常: {str(e)}")

@router.post("/arbitrate/{item_id}", summary="跟价监控商品人工终审裁定 (双向同步跟品表)")
def arbitrate_reprice_item(
    item_id: int,
    req: RepriceArbitrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    item = db.query(StoreProductReprice).filter(StoreProductReprice.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="未找到该跟价监控商品")
    _verify_store_access(current_user, item.store_id, db)

    verdict = req.human_verdict.upper()
    if verdict not in ["SAFE", "RISK", "PROHIBITED"]:
        raise HTTPException(status_code=400, detail="裁决状态必须为 SAFE, RISK 或 PROHIBITED")

    item.compliance_status = verdict

    details = {}
    if item.compliance_details:
        if isinstance(item.compliance_details, str):
            try:
                details = json.loads(item.compliance_details)
            except Exception:
                details = {}
        elif isinstance(item.compliance_details, dict):
            details = dict(item.compliance_details)
    elif item.source_piggyback and item.source_piggyback.compliance_details:
        try:
            details = json.loads(item.source_piggyback.compliance_details)
        except Exception:
            details = {}

    details["human_arbitration"] = {
        "human_verdict": verdict,
        "operator": current_user.nickname or current_user.username,
        "notes": req.human_notes or "",
        "arbitrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_module": "REPRICE"
    }
    item.compliance_details = json.dumps(details, ensure_ascii=False)
    item.last_compliance_check_at = datetime.now()

    if verdict == "PROHIBITED":
        item.monitor_status = "BLOCKED"
        item.auto_reprice = False
        item.last_reprice_result = "BLOCKED: 人工终审判定侵权红线，安全熔断暂停调价"
    elif verdict == "SAFE" and item.monitor_status == "BLOCKED":
        item.monitor_status = "ACTIVE"
        item.auto_reprice = True
        item.last_reprice_result = "ACTIVE: 人工终审恢复SAFE合规，恢复自动巡航跟价"

    # ★★★ 双向同步给 MakroPiggybackItem ★★★
    try:
        pb = None
        if item.source_piggyback_id:
            pb = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item.source_piggyback_id).first()
        if not pb:
            pb = db.query(MakroPiggybackItem).filter(
                MakroPiggybackItem.store_id == item.store_id,
                or_(
                    MakroPiggybackItem.seller_sku == item.seller_sku,
                    MakroPiggybackItem.makro_product_id == item.makro_product_id
                )
            ).first()

        if pb:
            pb.compliance_status = verdict
            pb.compliance_details = item.compliance_details
            if verdict == "PROHIBITED":
                pb.is_abandoned = True
                pb.abandoned_reason = f"人工仲裁侵权禁售 (由跟价监控同步): {req.human_notes or ''}"
                pb.abandoned_at = datetime.now()
            elif verdict == "SAFE" and pb.is_abandoned:
                pb.is_abandoned = False
                pb.abandoned_reason = None
                pb.abandoned_at = None
            logger.info(f"已双向同步仲裁结论至跟品挂靠表 [{pb.seller_sku}]: {verdict}")
    except Exception as pb_err:
        logger.warning(f"仲裁同步跟品表异常: {pb_err}")

    db.commit()
    return {
        "success": True,
        "compliance_status": item.compliance_status,
        "compliance_details": details,
        "monitor_status": item.monitor_status,
        "auto_reprice": item.auto_reprice,
        "message": f"已成功终审裁定为 [{verdict}] 并双向同步"
    }

@router.post("/batch-compliance-check", summary="批量执行在售商品侵权长效复检任务")
def batch_check_reprice_compliance(
    req: BatchComplianceCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(StoreProductReprice)
    if req.ids:
        query = query.filter(StoreProductReprice.id.in_(req.ids))
    elif req.store_id:
        query = query.filter(StoreProductReprice.store_id == req.store_id)
    else:
        raise HTTPException(status_code=400, detail="必须指定商品 ID 列表或店铺 ID")

    items = query.all()
    if not items:
        return {"success": True, "total": 0, "message": "未找到需要复检的商品"}

    item_ids = [it.id for it in items]
    total = len(item_ids)

    task = task_manager.create_task(
        task_type="REPRICE_COMPLIANCE_CHECK",
        name=f"在售侵权合规长效复检 ({total} 件)",
        total=total,
        product_ids=item_ids
    )
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        w_db = SessionLocal()
        compliance_srv = ComplianceService.from_db(w_db)
        w_db.close()

        done = 0
        errs = 0
        blocked = 0
        lock = threading.Lock()

        def _check_one(iid: int):
            nonlocal done, errs, blocked
            if tm.is_cancelled(tid):
                return
            t_db = SessionLocal()
            try:
                rep = t_db.query(StoreProductReprice).filter(StoreProductReprice.id == iid).first()
                if not rep:
                    return
                lst = rep.listing
                prod_dict = {
                    "id": rep.id,
                    "title": lst.title if lst else rep.seller_sku,
                    "makro_title": lst.title if lst else rep.seller_sku,
                    "brand": lst.brand if lst and lst.brand else "Generic",
                    "makro_brand": lst.brand if lst and lst.brand else "Generic",
                    "category": lst.vertical if lst and lst.vertical else "general",
                    "images": [lst.image_url] if lst and lst.image_url else [],
                    "raw_images": [lst.image_url] if lst and lst.image_url else [],
                    "is_piggyback": rep.source_type == "PIGGYBACK"
                }
                c_res = compliance_srv.check_product(prod_dict, check_image=bool(prod_dict.get("images")))
                c_status = c_res.get("status", "SAFE")

                rep.compliance_status = c_status
                rep.compliance_details = json.dumps(c_res, ensure_ascii=False)
                rep.last_compliance_check_at = datetime.now()

                is_b = False
                if c_status == "PROHIBITED":
                    rep.monitor_status = "BLOCKED"
                    rep.auto_reprice = False
                    rep.last_reprice_result = "BLOCKED: 侵权复检命中禁售，强制熔断暂停调价"
                    is_b = True

                # 双向同步跟品表
                try:
                    pb = None
                    if rep.source_piggyback_id:
                        pb = t_db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == rep.source_piggyback_id).first()
                    if not pb:
                        pb = t_db.query(MakroPiggybackItem).filter(
                            MakroPiggybackItem.store_id == rep.store_id,
                            or_(
                                MakroPiggybackItem.seller_sku == rep.seller_sku,
                                MakroPiggybackItem.makro_product_id == rep.makro_product_id
                            )
                        ).first()
                    if pb:
                        pb.compliance_status = c_status
                        pb.compliance_details = rep.compliance_details
                        if c_status == "PROHIBITED":
                            pb.is_abandoned = True
                            pb.abandoned_reason = "侵权批量复检命中禁售红线 (由跟价监控同步)"
                            pb.abandoned_at = datetime.now()
                except Exception:
                    pass

                t_db.commit()

                with lock:
                    done += 1
                    if is_b:
                        blocked += 1
                    tm.update_progress(
                        tid,
                        current=done + errs,
                        current_title=f"{rep.seller_sku}: {c_status}",
                        success_inc=1
                    )
            except Exception as ex:
                with lock:
                    errs += 1
                    tm.update_progress(tid, current=done + errs, fail_inc=1, error=str(ex))
            finally:
                t_db.close()

        with ThreadPoolExecutor(max_workers=min(20, total)) as executor:
            futures = [executor.submit(_check_one, iid) for iid in item_ids]
            for fut in as_completed(futures):
                if tm.is_cancelled(tid):
                    break
                try:
                    fut.result()
                except Exception:
                    pass

        tm.finish_task(
            tid,
            status="SUCCESS",
            message=f"合规复检完成：共 {done}/{total} 件 (熔断拦截: {blocked} 件，失败: {errs} 件)",
            success_count=done,
            fail_count=errs,
            blocked_count=blocked
        )

    task_manager.start_task(task_id, _worker)

    return {
        "success": True,
        "message": f"已在后台启动在售侵权合规长效复检任务 (共 {total} 件)",
        "task_id": task_id,
        "total": total
    }

@router.post("/quick-delist/{item_id}", summary="对熔断/侵权高危商品一键调用官方 API 执行下架并彻底移出监控")
def quick_delist_infringing_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    item = db.query(StoreProductReprice).filter(StoreProductReprice.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="监控商品不存在")

    store = _verify_store_access(current_user, item.store_id, db)
    try:
        sku = item.seller_sku
        res = MakroPortalService.deactivate_store_listings(
            store=store,
            sku_ids=[sku],
            reason_category="INFRINGING_RISK",
            note="侵权复检命中禁售红线，运营一键紧急下架避险",
            db=db
        )
        is_ok = res.get("success_count", 0) > 0
        if is_ok:
            # 物理硬删除：彻底移出跟价监控底表
            db.delete(item)
            db.commit()
        return {
            "success": is_ok,
            "message": f"商品 [{sku}] 已成功从 Makro 官方下架并已从跟价监控中彻底移除",
            "result": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"官方下架异常: {str(e)}")

@router.post("/batch-delist", summary="批量调用官方 API 下架商品并彻底移出跟价监控")
def batch_delist_reprice_items(
    req: BatchDelistRepriceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    批量下架跟价监控商品：
    1. 解析待下架商品 ID 列表（支持显式 ids 与 select_all + filters 跨页全选）
    2. 校验店铺操作权限
    3. 汇总商品 SKU，调用 MakroPortalService.deactivate_store_listings 执行：
       - Makro 官方 API 将 Listing 设为 INACTIVE 并清零库存
       - 本地 MakroListing 更新 internal_state 为 INACTIVE，inventory 为 0
       - 彻底删除 StoreProductReprice 移出跟价底册
    4. 联动更新 MakroPiggybackItem 对应跟品状态为 INACTIVE
    """
    target_ids = []
    if req.select_all:
        target_store_id = req.store_id or (req.filters and req.filters.get("store_id"))
        if not target_store_id:
            raise HTTPException(status_code=400, detail="跨页全选下架必须指定目标店铺 ID")
        _verify_store_access(current_user, target_store_id, db)

        q = db.query(StoreProductReprice.id).join(MakroListing, StoreProductReprice.listing_id == MakroListing.id).filter(
            StoreProductReprice.store_id == target_store_id,
            MakroListing.internal_state == "ACTIVE"
        )
        filters = req.filters or {}
        src_type = filters.get("source_type")
        if src_type and src_type != "ALL":
            q = q.filter(StoreProductReprice.source_type == src_type)

        bb_st = filters.get("buybox_status")
        if bb_st and bb_st != "ALL":
            if bb_st == "MISSING_FLOOR":
                q = q.filter(or_(StoreProductReprice.min_price_floor <= 0, StoreProductReprice.min_price_floor.is_(None)))
            else:
                q = q.filter(StoreProductReprice.buybox_status == bb_st)

        comp_st = filters.get("compliance_status")
        if comp_st and comp_st != "ALL":
            if comp_st == "PENDING_CHECK":
                q = q.filter(
                    or_(
                        StoreProductReprice.compliance_status == "PENDING_CHECK",
                        StoreProductReprice.compliance_status.is_(None),
                        StoreProductReprice.compliance_details.is_(None),
                        StoreProductReprice.compliance_details == "",
                        StoreProductReprice.compliance_details == "null",
                        StoreProductReprice.compliance_details == "{}"
                    )
                )
            elif comp_st == "SAFE":
                q = q.filter(
                    StoreProductReprice.compliance_status == "SAFE",
                    StoreProductReprice.compliance_details.isnot(None),
                    StoreProductReprice.compliance_details != "",
                    StoreProductReprice.compliance_details != "null",
                    StoreProductReprice.compliance_details != "{}"
                )
            else:
                q = q.filter(StoreProductReprice.compliance_status == comp_st)

        auto_rep = filters.get("auto_reprice")
        if auto_rep and auto_rep != "ALL":
            bool_val = str(auto_rep).lower() in ["true", "1", "yes"]
            q = q.filter(StoreProductReprice.auto_reprice == bool_val)

        mon_st = filters.get("monitor_status")
        if mon_st and mon_st != "ALL":
            q = q.filter(StoreProductReprice.monitor_status == mon_st)

        search_kw = filters.get("search")
        if search_kw and str(search_kw).strip():
            s = f"%{str(search_kw).strip()}%"
            q = q.filter(
                or_(
                    StoreProductReprice.seller_sku.ilike(s),
                    StoreProductReprice.makro_product_id.ilike(s),
                    StoreProductReprice.target_fsn.ilike(s),
                    StoreProductReprice.current_competitor_seller.ilike(s),
                    MakroListing.title.ilike(s),
                    MakroListing.brand.ilike(s)
                )
            )

        min_p = filters.get("min_my_price")
        if min_p is not None and min_p != "":
            try:
                q = q.filter(MakroListing.ssp >= float(min_p))
            except (ValueError, TypeError):
                pass

        max_p = filters.get("max_my_price")
        if max_p is not None and max_p != "":
            try:
                q = q.filter(MakroListing.ssp <= float(max_p))
            except (ValueError, TypeError):
                pass

        target_ids = [row[0] for row in q.all()]
    else:
        target_ids = req.ids or []

    if not target_ids:
        raise HTTPException(status_code=400, detail="未选择任何需要下架的监控商品")

    items = db.query(StoreProductReprice).filter(StoreProductReprice.id.in_(target_ids)).all()
    if not items:
        raise HTTPException(status_code=404, detail="未找到指定的跟价监控商品")

    from collections import defaultdict
    store_map = defaultdict(list)
    for it in items:
        store_map[it.store_id].append(it)

    total_success = 0
    total_failed = 0
    all_errors = []

    reason_cat = req.reason_category or "MANUAL_OFFLINE"
    note_text = req.note or "跟价监控批量下架操作"

    for s_id, s_items in store_map.items():
        try:
            store = _verify_store_access(current_user, s_id, db)
            sku_list = [it.seller_sku for it in s_items]

            res = MakroPortalService.deactivate_store_listings(
                store=store,
                sku_ids=sku_list,
                reason_category=reason_cat,
                note=note_text,
                db=db
            )

            s_success = res.get("success_count", 0)
            s_failed = res.get("failed_count", 0)
            total_success += s_success
            total_failed += s_failed
            if res.get("errors"):
                all_errors.extend(res.get("errors"))

            if s_success > 0:
                err_skus = [err.split(":")[0].strip() for err in res.get("errors", []) if ":" in err]
                success_skus = [s for s in sku_list if s not in err_skus]
                if success_skus:
                    db.query(MakroPiggybackItem).filter(
                        MakroPiggybackItem.store_id == s_id,
                        MakroPiggybackItem.seller_sku.in_(success_skus)
                    ).update({
                        MakroPiggybackItem.status: "INACTIVE"
                    }, synchronize_session=False)
                    db.commit()

        except Exception as e:
            logger.error(f"店铺 ID {s_id} 批量下架异常: {e}")
            total_failed += len(s_items)
            all_errors.append(f"店铺 ID {s_id}: {str(e)}")

    return {
        "success": total_success > 0,
        "total": len(items),
        "success_count": total_success,
        "failed_count": total_failed,
        "errors": all_errors,
        "message": f"成功下架 {total_success} 件商品并移出跟价监控" + (f"，{total_failed} 件下架失败" if total_failed > 0 else "")
    }

# =========================================================================
# 5. 普通店铺商品补齐竞对并开启监控 (Bind Competitor)
# =========================================================================
@router.post("/bind-competitor", summary="为店铺在售商品补录竞对 FSN 并开启监控")
def bind_competitor_to_listing(
    req: BindCompetitorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    store = _verify_store_access(current_user, req.store_id, db)
    listing = db.query(MakroListing).filter(
        MakroListing.store_id == store.id,
        MakroListing.id == req.listing_id
    ).first()
    if not listing:
        raise HTTPException(status_code=404, detail="未找到该店铺商品")

    rep = db.query(StoreProductReprice).filter(
        StoreProductReprice.listing_id == listing.id
    ).first()

    clean_fsn = req.target_fsn.strip().upper()
    from ..services.makro_scraper_service import MakroScraperService
    clean_url = req.target_makro_url or MakroScraperService.format_canonical_makro_url(clean_fsn)

    if not rep:
        rep = StoreProductReprice(
            store_id=store.id,
            listing_id=listing.id,
            seller_sku=listing.sku_id,
            makro_product_id=listing.product_id or clean_fsn,
            source_type="SELF_PUBLISHED" if listing.local_product_id else "STORE_SYNCED"
        )
        db.add(rep)

    rep.target_fsn = clean_fsn
    rep.target_makro_url = clean_url
    rep.min_price_floor = max(round(req.min_price_floor, 2), 0.0)

    s_strat = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_default_strategy").first()
    def_strategy = s_strat.value.strip() if s_strat and s_strat.value else "MINUS_1"
    rep.price_strategy = req.price_strategy or def_strategy

    rep.auto_reprice = req.auto_reprice
    rep.monitor_status = "ACTIVE"

    db.commit()
    db.refresh(rep)

    return {
        "success": True,
        "message": f"商品 [{listing.sku_id}] 成功绑定竞对 FSN [{clean_fsn}] 并开启监控",
        "reprice_id": rep.id,
        "target_fsn": rep.target_fsn,
        "auto_reprice": rep.auto_reprice
    }

# =========================================================================
# 6. 审计日志与调度器状态
# =========================================================================
@router.get("/logs", summary="获取自动跟价历史审计日志")
def get_reprice_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    store_id: Optional[int] = Query(None),
    seller_sku: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(MakroRepriceLog)
    if store_id:
        query = query.filter(MakroRepriceLog.store_id == store_id)
    if seller_sku:
        query = query.filter(MakroRepriceLog.seller_sku.ilike(f"%{seller_sku.strip()}%"))
    if action and action != "ALL":
        query = query.filter(MakroRepriceLog.action == action.strip().upper())

    total = query.count()
    logs = query.order_by(desc(MakroRepriceLog.created_at)).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for l in logs:
        items.append({
            "id": l.id,
            "reprice_id": l.reprice_id,
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

@router.get("/status", summary="获取后台自动跟价调度引擎的运行状态")
def get_reprice_scheduler_status(
    current_user: User = Depends(get_current_user)
):
    return {
        "success": True,
        "running": auto_reprice_scheduler.running,
        "scheduler_enabled": auto_reprice_scheduler.running,
        "interval_seconds": auto_reprice_scheduler.interval_seconds,
        "interval_minutes": auto_reprice_scheduler.interval_seconds // 60,
        "last_run_at": auto_reprice_scheduler.last_run_at.strftime("%Y-%m-%d %H:%M:%S") if auto_reprice_scheduler.last_run_at else None,
        "last_summary": auto_reprice_scheduler.last_summary
    }

@router.post("/scheduler/toggle", summary="手动启停后台跟价调度引擎")
def toggle_reprice_scheduler(
    enabled: Optional[bool] = Query(None),
    enable: Optional[bool] = Query(None),
    current_user: User = Depends(get_current_user)
):
    target = enabled if enabled is not None else (enable if enable is not None else not auto_reprice_scheduler.running)
    if target:
        auto_reprice_scheduler.start()
    else:
        auto_reprice_scheduler.stop()
    return {
        "success": True,
        "running": auto_reprice_scheduler.running,
        "enabled": auto_reprice_scheduler.running,
        "message": "24h 自动巡航已开启" if auto_reprice_scheduler.running else "24h 自动巡航已暂停"
    }
