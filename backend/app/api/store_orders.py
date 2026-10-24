import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from datetime import datetime, timedelta
from ..database import get_db, SessionLocal
from ..models.user import User, UserStore
from ..models.store import Store
from ..models.makro_order import MakroOrder
from ..models.makro_order_status_log import MakroOrderStatusLog
from ..models.makro_purchase_order import MakroPurchaseOrder
from ..models.makro_listing import MakroListing
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.setting import SystemSetting
from ..services.makro_portal_service import MakroPortalService
from ..services.label_service import OrderLabelService
from ..services.task_manager import task_manager, TaskManager
from ..utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/store-orders", tags=["店铺订单管理与履约"])

# =========================================================================
# 请求参数模型
# =========================================================================
class UpdateFulfillmentStatusRequest(BaseModel):
    order_ids: List[int]
    fulfillment_status: str  # NEW, PURCHASED, SENT_TO_FORWARDER, AIR_FREIGHT, ARRIVED_SA, SA_PICKUP, COMPLETED, CANCELLED_RTO

class UpdateProcurementCostRequest(BaseModel):
    order_id: int
    purchase_cost: Optional[float] = None
    domestic_logistics_fee: Optional[float] = None  # 兼容参数，实际由 PO 平摊
    independent_head_haul: Optional[float] = None
    purchase_order_sn: Optional[str] = None
    purchase_notes: Optional[str] = None
    domestic_tracking_no: Optional[str] = None

class OrderItemWeightInput(BaseModel):
    sku_id: Optional[str] = None
    weight_g: Optional[float] = 0.0

class UpdateWeightAndFreightRequest(BaseModel):
    order_id: int
    items_weight: Optional[Dict[str, float]] = None  # sku_id 或 item_id -> weight_g (克)
    items: Optional[List[OrderItemWeightInput]] = None  # 兼容前端数组形式传参 [{sku_id: "...", weight_g: 221}]
    total_weight_g: Optional[float] = None  # 整单总重量 (克)
    air_freight_rate: Optional[float] = None  # None 或 <=0 使用全局单价 (默认 90.0)
    sync_to_catalog: bool = True  # 同步更新至商品库 (MakroListing & MakroPiggybackItem)
    sync_same_sku_orders: bool = True  # 智能扩散更新当前其他包含此 SKU 的待称重订单
    propagate_to_same_sku: Optional[bool] = None  # 别名兼容前端字段命名

class BatchLabelsRequest(BaseModel):
    order_ids: List[int]

class CreatePoRequest(BaseModel):
    store_id: Optional[int] = None
    po_number: Optional[str] = None
    name: Optional[str] = None
    forwarder_name: Optional[str] = None
    air_waybill_no: Optional[str] = None
    domestic_logistics_fee: Optional[float] = 0.0  # PO 批次国内发货快递总运费 (RMB)
    head_haul_fee: Optional[float] = 0.0           # 货代实收空运头程总费用 (RMB)
    logistics_loss_fee: Optional[float] = 0.0      # 物流损耗费 (RMB)
    box_length_cm: Optional[float] = 0.0          # 外箱长 (cm)
    box_width_cm: Optional[float] = 0.0           # 外箱宽 (cm)
    box_height_cm: Optional[float] = 0.0          # 外箱高 (cm)
    package_weight_kg: Optional[float] = 0.0      # 打包实测整箱毛重 (kg)
    notes: Optional[str] = None
    order_ids: Optional[List[int]] = None

class AssignPoRequest(BaseModel):
    po_id: Optional[int] = None  # None 为取消归档/移出PO
    order_ids: List[int]

class RemoveOrderFromPoRequest(BaseModel):
    po_id: int
    order_id: int

class UpdatePoRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    forwarder_name: Optional[str] = None
    air_waybill_no: Optional[str] = None
    domestic_logistics_fee: Optional[float] = None  # PO 批次国内发货快递总运费 (RMB)
    head_haul_fee: Optional[float] = None           # 货代实收空运头程总费用 (RMB)
    logistics_loss_fee: Optional[float] = None      # 物流损耗费 (RMB)
    box_length_cm: Optional[float] = None          # 外箱长 (cm)
    box_width_cm: Optional[float] = None           # 外箱宽 (cm)
    box_height_cm: Optional[float] = None          # 外箱高 (cm)
    package_weight_kg: Optional[float] = None      # 打包实测整箱毛重 (kg)
    notes: Optional[str] = None

class BatchPoFulfillmentRequest(BaseModel):
    target_status: str  # 批量流转的目标履约状态

def _verify_store_access(user: User, store_id: int, db: Session) -> Store:
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if user.role != "ADMIN":
        perm = db.query(UserStore).filter(UserStore.user_id == user.id, UserStore.store_id == store_id).first()
        if not perm:
            raise HTTPException(status_code=403, detail="无权访问该店铺的订单数据")
    return store

