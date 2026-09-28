import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from ..database import get_db
from ..models.user import User, UserStore
from ..models.store import Store
from ..models.makro_listing import MakroListing
from ..services.makro_portal_service import MakroPortalService
from ..utils.auth import get_current_user

router = APIRouter(prefix="/store-products", tags=["店铺在线商品管理"])

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

def _verify_store_access(user: User, store_id: int, db: Session) -> Store:
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if user.role != "ADMIN":
        perm = db.query(UserStore).filter(UserStore.user_id == user.id, UserStore.store_id == store_id).first()
        if not perm:
            raise HTTPException(status_code=403, detail="无权访问该店铺的数据")
    return store

@router.get("/list", summary="查询店铺商品列表 (支持状态 Tab 与搜索)")
def list_store_products(
    store_id: int = Query(..., description="店铺 ID"),
    internal_state: Optional[str] = Query(None, description="状态: ACTIVE, READY_FOR_ACTIVATION, INACTIVE, INACTIVATED_BY_FLIPKART, ALL"),
    status: Optional[str] = Query(None, description="兼容别名: ACTIVE, INACTIVE, ALL 等"),
    search: Optional[str] = Query(None, description="搜索标题、SKU、FSN"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)

    query = db.query(MakroListing).filter(MakroListing.store_id == store.id)

    target_state = status or internal_state or "ALL"
    if target_state and target_state != "ALL":
        query = query.filter(MakroListing.internal_state == target_state)

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

    total = query.count()
    items = query.order_by(MakroListing.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

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
            "local_product_id": it.local_product_id,
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    try:
        res = MakroPortalService.sync_store_listings(store, db)
        return {
            "success": True,
            "message": f"店铺 '{store.name}' 商品同步完成，共同步 {res['total_synced']} 件商品",
            "state_counts": res.get("state_counts", {})
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步商品失败: {str(e)}")

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

        # 同步更新本地数据库
        if local_item:
            local_item.inventory = qty
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
    query = db.query(MakroListing).filter(MakroListing.store_id == store.id)
    if req.select_all:
        if req.status and req.status != "ALL":
            query = query.filter(MakroListing.internal_state == req.status)
        listings = query.all()
    else:
        if not req.sku_ids:
            raise HTTPException(status_code=400, detail="未选择任何商品进行批量修改")
        listings = query.filter(MakroListing.sku_id.in_(req.sku_ids)).all()

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

        # 针对 Makro 回写成功的 SKU，批量更新本地数据库
        success_skus = [sku for sku, res in gateway_res.get("results", {}).items() if res.get("status") == "SUCCESS"]
        if success_skus:
            (
                db.query(MakroListing)
                .filter(MakroListing.store_id == store.id, MakroListing.sku_id.in_(success_skus))
                .update({MakroListing.inventory: req.new_inventory}, synchronize_session=False)
            )
            db.commit()

        return {
            "success": True,
            "total_selected": len(items_to_update),
            "success_count": gateway_res.get("success_count", 0),
            "failed_count": gateway_res.get("failed_count", 0),
            "message": f"批量修改完成：成功 {gateway_res.get('success_count', 0)} 件，失败 {gateway_res.get('failed_count', 0)} 件",
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
