import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime
from ..database import get_db
from ..models.user import User, UserStore
from ..models.store import Store
from ..models.makro_order import MakroOrder
from ..services.makro_portal_service import MakroPortalService
from ..utils.auth import get_current_user

router = APIRouter(prefix="/store-orders", tags=["店铺订单管理与履约"])

def _verify_store_access(user: User, store_id: int, db: Session) -> Store:
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if user.role != "ADMIN":
        perm = db.query(UserStore).filter(UserStore.user_id == user.id, UserStore.store_id == store_id).first()
        if not perm:
            raise HTTPException(status_code=403, detail="无权访问该店铺的订单数据")
    return store

@router.get("/list", summary="查询店铺订单列表 (支持状态过滤、SLA超期预警与关键词搜索)")
def list_store_orders(
    store_id: int = Query(..., description="店铺 ID"),
    status: Optional[str] = Query("all", description="状态: all, pending_rtd, in_transit, completed, sla_breached"),
    search: Optional[str] = Query(None, description="搜索订单号、运单号、买家姓名、城市"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)

    query = db.query(MakroOrder).filter(MakroOrder.store_id == store.id)

    if status == "sla_breached":
        query = query.filter(
            MakroOrder.is_sla_breached == True,
            ~MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"])
        )
    elif status and status != "all":
        if status == "in_transit":
            query = query.filter(MakroOrder.status.in_(["in_transit", "picked_up", "dispatched", "shipped", "shipments_in_transit"]))
        elif status == "pending_rtd":
            query = query.filter(MakroOrder.status.in_(["pending_rtd", "pending_labels", "shipments_to_pack", "shipments_to_handover", "pending_handover", "packed", "rtd", "upcoming", "processing"]))
        elif status == "completed":
            query = query.filter(MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"]))
        else:
            query = query.filter(MakroOrder.status == status)

    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                MakroOrder.order_id.ilike(s),
                MakroOrder.tracking_id.ilike(s),
                MakroOrder.buyer_name.ilike(s),
                MakroOrder.shipping_city.ilike(s)
            )
        )

    total = query.count()
    items = query.order_by(MakroOrder.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # 统计核心指标
    counts = {
        "all": db.query(MakroOrder).filter(MakroOrder.store_id == store.id).count(),
        "pending_rtd": db.query(MakroOrder).filter(MakroOrder.store_id == store.id, MakroOrder.status.in_(["pending_rtd", "pending_labels", "shipments_to_pack", "shipments_to_handover", "pending_handover", "packed", "rtd", "upcoming", "processing"])).count(),
        "in_transit": db.query(MakroOrder).filter(MakroOrder.store_id == store.id, MakroOrder.status.in_(["in_transit", "picked_up", "dispatched", "shipped", "shipments_in_transit"])).count(),
        "completed": db.query(MakroOrder).filter(MakroOrder.store_id == store.id, MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"])).count(),
        "sla_breached": db.query(MakroOrder).filter(
            MakroOrder.store_id == store.id,
            MakroOrder.is_sla_breached == True,
            ~MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"])
        ).count()
    }

    results = []
    for o in items:
        order_items = []
        if o.raw_items_json:
            try:
                raw_oi = json.loads(o.raw_items_json)
                for item_raw in raw_oi:
                    pd = item_raw.get("product_details") or {}
                    pricing = item_raw.get("pricing") or {}
                    order_items.append({
                        "title": item_raw.get("title") or pd.get("title") or f"商品 (SKU: {item_raw.get('sku', '')})",
                        "sku_id": item_raw.get("sku_id") or item_raw.get("sku") or item_raw.get("fsn") or "",
                        "fsn": item_raw.get("fsn") or "",
                        "quantity": item_raw.get("quantity") or 1,
                        "price": pricing.get("total_price") or pricing.get("list_price") or item_raw.get("price") or 0,
                        "image_url": item_raw.get("image_url") or pd.get("product_image") or pd.get("large_product_image") or "",
                        "vertical": pd.get("vertical") or ""
                    })
            except Exception:
                pass

        results.append({
            "id": o.id,
            "store_id": o.store_id,
            "seller_id": o.seller_id,
            "order_id": o.order_id,
            "shipment_id": o.shipment_id,
            "status": o.status,
            "service_profile": o.service_profile,
            "total_amount": o.total_amount,
            "currency": o.currency,
            "buyer_name": o.buyer_name,
            "buyer_phone": o.buyer_phone,
            "shipping_city": o.shipping_city,
            "shipping_state": o.shipping_state,
            "shipping_pincode": o.shipping_pincode,
            "shipping_address_line1": o.shipping_address_line1,
            "tracking_id": o.tracking_id,
            "courier_name": o.courier_name,
            "delivery_vendor": o.delivery_vendor,
            "order_date": o.order_date.strftime("%Y-%m-%d %H:%M") if o.order_date else "",
            "dispatch_by_date": o.dispatch_by_date.strftime("%Y-%m-%d %H:%M") if o.dispatch_by_date else "",
            "delivered_date": o.delivered_date.strftime("%Y-%m-%d %H:%M") if getattr(o, 'delivered_date', None) else "",
            "is_sla_breached": o.is_sla_breached,
            "order_items": order_items,
            "synced_at": o.synced_at.strftime("%Y-%m-%d %H:%M:%S") if o.synced_at else ""
        })

    return {
        "store": {"id": store.id, "name": store.name, "seller_id": store.seller_id},
        "total": total,
        "page": page,
        "page_size": page_size,
        "counts": counts,
        "indicators": counts,
        "items": results
    }

@router.post("/sync", summary="从 Makro 网关同步店铺订单")
def sync_store_orders(
    store_id: int = Query(..., description="店铺 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    try:
        res = MakroPortalService.sync_store_orders(store, db)
        return {
            "success": True,
            "message": f"店铺 '{store.name}' 订单同步完成，共同步 {res['total_synced']} 条订单",
            "state_counts": res.get("state_counts", {})
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步订单失败: {str(e)}")