# =========================================================================
# 1. 查询订单列表与成本回款综合看板
# =========================================================================
@router.get("/list", summary="查询店铺订单列表 (支持7阶履约过滤、成本核算与搜索)")
def list_store_orders(
    store_id: int = Query(..., description="店铺 ID"),
    status: Optional[str] = Query("all", description="官方状态或 SLA: all, pending_rtd, in_transit, completed, sla_breached"),
    fulfillment_status: Optional[str] = Query("all", description="7阶履约状态: all, NEW, PURCHASED, SENT_TO_FORWARDER, AIR_FREIGHT, ARRIVED_SA, SA_PICKUP, COMPLETED, CANCELLED_RTO"),
    po_id: Optional[int] = Query(None, description="PO 批次 ID 过滤"),
    search: Optional[str] = Query(None, description="搜索订单号、运单号、买家姓名、采购单号、国内快递单号"),
    order_date_start: Optional[str] = Query(None, description="出单日期起始 (YYYY-MM-DD，北京时间)"),
    order_date_end: Optional[str] = Query(None, description="出单日期截止 (YYYY-MM-DD，北京时间)"),
    label_printed: Optional[str] = Query("all", description="标签打印状态: all, printed, unprinted"),
    commission_rate: float = Query(17.5, description="平台佣金扣点率 (%)，默认 17.5% 对应 0.825"),
    shipping_fee_zar: float = Query(70.0, description="平台单笔基础运费 (ZAR)，默认 R70"),
    exchange_rate: float = Query(0.39, description="结算汇率 ZAR->CNY，默认 0.39"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    base_filtered_query = db.query(MakroOrder).filter(MakroOrder.store_id == store.id)

    # 1. 出单日期范围过滤 (北京时间)
    if order_date_start and order_date_start.strip():
        try:
            s_dt = datetime.strptime(order_date_start.strip(), "%Y-%m-%d")
            base_filtered_query = base_filtered_query.filter(MakroOrder.order_date >= s_dt)
        except Exception as e:
            logger.warning(f"解析 order_date_start 失败: {e}")
    if order_date_end and order_date_end.strip():
        try:
            e_dt = datetime.strptime(order_date_end.strip(), "%Y-%m-%d") + timedelta(days=1)
            base_filtered_query = base_filtered_query.filter(MakroOrder.order_date < e_dt)
        except Exception as e:
            logger.warning(f"解析 order_date_end 失败: {e}")

    # 2. 标签打印状态过滤
    if label_printed == "printed":
        base_filtered_query = base_filtered_query.filter(MakroOrder.label_printed == True)
    elif label_printed == "unprinted":
        base_filtered_query = base_filtered_query.filter(or_(MakroOrder.label_printed == False, MakroOrder.label_printed == None))

    # 3. 关键词搜索
    if search and isinstance(search, str) and search.strip():
        s = f"%{search.strip()}%"
        base_filtered_query = base_filtered_query.filter(
            or_(
                MakroOrder.order_id.ilike(s),
                MakroOrder.shipment_id.ilike(s),
                MakroOrder.tracking_id.ilike(s),
                MakroOrder.buyer_name.ilike(s),
                MakroOrder.shipping_city.ilike(s),
                MakroOrder.purchase_order_sn.ilike(s),
                MakroOrder.domestic_tracking_no.ilike(s)
            )
        )

    # 4. 基于综合过滤集进一步筛选状态与 PO
    query = base_filtered_query

    # 官方状态过滤
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

    # 7阶履约状态过滤
    if fulfillment_status and fulfillment_status != "all":
        query = query.filter(MakroOrder.fulfillment_status == fulfillment_status)

    # PO 批次过滤
    if po_id is not None:
        if po_id == 0:
            query = query.filter(MakroOrder.po_id == None)
        else:
            query = query.filter(MakroOrder.po_id == po_id)

    total = query.count()
    items = query.order_by(MakroOrder.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # 5. 预加载全局空运单价与当前涉及的所有 PO 信息及 PO 订单数映射 (避免 N+1 查询)
    setting_rate_row = db.query(SystemSetting).filter(SystemSetting.key == "default_air_freight_rate").first()
    global_air_rate = float(setting_rate_row.value) if setting_rate_row and setting_rate_row.value else 90.0

    po_ids_set = set(o.po_id for o in items if o.po_id)
    po_map = {}
    po_count_map = {}
    if po_ids_set:
        pos = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id.in_(list(po_ids_set))).all()
        for p in pos:
            po_map[p.id] = p
        
        # 统计各 PO 下订单总数用于平摊国内运费与损耗
        po_counts = db.query(MakroOrder.po_id, func.count(MakroOrder.id)).filter(
            MakroOrder.po_id.in_(list(po_ids_set))
        ).group_by(MakroOrder.po_id).all()
        for p_id, cnt in po_counts:
            po_count_map[p_id] = cnt

    # 预加载商品资料库重量 (MakroListing & MakroPiggybackItem) 辅助未称重商品自动带出
    all_page_skus = set()
    for o in items:
        if o.raw_items_json:
            try:
                for it_raw in json.loads(o.raw_items_json):
                    s = it_raw.get("sku_id") or it_raw.get("sku") or it_raw.get("fsn") or ""
                    if s:
                        all_page_skus.add(s)
            except Exception:
                pass

    catalog_sku_weight_map = {}
    catalog_sku_fsn_map = {}
    if all_page_skus:
        sku_list = list(all_page_skus)
        for s_id, w, fsn_val in db.query(MakroListing.sku_id, MakroListing.weight, MakroListing.product_id).filter(MakroListing.sku_id.in_(sku_list)).all():
            if w and w > 0:
                catalog_sku_weight_map[s_id] = int(round(float(w) * 1000))
            if fsn_val:
                catalog_sku_fsn_map[s_id] = fsn_val
        for s_id, w, fsn_val in db.query(MakroPiggybackItem.seller_sku, MakroPiggybackItem.weight, MakroPiggybackItem.makro_product_id).filter(MakroPiggybackItem.seller_sku.in_(sku_list)).all():
            if w and w > 0 and s_id not in catalog_sku_weight_map:
                catalog_sku_weight_map[s_id] = int(round(float(w) * 1000))
            if fsn_val and s_id not in catalog_sku_fsn_map:
                catalog_sku_fsn_map[s_id] = fsn_val

    # 6. 计算单单成本利润与结果组装
    results = []
    for o in items:
        order_items = []
        calc_items_total_g = 0
        if o.raw_items_json:
            try:
                raw_oi = json.loads(o.raw_items_json)
                for item_raw in raw_oi:
                    pd = item_raw.get("product_details") or {}
                    pricing = item_raw.get("pricing") or {}
                    img_url = item_raw.get("image_url") or pd.get("product_image") or pd.get("large_product_image") or ""
                    sku = item_raw.get("sku_id") or item_raw.get("sku") or item_raw.get("fsn") or ""
                    qty = int(item_raw.get("quantity") or 1)

                    # 提取克重: 优先从 item 快照取，其次从商品资料库预填带出
                    raw_w = item_raw.get("weight_g")
                    if raw_w is not None and int(raw_w) > 0:
                        w_g = int(raw_w)
                    else:
                        w_g = catalog_sku_weight_map.get(sku, 0)

                    calc_items_total_g += w_g * qty

                    # 提取 FSN 并拼接 Makro 官方买家前台商品详情 URL
                    item_fsn = item_raw.get("fsn") or pd.get("fsn") or pd.get("product_id") or catalog_sku_fsn_map.get(sku, "")
                    item_id = item_raw.get("item_id") or pd.get("item_id") or item_fsn
                    if item_fsn:
                        prod_url = f"https://www.makro.co.za/-/p/{item_id}?pid={item_fsn}"
                    elif sku:
                        prod_url = f"https://www.makro.co.za/search?q={sku}"
                    else:
                        prod_url = ""

                    # 确定单件单价 (unit_price) 与 该品行小计金额 (total_price)
                    line_total = float(pricing.get("total_price") or item_raw.get("price") or 0.0)
                    list_price = float(pricing.get("list_price") or 0.0)
                    if list_price > 0:
                        unit_price = list_price
                    elif qty > 0 and line_total > 0:
                        unit_price = round(line_total / qty, 2)
                    else:
                        unit_price = line_total

                    order_items.append({
                        "title": item_raw.get("title") or pd.get("title") or f"商品 (SKU: {sku})",
                        "sku_id": sku,
                        "fsn": item_fsn,
                        "product_url": prod_url,
                        "quantity": qty,
                        "unit_price": unit_price,
                        "price": unit_price,
                        "total_price": line_total if line_total > 0 else round(unit_price * qty, 2),
                        "image_url": img_url,
                        "vertical": pd.get("vertical") or "",
                        "weight_g": w_g
                    })
            except Exception:
                pass

        # 订单实测总克重与状态 (以 is_weight_measured 显式标识是否已填写实测重量)
        is_measured = bool(getattr(o, "is_weight_measured", False))
        final_total_weight_g = o.total_weight_g if is_measured else calc_items_total_g
        effective_air_rate = float(o.air_freight_rate) if (o.air_freight_rate and o.air_freight_rate > 0) else global_air_rate

        # 成本与回款核算 (以数据库真实汇总 total_amount 为基准，若无则取各明细行小计之和，绝不重复相乘数量)
        amt_zar = float(o.total_amount or 0.0)
        sum_line_totals = sum(float(it.get("total_price") or 0.0) for it in order_items)
        if amt_zar <= 0 or sum_line_totals > amt_zar:
            amt_zar = sum_line_totals if sum_line_totals > 0 else amt_zar

        st_ful = o.fulfillment_status or "NEW"
        is_abnormal = st_ful in ["CANCELLED_RTO", "cancelled", "returned"]
        
        # 最终回款公式: ((售价 * (1 - 平台扣点)) - 单笔运费) * 汇率
        if is_abnormal:
            payout_cny = 0.0
        else:
            zar_net = (amt_zar * (1.0 - commission_rate / 100.0)) - shipping_fee_zar
            payout_cny = round(max(0.0, zar_net) * exchange_rate, 2)

        # PO 视角平摊国内运费与损耗 (未入 PO 时国内运费为 0.00，待归档后平摊)
        po_info = None
        allocated_domestic = 0.0
        allocated_loss = 0.0
        if o.po_id and o.po_id in po_map:
            p_obj = po_map[o.po_id]
            order_c = max(1, po_count_map.get(o.po_id, 1))
            allocated_domestic = round(float(p_obj.domestic_logistics_fee or 0.0) / order_c, 2)
            allocated_loss = round(float(p_obj.logistics_loss_fee or 0.0) / order_c, 2)
            po_info = {
                "id": p_obj.id,
                "po_number": p_obj.po_number,
                "name": p_obj.name,
                "status": p_obj.status,
                "forwarder_name": p_obj.forwarder_name,
                "air_waybill_no": p_obj.air_waybill_no,
                "domestic_logistics_fee": p_obj.domestic_logistics_fee,
                "head_haul_fee": p_obj.head_haul_fee,
                "logistics_loss_fee": p_obj.logistics_loss_fee,
                "total_orders_in_po": order_c
            }

        # 头程运费: 按单单商品重量进行计算 (克重转千克 * 空运单价，0克为0.0)
        if final_total_weight_g > 0:
            head_haul_cost = round((final_total_weight_g / 1000.0) * effective_air_rate, 2)
        elif is_measured and final_total_weight_g == 0:
            head_haul_cost = 0.0
        else:
            head_haul_cost = round(float(o.independent_head_haul or 0.0), 2)

        p_cost = round(float(o.purchase_cost or 0.0), 2)
        total_cost_cny = round(p_cost + allocated_domestic + head_haul_cost + allocated_loss, 2)
        net_profit_cny = round(payout_cny - total_cost_cny, 2)

        results.append({
            "id": o.id,
            "store_id": o.store_id,
            "seller_id": o.seller_id,
            "order_id": o.order_id,
            "shipment_id": o.shipment_id,
            "status": o.status,
            "fulfillment_status": st_ful,
            "fulfillment_updated_at": o.fulfillment_updated_at.strftime("%Y-%m-%d %H:%M") if o.fulfillment_updated_at else "",
            "service_profile": o.service_profile,
            "total_amount": amt_zar,
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
            "label_printed": bool(getattr(o, "label_printed", False)),
            "label_printed_at": o.label_printed_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(o, "label_printed_at", None) else "",
            "order_items": order_items,
            # 成本与利润核算字段 (订单视角)
            "purchase_cost": p_cost,
            "domestic_logistics_fee": allocated_domestic,
            "allocated_domestic_fee": allocated_domestic,
            "independent_head_haul": head_haul_cost,
            "head_haul_cost": head_haul_cost,
            "total_weight_g": final_total_weight_g,
            "is_weight_measured": is_measured,
            "weight_status": "MEASURED" if is_measured else "PENDING",
            "air_freight_rate": effective_air_rate,
            "allocated_loss": allocated_loss,
            "calculated_payout": payout_cny,
            "total_cost": total_cost_cny,
            "net_profit": net_profit_cny,
            "purchase_order_sn": o.purchase_order_sn or "",
            "purchase_notes": o.purchase_notes or "",
            "domestic_tracking_no": o.domestic_tracking_no or "",
            "po_id": o.po_id,
            "po_info": po_info,
            "synced_at": o.synced_at.strftime("%Y-%m-%d %H:%M:%S") if o.synced_at else ""
        })

    # 7. 统计 7 阶履约状态与核心指标 (严格关联当前出单日期及筛选条件)
    base_store_query = base_filtered_query
    fulfillment_counts = {
        "all": base_store_query.count(),
        "NEW": base_store_query.filter(MakroOrder.fulfillment_status == "NEW").count(),
        "PURCHASED": base_store_query.filter(MakroOrder.fulfillment_status == "PURCHASED").count(),
        "SENT_TO_FORWARDER": base_store_query.filter(MakroOrder.fulfillment_status == "SENT_TO_FORWARDER").count(),
        "AIR_FREIGHT": base_store_query.filter(MakroOrder.fulfillment_status == "AIR_FREIGHT").count(),
        "ARRIVED_SA": base_store_query.filter(MakroOrder.fulfillment_status == "ARRIVED_SA").count(),
        "SA_PICKUP": base_store_query.filter(MakroOrder.fulfillment_status == "SA_PICKUP").count(),
        "COMPLETED": base_store_query.filter(MakroOrder.fulfillment_status == "COMPLETED").count(),
        "CANCELLED_RTO": base_store_query.filter(MakroOrder.fulfillment_status == "CANCELLED_RTO").count()
    }

    counts = {
        "all": fulfillment_counts["all"],
        "pending_rtd": base_store_query.filter(MakroOrder.status.in_(["pending_rtd", "pending_labels", "shipments_to_pack", "shipments_to_handover", "pending_handover", "packed", "rtd", "upcoming", "processing"])).count(),
        "in_transit": base_store_query.filter(MakroOrder.status.in_(["in_transit", "picked_up", "dispatched", "shipped", "shipments_in_transit"])).count(),
        "completed": base_store_query.filter(MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"])).count(),
        "sla_breached": base_store_query.filter(
            MakroOrder.is_sla_breached == True,
            ~MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"])
        ).count()
    }

    # 8. 财务看板汇总 (当前筛选集)
    all_filtered = query.all()
    total_gmv_zar = sum(float(x.total_amount or 0.0) for x in all_filtered)
    total_payout_cny = 0.0
    total_purchase_cny = 0.0
    total_domestic_cny = 0.0
    total_head_haul_cny = 0.0
    total_po_loss_cny = 0.0

    # 统计筛选集内关联 PO 的总数用于平摊
    all_filtered_po_ids = list(set(x.po_id for x in all_filtered if x.po_id))
    all_filtered_po_map = {}
    all_filtered_po_counts = {}
    if all_filtered_po_ids:
        for p in db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id.in_(all_filtered_po_ids)).all():
            all_filtered_po_map[p.id] = p
        for p_id, cnt in db.query(MakroOrder.po_id, func.count(MakroOrder.id)).filter(
            MakroOrder.po_id.in_(all_filtered_po_ids)
        ).group_by(MakroOrder.po_id).all():
            all_filtered_po_counts[p_id] = cnt

    for x in all_filtered:
        st_ful = x.fulfillment_status or "NEW"
        if st_ful not in ["CANCELLED_RTO", "cancelled", "returned"]:
            zar_n = (float(x.total_amount or 0.0) * (1.0 - commission_rate / 100.0)) - shipping_fee_zar
            total_payout_cny += max(0.0, zar_n) * exchange_rate
        total_purchase_cny += float(x.purchase_cost or 0.0)

        # 国内运费与损耗平摊自 PO
        if x.po_id and x.po_id in all_filtered_po_map:
            p_obj = all_filtered_po_map[x.po_id]
            order_c = max(1, all_filtered_po_counts.get(x.po_id, 1))
            total_domestic_cny += float(p_obj.domestic_logistics_fee or 0.0) / order_c
            total_po_loss_cny += float(p_obj.logistics_loss_fee or 0.0) / order_c

        # 头程运费按重计算
        x_rate = float(x.air_freight_rate) if (x.air_freight_rate and x.air_freight_rate > 0) else global_air_rate
        if x.total_weight_g and x.total_weight_g > 0:
            total_head_haul_cny += (float(x.total_weight_g) / 1000.0) * x_rate
        else:
            total_head_haul_cny += float(x.independent_head_haul or 0.0)

    total_cost_cny = total_purchase_cny + total_domestic_cny + total_head_haul_cny + total_po_loss_cny
    total_profit_cny = total_payout_cny - total_cost_cny
    margin = (total_profit_cny / total_payout_cny * 100.0) if total_payout_cny > 0 else 0.0

    financial_summary = {
        "total_gmv_zar": round(total_gmv_zar, 2),
        "total_payout_cny": round(total_payout_cny, 2),
        "total_purchase_cny": round(total_purchase_cny, 2),
        "total_domestic_cny": round(total_domestic_cny, 2),
        "total_po_head_haul_cny": round(total_head_haul_cny, 2),
        "total_po_loss_cny": round(total_po_loss_cny, 2),
        "total_cost_cny": round(total_cost_cny, 2),
        "total_profit_cny": round(total_profit_cny, 2),
        "margin_percent": round(margin, 2)
    }

    return {
        "store": {"id": store.id, "name": store.name, "seller_id": store.seller_id},
        "total": total,
        "page": page,
        "page_size": page_size,
        "counts": counts,
        "fulfillment_counts": fulfillment_counts,
        "financial_summary": financial_summary,
        "items": results
    }

# =========================================================================
# 2. 更新订单 7 阶履约状态
# =========================================================================
@router.put("/update-fulfillment", summary="更新订单 7 阶履约状态 (支持批量与单笔)")
def update_order_fulfillment_status(
    req: UpdateFulfillmentStatusRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.order_ids:
        raise HTTPException(status_code=400, detail="请至少指定一个订单 ID")

    valid_statuses = [
        "NEW", "PURCHASED", "SENT_TO_FORWARDER",
        "AIR_FREIGHT", "ARRIVED_SA", "SA_PICKUP", "COMPLETED", "CANCELLED_RTO"
    ]
    if req.fulfillment_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"无效的履约状态: {req.fulfillment_status}")

    orders = db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).all()
    now = datetime.now()
    operator_name = current_user.username if current_user else "MANUAL"

    for o in orders:
        old_st = o.fulfillment_status or "NEW"
        o.fulfillment_status = req.fulfillment_status
        o.fulfillment_updated_at = now
        db.add(MakroOrderStatusLog(
            order_id=o.id,
            order_sn=o.order_id,
            change_type="FULFILLMENT_STATUS",
            old_value=old_st,
            new_value=req.fulfillment_status,
            operator=operator_name,
            notes="人工手动流转状态"
        ))

    db.commit()
    return {
        "success": True,
        "updated_count": len(orders),
        "fulfillment_status": req.fulfillment_status,
        "message": f"成功流转 {len(orders)} 笔订单状态至: {req.fulfillment_status}"
    }

# =========================================================================
# 3. 录入/更新订单采购成本与快递单号追溯 (已解除订单国内运费，转由PO平摊)
# =========================================================================
@router.put("/update-procurement-cost", summary="录入订单采购价、单号与物流追踪单号")
def update_order_procurement_cost(
    req: UpdateProcurementCostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.query(MakroOrder).filter(MakroOrder.id == req.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    _verify_store_access(current_user, order.store_id, db)

    old_cost = order.purchase_cost
    if req.purchase_cost is not None:
        order.purchase_cost = max(0.0, round(float(req.purchase_cost), 2))
    if req.independent_head_haul is not None:
        order.independent_head_haul = max(0.0, round(float(req.independent_head_haul), 2))
    if req.purchase_order_sn is not None:
        order.purchase_order_sn = req.purchase_order_sn.strip()
    if req.purchase_notes is not None:
        order.purchase_notes = req.purchase_notes.strip()
    if req.domestic_tracking_no is not None:
        order.domestic_tracking_no = req.domestic_tracking_no.strip()

    db.add(MakroOrderStatusLog(
        order_id=order.id,
        order_sn=order.order_id,
        change_type="PROCUREMENT_UPDATE",
        old_value=f"采购成本: ¥{old_cost or 0.0}",
        new_value=f"采购成本: ¥{order.purchase_cost or 0.0}",
        operator=current_user.username if current_user else "MANUAL",
        notes=f"采购单号: {order.purchase_order_sn or '-'}, 快递单号: {order.domestic_tracking_no or '-'}"
    ))

    db.commit()
    db.refresh(order)
    return {
        "success": True,
        "order_id": order.id,
        "purchase_cost": order.purchase_cost,
        "purchase_order_sn": order.purchase_order_sn,
        "purchase_notes": order.purchase_notes,
        "domestic_tracking_no": order.domestic_tracking_no,
        "message": f"订单 #{order.order_id} 采购信息与单号已保存"
    }

# =========================================================================
# 3.1 录入订单实测克重，按重核算头程并联动商品资料库
# =========================================================================
@router.put("/update-weight-and-freight", summary="录入订单各商品实测克重，自动核算头程并联动商品库")
def update_order_weight_and_freight(
    req: UpdateWeightAndFreightRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.query(MakroOrder).filter(MakroOrder.id == req.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="未找到该订单")
    _verify_store_access(current_user, order.store_id, db)

    # 0. 参数归一化与多轨兼容转换
    normalized_items_weight = dict(req.items_weight or {})
    if req.items:
        for it in req.items:
            s_sku = (it.sku_id or "").strip()
            if s_sku:
                normalized_items_weight[s_sku] = float(it.weight_g or 0.0)

    sync_same_sku = req.sync_same_sku_orders
    if req.propagate_to_same_sku is not None:
        sync_same_sku = bool(req.propagate_to_same_sku)

    # 1. 更新 raw_items_json 内各商品的 weight_g
    total_g = 0
    sku_weights = {}
    if order.raw_items_json:
        try:
            raw_oi = json.loads(order.raw_items_json)
            for it in raw_oi:
                sku = it.get("sku_id") or it.get("sku") or it.get("fsn") or ""
                qty = int(it.get("quantity") or 1)
                if sku in normalized_items_weight:
                    w_g = max(0, int(normalized_items_weight[sku]))
                    it["weight_g"] = w_g
                elif str(it.get("id")) in normalized_items_weight:
                    w_g = max(0, int(normalized_items_weight[str(it.get("id"))]))
                    it["weight_g"] = w_g
                else:
                    w_g = int(it.get("weight_g") or 0)
                
                if sku:
                    sku_weights[sku] = w_g
                total_g += w_g * qty
            order.raw_items_json = json.dumps(raw_oi, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"更新订单 raw_items_json 克重异常: {e}")

    final_total_weight = 0
    if req.total_weight_g is not None and req.total_weight_g >= 0:
        final_total_weight = max(0, int(round(req.total_weight_g)))
    elif total_g > 0:
        final_total_weight = total_g
    elif normalized_items_weight:
        final_total_weight = sum(max(0, int(round(v))) for v in normalized_items_weight.values())

    old_weight = order.total_weight_g
    order.total_weight_g = final_total_weight
    order.is_weight_measured = True

    # 2. 空运单价 (默认 90.0)
    if req.air_freight_rate is not None and req.air_freight_rate > 0:
        order.air_freight_rate = round(float(req.air_freight_rate), 2)
    elif req.air_freight_rate == 0.0:
        order.air_freight_rate = 0.0

    setting_rate_row = db.query(SystemSetting).filter(SystemSetting.key == "default_air_freight_rate").first()
    global_rate = float(setting_rate_row.value) if setting_rate_row and setting_rate_row.value else 90.0
    effective_rate = order.air_freight_rate if order.air_freight_rate and order.air_freight_rate > 0 else global_rate

    # 按重自动更新头程运费 (若为 0 克，头程运费为 0.0)
    if final_total_weight == 0:
        order.independent_head_haul = 0.0
    else:
        order.independent_head_haul = round((final_total_weight / 1000.0) * effective_rate, 2)

    # 3. 若勾选同步更新至商品资料库 (MakroListing & MakroPiggybackItem)
    synced_catalog_count = 0
    if req.sync_to_catalog and sku_weights:
        for sku, w_g in sku_weights.items():
            if w_g > 0 and sku:
                w_kg = round(w_g / 1000.0, 3)
                db.query(MakroListing).filter(MakroListing.sku_id == sku).update(
                    {MakroListing.weight: w_kg}, synchronize_session=False
                )
                db.query(MakroPiggybackItem).filter(MakroPiggybackItem.seller_sku == sku).update(
                    {MakroPiggybackItem.weight: w_kg}, synchronize_session=False
                )
                synced_catalog_count += 1

    # 4. 若勾选同步当前未称重的同 SKU 订单
    diffused_orders_count = 0
    if sync_same_sku and sku_weights:
        other_orders = db.query(MakroOrder).filter(
            MakroOrder.id != order.id,
            or_(MakroOrder.total_weight_g == 0, MakroOrder.total_weight_g == None)
        ).all()
        for other in other_orders:
            if not other.raw_items_json:
                continue
            try:
                o_items = json.loads(other.raw_items_json)
                has_match = False
                o_total_g = 0
                for it in o_items:
                    o_sku = it.get("sku_id") or it.get("sku") or it.get("fsn") or ""
                    qty = int(it.get("quantity") or 1)
                    if o_sku in sku_weights and sku_weights[o_sku] > 0:
                        it["weight_g"] = sku_weights[o_sku]
                        has_match = True
                    o_total_g += int(it.get("weight_g") or 0) * qty
                if has_match and o_total_g > 0:
                    other.raw_items_json = json.dumps(o_items, ensure_ascii=False)
                    other.total_weight_g = o_total_g
                    other.is_weight_measured = True
                    o_rate = other.air_freight_rate if other.air_freight_rate and other.air_freight_rate > 0 else global_rate
                    other.independent_head_haul = round((o_total_g / 1000.0) * o_rate, 2)
                    diffused_orders_count += 1
            except Exception:
                pass

    db.add(MakroOrderStatusLog(
        order_id=order.id,
        order_sn=order.order_id,
        change_type="WEIGHT_UPDATE",
        old_value=f"{old_weight or 0}g",
        new_value=f"{final_total_weight}g",
        operator=current_user.username if current_user else "MANUAL",
        notes=f"实测克重: {final_total_weight}g, 核算头程: ¥{order.independent_head_haul}"
    ))

    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "order_id": order.id,
        "total_weight_g": order.total_weight_g,
        "head_haul_cost": order.independent_head_haul,
        "synced_catalog_count": synced_catalog_count,
        "diffused_orders_count": diffused_orders_count,
        "message": f"订单实测总重 {order.total_weight_g}g，核算头程 ¥{order.independent_head_haul}"
    }

# =========================================================================
# 3.2 40x30mm 订单打包唯一标签 PDF 生成与批量下载
# =========================================================================
@router.get("/labels/{order_id}/pdf", summary="生成并下载单笔订单 40x30mm 打包防混标签 PDF")
def download_order_label_pdf(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.query(MakroOrder).filter(MakroOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="未找到该订单")
    _verify_store_access(current_user, order.store_id, db)

    # 标记已打单
    now = datetime.now()
    if not order.label_printed:
        order.label_printed = True
        order.label_printed_at = now
        db.add(MakroOrderStatusLog(
            order_id=order.id,
            order_sn=order.order_id,
            change_type="LABEL_PRINTED",
            old_value="UNPRINTED",
            new_value="PRINTED",
            operator=current_user.username if current_user else "SYSTEM",
            notes="打印防混/面单标签"
        ))
        db.commit()

    pdf_bytes = OrderLabelService.generate_single_label_pdf(order)
    filename = f"label_{order.order_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.post("/batch-labels/pdf", summary="批量合并生成多页 40x30mm 打包防混标签 PDF (支持连打)")
def download_batch_labels_pdf(
    req: BatchLabelsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.order_ids:
        raise HTTPException(status_code=400, detail="请至少勾选一笔订单")

    orders = db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).all()
    if not orders:
        raise HTTPException(status_code=404, detail="未找到有效订单")

    now = datetime.now()
    for o in orders:
        if not o.label_printed:
            o.label_printed = True
            o.label_printed_at = now
            db.add(MakroOrderStatusLog(
                order_id=o.id,
                order_sn=o.order_id,
                change_type="LABEL_PRINTED",
                old_value="UNPRINTED",
                new_value="PRINTED",
                operator=current_user.username if current_user else "SYSTEM",
                notes="批量打印防混/面单标签"
            ))
    db.commit()

    pdf_bytes = OrderLabelService.generate_batch_labels_pdf(orders)
    filename = f"batch_labels_{len(orders)}_orders.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# =========================================================================
# 4. PO 批次拼单与财务总账
# =========================================================================
@router.post("/po/create-and-assign", summary="合并订单生成新 PO 采购发货批次")
def create_po_and_assign_orders(
    req: CreatePoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    po_sn = req.po_number or f"PO{datetime.now().strftime('%Y%m%d%H%M%S')}"
    existing = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.po_number == po_sn).first()
    if existing:
        po_sn = f"{po_sn}-{int(datetime.now().timestamp()) % 1000}"

    orders_weight_kg = 0.0
    assigned_count = 0
    if req.order_ids:
        assigned_orders = db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).all()
        orders_weight_kg = round(sum(int(o.total_weight_g or 0) for o in assigned_orders) / 1000.0, 2)
        assigned_count = len(assigned_orders)

    # 默认自动带入所选订单的累计重量作为参考，可自由修改为打包后的整箱过秤重量
    pkg_weight = round(float(req.package_weight_kg), 2) if (req.package_weight_kg is not None and req.package_weight_kg > 0) else orders_weight_kg

    po = MakroPurchaseOrder(
        po_number=po_sn,
        store_id=req.store_id,
        name=req.name or f"南非空运批次 {datetime.now().strftime('%m月%d日')}",
        forwarder_name=req.forwarder_name or "",
        air_waybill_no=req.air_waybill_no or "",
        domestic_logistics_fee=max(0.0, round(float(req.domestic_logistics_fee or 0.0), 2)),
        head_haul_fee=max(0.0, round(float(req.head_haul_fee or 0.0), 2)),
        logistics_loss_fee=max(0.0, round(float(req.logistics_loss_fee or 0.0), 2)),
        box_length_cm=max(0.0, round(float(req.box_length_cm or 0.0), 1)),
        box_width_cm=max(0.0, round(float(req.box_width_cm or 0.0), 1)),
        box_height_cm=max(0.0, round(float(req.box_height_cm or 0.0), 1)),
        package_weight_kg=max(0.0, pkg_weight),
        notes=req.notes or "",
        status="PLANNING"
    )
    db.add(po)
    db.flush()

    if req.order_ids:
        db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).update({
            MakroOrder.po_id: po.id
        }, synchronize_session=False)

    db.commit()
    return {
        "success": True,
        "po_id": po.id,
        "po_number": po.po_number,
        "name": po.name,
        "assigned_orders_count": assigned_count,
        "message": f"成功创建 PO 批次 [{po.po_number}]，已合并归档 {assigned_count} 笔订单"
    }

@router.put("/po/assign-existing", summary="将订单归档到已有 PO 或取消归档")
def assign_orders_to_existing_po(
    req: AssignPoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not req.order_ids:
        raise HTTPException(status_code=400, detail="请选择要操作的订单")

    if req.po_id:
        po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == req.po_id).first()
        if not po:
            raise HTTPException(status_code=404, detail="目标 PO 批次不存在")
        msg = f"成功将 {len(req.order_ids)} 笔订单归档至 PO [{po.po_number}]"
    else:
        msg = f"已将 {len(req.order_ids)} 笔订单移出 PO 批次"

    count = db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).update({
        MakroOrder.po_id: req.po_id
    }, synchronize_session=False)

    db.commit()
    return {"success": True, "updated_count": count, "message": msg}

@router.post("/po/remove-order", summary="一键将指定订单从 PO 批次中剔除")
def remove_order_from_po(
    req: RemoveOrderFromPoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == req.po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="目标 PO 批次不存在")

    order = db.query(MakroOrder).filter(MakroOrder.id == req.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="目标订单不存在")

    if order.po_id != po.id:
        raise HTTPException(status_code=400, detail=f"该订单不属于 PO [{po.po_number}]")

    order.po_id = None

    # 记录审计日志
    try:
        from app.models.makro_order_status_log import MakroOrderStatusLog
        log_entry = MakroOrderStatusLog(
            order_id=order.id,
            order_sn=order.order_id,
            change_type="PO_REMOVAL",
            from_status=po.po_number,
            to_status="UNASSIGNED",
            operator=getattr(current_user, "username", "SYSTEM"),
            note=f"从 PO 批次 [{po.po_number}] 中一键剔除"
        )
        db.add(log_entry)
    except Exception as e:
        logger.warning(f"记录 PO 剔除审计日志失败: {e}")

    db.commit()
    return {
        "success": True,
        "message": f"成功将订单 #{order.order_id} 从 PO [{po.po_number}] 中剔除",
        "po_id": po.id,
        "order_id": order.id
    }

@router.get("/po/list", summary="查询 PO 批次列表及财务利润总账 (支持对账分析)")
def list_purchase_orders(
    store_id: Optional[int] = Query(None, description="店铺 ID 过滤"),
    commission_rate: float = Query(17.5, description="平台扣点率 (%)"),
    shipping_fee_zar: float = Query(70.0, description="平台单笔运费 (ZAR)"),
    exchange_rate: float = Query(0.39, description="汇率"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    c_rate = float(commission_rate) if isinstance(commission_rate, (int, float)) else 17.5
    s_fee = float(shipping_fee_zar) if isinstance(shipping_fee_zar, (int, float)) else 70.0
    e_rate = float(exchange_rate) if isinstance(exchange_rate, (int, float)) else 0.39

    query = db.query(MakroPurchaseOrder)
    if store_id:
        query = query.filter(or_(MakroPurchaseOrder.store_id == store_id, MakroPurchaseOrder.store_id == None))

    pos = query.order_by(MakroPurchaseOrder.id.desc()).all()
    results = []

    setting_rate_row = db.query(SystemSetting).filter(SystemSetting.key == "default_air_freight_rate").first()
    global_air_rate = float(setting_rate_row.value) if setting_rate_row and setting_rate_row.value else 90.0

    for p in pos:
        orders = db.query(MakroOrder).filter(MakroOrder.po_id == p.id).all()
        orders_cnt = len(orders)
        total_sales_zar = sum(float(o.total_amount or 0.0) for o in orders)
        total_weight_g = sum(int(o.total_weight_g or 0) for o in orders)

        total_payout_cny = 0.0
        total_purchase_cny = 0.0
        sum_order_theoretical_head_haul = 0.0
        orders_summary = []

        for o in orders:
            st_ful = o.fulfillment_status or "NEW"
            is_abnormal = st_ful in ["CANCELLED_RTO", "cancelled", "returned"]
            if not is_abnormal:
                zar_n = (float(o.total_amount or 0.0) * (1.0 - c_rate / 100.0)) - s_fee
                order_payout_cny = round(max(0.0, zar_n) * e_rate, 2)
            else:
                order_payout_cny = 0.0
            total_payout_cny += order_payout_cny
            total_purchase_cny += float(o.purchase_cost or 0.0)

            # 累计该单按重理论头程运费
            o_rate = float(o.air_freight_rate) if (o.air_freight_rate and o.air_freight_rate > 0) else global_air_rate
            if o.total_weight_g is not None and o.total_weight_g >= 0:
                o_head_haul = (float(o.total_weight_g) / 1000.0) * o_rate
            else:
                o_head_haul = float(o.independent_head_haul or 0.0)
            sum_order_theoretical_head_haul += o_head_haul

            first_img_url = ""
            first_title = ""
            first_sku = ""
            if o.raw_items_json:
                try:
                    raw_oi = json.loads(o.raw_items_json)
                    if raw_oi and isinstance(raw_oi, list):
                        first_item = raw_oi[0]
                        pd = first_item.get("product_details") or {}
                        first_img_url = first_item.get("image_url") or pd.get("product_image") or pd.get("large_product_image") or ""
                        first_title = pd.get("title") or first_item.get("title") or ""
                        first_sku = first_item.get("sku_id") or first_item.get("sku") or first_item.get("fsn") or ""
                except Exception:
                    pass

            is_w_measured = bool(getattr(o, "is_weight_measured", False))
            orders_summary.append({
                "id": o.id,
                "order_id": o.order_id,
                "buyer_name": o.buyer_name or "",
                "total_amount": o.total_amount,
                "weight_g": o.total_weight_g if (is_w_measured and o.total_weight_g is not None) else 0,
                "is_weight_measured": is_w_measured,
                "purchase_cost": o.purchase_cost or 0.0,
                "head_haul_cost": round(o_head_haul, 2),
                "payout_cny": order_payout_cny,
                "fulfillment_status": st_ful,
                "image_url": first_img_url,
                "product_title": first_title,
                "sku": first_sku
            })

        # PO 视角费用计算：以 PO 录入的实际国内运费和实际头程为准
        domestic_fee = float(p.domestic_logistics_fee or 0.0)
        head_haul = float(p.head_haul_fee or 0.0)
        loss_fee = float(p.logistics_loss_fee or 0.0)

        total_cost_cny = total_purchase_cny + domestic_fee + head_haul + loss_fee
        net_profit_cny = total_payout_cny - total_cost_cny
        margin = (net_profit_cny / total_payout_cny * 100.0) if total_payout_cny > 0 else 0.0

        # 头程对账差异: 订单按重累计理论头程 - 货代实收账单
        freight_diff_cny = round(sum_order_theoretical_head_haul - head_haul, 2)

        box_l = float(p.box_length_cm or 0.0)
        box_w = float(p.box_width_cm or 0.0)
        box_h = float(p.box_height_cm or 0.0)
        pkg_w = float(p.package_weight_kg or 0.0)
        dim_str = f"{box_l:g}×{box_w:g}×{box_h:g} cm" if (box_l > 0 and box_w > 0 and box_h > 0) else ""

        results.append({
            "id": p.id,
            "po_number": p.po_number,
            "name": p.name,
            "status": p.status,
            "forwarder_name": p.forwarder_name or "",
            "air_waybill_no": p.air_waybill_no or "",
            "domestic_logistics_fee": domestic_fee,
            "head_haul_fee": head_haul,
            "logistics_loss_fee": loss_fee,
            "box_length_cm": box_l,
            "box_width_cm": box_w,
            "box_height_cm": box_h,
            "package_weight_kg": pkg_w,
            "box_dimensions_str": dim_str,
            "notes": p.notes or "",
            "orders_count": orders_cnt,
            "total_weight_g": total_weight_g,
            "total_weight_kg": round(total_weight_g / 1000.0, 2),
            "total_sales_zar": round(total_sales_zar, 2),
            "total_payout_cny": round(total_payout_cny, 2),
            "total_purchase_cost_cny": round(total_purchase_cny, 2),
            "total_domestic_logistics_cny": round(domestic_fee, 2),
            "total_cost_cny": round(total_cost_cny, 2),
            "net_profit_cny": round(net_profit_cny, 2),
            "profit_margin_percent": round(margin, 2),
            "sum_order_theoretical_head_haul": round(sum_order_theoretical_head_haul, 2),
            "freight_diff_cny": freight_diff_cny,
            "orders": orders_summary,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else ""
        })

    return {"total": len(results), "items": results}

@router.put("/po/update/{po_id}", summary="修改 PO 批次头程费用、国内运费、箱规尺寸与提单号")
def update_purchase_order(
    po_id: int,
    req: UpdatePoRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="PO 批次不存在")

    if req.name is not None:
        po.name = req.name.strip()
    if req.status is not None:
        po.status = req.status
    if req.forwarder_name is not None:
        po.forwarder_name = req.forwarder_name.strip()
    if req.air_waybill_no is not None:
        po.air_waybill_no = req.air_waybill_no.strip()
    if req.domestic_logistics_fee is not None:
        po.domestic_logistics_fee = max(0.0, round(float(req.domestic_logistics_fee), 2))
    if req.head_haul_fee is not None:
        po.head_haul_fee = max(0.0, round(float(req.head_haul_fee), 2))
    if req.logistics_loss_fee is not None:
        po.logistics_loss_fee = max(0.0, round(float(req.logistics_loss_fee), 2))
    if req.box_length_cm is not None:
        po.box_length_cm = max(0.0, round(float(req.box_length_cm), 1))
    if req.box_width_cm is not None:
        po.box_width_cm = max(0.0, round(float(req.box_width_cm), 1))
    if req.box_height_cm is not None:
        po.box_height_cm = max(0.0, round(float(req.box_height_cm), 1))
    if req.package_weight_kg is not None:
        po.package_weight_kg = max(0.0, round(float(req.package_weight_kg), 2))
    if req.notes is not None:
        po.notes = req.notes.strip()

    po.updated_at = datetime.now()
    db.commit()
    return {"success": True, "po_id": po.id, "message": f"PO [{po.po_number}] 信息已更新"}

@router.post("/po/batch-update-fulfillment/{po_id}", summary="一键批量同步流转 PO 内所有订单状态 (保持异常退回订单不变)")
def batch_update_po_orders_fulfillment(
    po_id: int,
    req: BatchPoFulfillmentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="PO 批次不存在")

    now = datetime.now()
    operator_name = current_user.username if current_user else "PO_BATCH"

    # 保持异常退回订单不变: 排除 CANCELLED_RTO 等异常状态订单
    eligible_orders = db.query(MakroOrder).filter(
        MakroOrder.po_id == po.id,
        ~MakroOrder.fulfillment_status.in_(["CANCELLED_RTO", "cancelled", "returned"])
    ).all()

    for o in eligible_orders:
        old_st = o.fulfillment_status or "NEW"
        o.fulfillment_status = req.target_status
        o.fulfillment_updated_at = now
        db.add(MakroOrderStatusLog(
            order_id=o.id,
            order_sn=o.order_id,
            change_type="FULFILLMENT_STATUS",
            old_value=old_st,
            new_value=req.target_status,
            operator=operator_name,
            notes=f"PO [{po.po_number}] 批量同步流转 (保持异常退回单不变)"
        ))

    db.commit()
    return {
        "success": True,
        "po_id": po.id,
        "updated_count": len(eligible_orders),
        "target_status": req.target_status,
        "message": f"PO [{po.po_number}] 名下 {len(eligible_orders)} 笔正常订单已流转至: {req.target_status} (异常退回订单已保持不变)"
    }

@router.delete("/po/delete/{po_id}", summary="解散/删除 PO 批次 (订单自动恢复未归档)")
def delete_purchase_order(
    po_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="PO 批次不存在")

    # 解绑订单
    db.query(MakroOrder).filter(MakroOrder.po_id == po.id).update({
        MakroOrder.po_id: None
    }, synchronize_session=False)

    db.delete(po)
    db.commit()
    return {"success": True, "message": f"PO 批次 [{po.po_number}] 已解散，所有关联订单已恢复为未归档状态"}

# =========================================================================
# 5. Makro 网关订单同步
# =========================================================================
@router.post("/sync", summary="从 Makro 网关同步店铺订单")
def sync_store_orders(
    store_id: int = Query(..., description="店铺 ID"),
    async_task: bool = Query(True, description="是否后台异步执行 (默认 True)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    store = _verify_store_access(current_user, store_id, db)
    if not async_task:
        try:
            res = MakroPortalService.sync_store_orders(store, db)
            return {
                "success": True,
                "message": f"店铺 '{store.name}' 订单同步完成，共同步 {res['total_synced']} 条订单",
                "state_counts": res.get("state_counts", {})
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"同步订单失败: {str(e)}")

    task = task_manager.create_task(
        task_type="SYNC_STORE_ORDERS",
        name=f"同步官方订单 [{store.name}]",
        total=0
    )
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        thread_db = SessionLocal()
        try:
            def _progress(cur: int, tot: int, msg: str):
                tm.update_progress(tid, current=cur, total=tot, current_title=msg)

            res = MakroPortalService.sync_store_orders(
                store=store,
                db=thread_db,
                progress_callback=_progress,
                is_cancelled_fn=lambda: tm.is_cancelled(tid)
            )
            tm.finish_task(
                tid,
                status="SUCCESS",
                message=f"店铺 '{store.name}' 订单同步完成，共同步 {res['total_synced']} 条订单",
                success_count=res.get("total_synced", 0)
            )
        except Exception as ex:
            logger.error(f"同步店铺 [{store.name}] 官方订单后台任务异常: {ex}", exc_info=True)
            tm.finish_task(
                tid,
                status="FAILED",
                message=f"同步订单失败: {str(ex)}"
            )
        finally:
            thread_db.close()

    task_manager.start_task(task_id, _worker)
    return {
        "success": True,
        "task_id": task_id,
        "message": f"已在后台启动店铺 '{store.name}' 官方订单多批次同步任务"
    }

# =========================================================================
# 6. 订单打印标记与生命周期审计轨迹
# =========================================================================
@router.put("/{order_id}/toggle-label-printed", summary="手动切换/标记订单打包标签打印状态")
def toggle_order_label_printed(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.query(MakroOrder).filter(MakroOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="未找到该订单")
    _verify_store_access(current_user, order.store_id, db)

    new_val = not bool(order.label_printed)
    order.label_printed = new_val
    order.label_printed_at = datetime.now() if new_val else None

    db.add(MakroOrderStatusLog(
        order_id=order.id,
        order_sn=order.order_id,
        change_type="LABEL_PRINTED",
        old_value="PRINTED" if not new_val else "UNPRINTED",
        new_value="PRINTED" if new_val else "UNPRINTED",
        operator=current_user.username if current_user else "MANUAL",
        notes="手动标记切换打印状态"
    ))
    db.commit()
    return {
        "success": True,
        "order_id": order.id,
        "label_printed": new_val,
        "label_printed_at": order.label_printed_at.strftime("%Y-%m-%d %H:%M:%S") if order.label_printed_at else ""
    }

@router.get("/{order_id}/status-history", summary="查询订单状态全生命周期流转记录")
def get_order_status_history(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.query(MakroOrder).filter(MakroOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="未找到该订单")
    _verify_store_access(current_user, order.store_id, db)

    logs = db.query(MakroOrderStatusLog).filter(
        MakroOrderStatusLog.order_id == order.id
    ).order_by(MakroOrderStatusLog.created_at.desc(), MakroOrderStatusLog.id.desc()).all()

    return {
        "success": True,
        "order_id": order.id,
        "order_sn": order.order_id,
        "history": [
            {
                "id": log.id,
                "change_type": log.change_type,
                "old_value": log.old_value,
                "new_value": log.new_value,
                "operator": log.operator,
                "notes": log.notes,
                "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""
            }
            for log in logs
        ]
    }
