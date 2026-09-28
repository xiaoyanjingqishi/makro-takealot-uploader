import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc

from ..database import get_db
from ..models.user import User, UserStore
from ..models.store import Store, ProductStoreListing
from ..models.product import Product, ProductVariant
from ..models.makro_audit_listing import MakroAuditListing
from ..services.makro_portal_service import MakroPortalService
from ..utils.auth import get_current_user

router = APIRouter(prefix="/store-audits", tags=["店铺商品审核流转管理 (In Progress)"])
logger = logging.getLogger(__name__)

def _verify_store_access(user: User, store_id: int, db: Session) -> Store:
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if user.role != "ADMIN":
        perm = db.query(UserStore).filter(UserStore.user_id == user.id, UserStore.store_id == store_id).first()
        if not perm:
            raise HTTPException(status_code=403, detail="无权访问该店铺的数据")
    return store

@router.get("/list", summary="查询在途审核商品列表 (支持多状态与错误诊断)")
def list_store_audits(
    store_id: int = Query(..., description="店铺 ID"),
    status: Optional[str] = Query(None, description="状态: ALL, FSN_CREATION_COMPLETED, DRAFT, IN_PROGRESS"),
    search: Optional[str] = Query(None, description="搜索 SKU、标题、申请批次号 (Request ID)"),
    vertical: Optional[str] = Query(None, description="按类目过滤"),
    only_errors: Optional[bool] = Query(False, description="仅看有异常驳回的商品"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)

    # 1. 统计各状态数量 (不受当前 status 筛选影响)
    base_q = db.query(MakroAuditListing).filter(MakroAuditListing.store_id == store.id)
    total_all = base_q.count()
    # 质检审核中 (QC In Progress, 官方包含 SUBMITTED, FSN_CREATION_IN_PROGRESS, FSN_CREATION_COMPLETED 等)
    total_in_progress = base_q.filter(
        MakroAuditListing.state.in_(["QC_IN_PROGRESS", "IN_PROGRESS", "FSN_CREATION_COMPLETED"])
    ).count()
    # 质检通过已上线 (QC Passed, 官方为 LISTING_CREATION_COMPLETED, COMPLETED)
    total_passed = base_q.filter(
        MakroAuditListing.state.in_(["QC_PASSED", "LISTING_CREATION_COMPLETED", "COMPLETED"])
    ).count()
    # 驳回/草稿待修改 (Draft + QC Failed)
    total_draft = base_q.filter(MakroAuditListing.state == "DRAFT").count()
    total_failed = base_q.filter(MakroAuditListing.state.in_(["QC_FAILED", "LISTING_CREATION_ERROR"])).count()

    state_counts = {
        "ALL": total_all,
        "QC_IN_PROGRESS": total_in_progress,
        "QC_PASSED": total_passed,
        "DRAFT": total_draft + total_failed,
        "QC_FAILED": total_failed,
        # 兼容旧 key
        "FSN_CREATION_COMPLETED": total_passed,
        "IN_PROGRESS": total_in_progress
    }

    # 2. 状态与条件筛选
    query = base_q
    if status and status != "ALL":
        if status in ["QC_IN_PROGRESS", "IN_PROGRESS"]:
            query = query.filter(MakroAuditListing.state.in_(["QC_IN_PROGRESS", "IN_PROGRESS", "FSN_CREATION_COMPLETED"]))
        elif status in ["QC_PASSED", "FSN_CREATION_COMPLETED"]:
            query = query.filter(MakroAuditListing.state.in_(["QC_PASSED", "LISTING_CREATION_COMPLETED", "COMPLETED"]))
        elif status == "DRAFT":
            query = query.filter(MakroAuditListing.state.in_(["DRAFT", "QC_FAILED", "LISTING_CREATION_ERROR"]))
        elif status == "QC_FAILED":
            query = query.filter(MakroAuditListing.state.in_(["QC_FAILED", "LISTING_CREATION_ERROR"]))
        else:
            query = query.filter(MakroAuditListing.state == status)

    if only_errors:
        query = query.filter(MakroAuditListing.has_errors == True)

    if vertical:
        query = query.filter(MakroAuditListing.vertical == vertical)

    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                MakroAuditListing.sku_id.ilike(s),
                MakroAuditListing.request_id.ilike(s),
                MakroAuditListing.title.ilike(s),
                MakroAuditListing.brand.ilike(s)
            )
        )

    total = query.count()
    items_db = (
        query.order_by(desc(MakroAuditListing.official_last_modified), desc(MakroAuditListing.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for it in items_db:
        # 解析中文错误摘要列表
        err_list = []
        if it.error_summary:
            try:
                err_list = json.loads(it.error_summary)
            except Exception:
                err_list = [it.error_summary]

        # 关联的本地选品信息
        local_prod_info = None
        if it.local_product:
            local_prod_info = {
                "id": it.local_product.id,
                "sku_id": it.local_product.sku_id or it.local_product.makro_sku_id or it.sku_id,
                "makro_sku_id": it.local_product.makro_sku_id or it.sku_id,
                "title": it.local_product.makro_title or it.local_product.takealot_title,
                "original_url": it.local_product.takealot_url,
                "status": it.local_product.status,
                "compliance_status": it.local_product.compliance_status
            }

        # 格式化展示时间
        formatted_time = ""
        if it.official_last_modified:
            try:
                formatted_time = datetime.fromtimestamp(it.official_last_modified / 1000).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        elif it.synced_at:
            formatted_time = it.synced_at.strftime("%Y-%m-%d %H:%M:%S")

        items.append({
            "id": it.id,
            "store_id": it.store_id,
            "sku_id": it.sku_id,
            "request_id": it.request_id,
            "txn_id": it.txn_id,
            "vertical": it.vertical,
            "vertical_display_name": it.vertical_display_name,
            "state": "QC_IN_PROGRESS" if it.state in ["QC_IN_PROGRESS", "IN_PROGRESS", "FSN_CREATION_COMPLETED"] else ("QC_PASSED" if it.state in ["QC_PASSED", "LISTING_CREATION_COMPLETED", "COMPLETED"] else it.state),
            "raw_state": it.raw_state or it.state,
            "title": it.title or "未知商品名称",
            "brand": it.brand or "",
            "image_url": it.image_url,
            "fsp": it.fsp or 0.0,
            "mrp": it.mrp or 0.0,
            "has_errors": it.has_errors,
            "error_summary": err_list,
            "delete_allowed": it.delete_allowed,
            "last_modified_time": formatted_time,
            "local_product": local_prod_info
        })

    # 获取该店铺下所有已有的类目列表 (用于下拉选择)
    distinct_verticals = [v[0] for v in db.query(MakroAuditListing.vertical).filter(MakroAuditListing.store_id == store.id).distinct().all() if v[0]]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "state_counts": state_counts,
        "verticals": distinct_verticals,
        "items": items
    }

@router.post("/sync", summary="一键从 Makro 官方网关拉取最新在途审核状态")
def sync_store_audits(
    store_id: int = Query(..., description="店铺 ID"),
    max_pages: int = Query(25, ge=1, le=50, description="最大拉取批次数 (每页50条)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)

    total_synced = 0
    state_counts = {}
    batch_size = 50

    try:
        for page_no in range(0, max_pages):
            res_data = MakroPortalService.fetch_in_progress_listings(
                store=store,
                page_no=page_no,
                page_size=batch_size
            )
            content = res_data.get("content", [])
            if not content:
                break

            for raw_item in content:
                parsed = MakroPortalService.parse_in_progress_item(raw_item)
                st = parsed["state"]
                state_counts[st] = state_counts.get(st, 0) + 1

                # 匹配本地选品
                local_p = None
                if parsed["sku_id"]:
                    s_id = str(parsed["sku_id"]).strip()
                    conds = [
                        Product.makro_sku_id == s_id,
                        Product.sku_id == s_id,
                        Product.variants.any(ProductVariant.sku_id == s_id),
                        Product.store_listings.any(ProductStoreListing.makro_sku_id == s_id)
                    ]
                    if s_id.isdigit():
                        conds.append(Product.id == int(s_id))
                    local_p = db.query(Product).filter(or_(*conds)).first()

                # 查询是否已存在
                existing = (
                    db.query(MakroAuditListing)
                    .filter(
                        MakroAuditListing.store_id == store.id,
                        MakroAuditListing.request_id == parsed["request_id"],
                        MakroAuditListing.sku_id == parsed["sku_id"]
                    )
                    .first()
                )

                err_summary_json = json.dumps(parsed["error_summary"], ensure_ascii=False)
                err_details_json = json.dumps(parsed["error_details"], ensure_ascii=False)

                if existing:
                    existing.state = parsed["state"]
                    existing.raw_state = parsed.get("raw_state")
                    existing.vertical = parsed["vertical"]
                    existing.title = parsed["title"]
                    existing.brand = parsed["brand"]
                    existing.image_url = parsed["image_url"]
                    existing.fsp = parsed["fsp"]
                    existing.mrp = parsed["mrp"]
                    existing.has_errors = parsed["has_errors"]
                    existing.error_summary = err_summary_json
                    existing.error_details = err_details_json
                    existing.delete_allowed = parsed["delete_allowed"]
                    existing.official_last_modified = parsed["official_last_modified"]
                    existing.synced_at = datetime.now()
                    if local_p:
                        existing.local_product_id = local_p.id
                else:
                    new_item = MakroAuditListing(
                        store_id=store.id,
                        seller_id=store.seller_id,
                        sku_id=parsed["sku_id"],
                        request_id=parsed["request_id"],
                        txn_id=parsed["txn_id"],
                        vertical=parsed["vertical"],
                        state=parsed["state"],
                        raw_state=parsed.get("raw_state"),
                        title=parsed["title"],
                        brand=parsed["brand"],
                        image_url=parsed["image_url"],
                        fsp=parsed["fsp"],
                        mrp=parsed["mrp"],
                        has_errors=parsed["has_errors"],
                        error_summary=err_summary_json,
                        error_details=err_details_json,
                        delete_allowed=parsed["delete_allowed"],
                        official_created_on=parsed["official_created_on"],
                        official_last_modified=parsed["official_last_modified"],
                        local_product_id=local_p.id if local_p else None,
                        synced_at=datetime.now()
                    )
                    db.add(new_item)

                total_synced += 1

            db.commit()

            if not res_data.get("hasMore", False):
                break

        return {
            "success": True,
            "message": f"店铺 '{store.name}' 审核流转同步完成，共拉取与更新 {total_synced} 件记录",
            "total_synced": total_synced,
            "state_counts": state_counts
        }
    except Exception as e:
        db.rollback()
        logger.error(f"同步店铺 '{store.name}' (ID: {store_id}) 审核状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"同步审核状态失败: {str(e)}")

@router.get("/detail", summary="查看单件商品完整审核详情与错误诊断")
def get_audit_detail(
    store_id: int = Query(..., description="店铺 ID"),
    audit_id: int = Query(..., description="审核记录 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    item = (
        db.query(MakroAuditListing)
        .filter(MakroAuditListing.store_id == store.id, MakroAuditListing.id == audit_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="未找到该审核记录")

    err_details = {}
    if item.error_details:
        try:
            err_details = json.loads(item.error_details)
        except Exception:
            err_details = {"raw": item.error_details}

    err_summary = []
    if item.error_summary:
        try:
            err_summary = json.loads(item.error_summary)
        except Exception:
            err_summary = [item.error_summary]

    return {
        "id": item.id,
        "sku_id": item.sku_id,
        "request_id": item.request_id,
        "txn_id": item.txn_id,
        "vertical": item.vertical,
        "state": item.state,
        "title": item.title,
        "brand": item.brand,
        "image_url": item.image_url,
        "fsp": item.fsp,
        "mrp": item.mrp,
        "has_errors": item.has_errors,
        "error_summary": err_summary,
        "error_details": err_details,
        "delete_allowed": item.delete_allowed,
        "local_product_id": item.local_product_id
    }
