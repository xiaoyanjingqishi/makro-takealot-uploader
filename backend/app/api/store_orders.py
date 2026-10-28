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
from ..models.makro_buffer_stock import MakroBufferStock
from ..services.makro_portal_service import MakroPortalService
from ..services.label_service import OrderLabelService
from ..services.task_manager import task_manager, TaskManager
from ..utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/store-orders", tags=["店铺订单管理与履约"])

# =========================================================================
# 中文状态映射与时效计算辅助函数
# =========================================================================
CHANGE_TYPE_MAP = {
    "FULFILLMENT_STATUS": "履约状态流转",
    "WEIGHT_UPDATE": "录入实测重量",
    "PO_ASSIGNMENT": "加入PO拼单批次",
    "PO_REMOVAL": "移出PO拼单批次",
    "PROCUREMENT_UPDATE": "采购信息录入",
    "INITIAL_SYNC": "官方订单首次同步",
    "OFFICIAL_STATUS": "官方状态更新",
    "STOCK_ALLOCATION": "南非现货调拨履约",
    "STOCK_REALLOCATED_OUT": "滞留货品转出履约",
    "BUFFER_STOCK_INFLOW": "异常订单滞留入库"
}

STATUS_ZH_MAP = {
    "NEW": "新订单",
    "PURCHASED": "已采购",
    "PO_PACKING": "PO打包",
    "SENT_TO_FORWARDER": "发送货代",
    "AIR_FREIGHT": "货代空运",
    "ARRIVED_SA": "到达南非",
    "SA_PICKUP": "南非取件",
    "COMPLETED": "订单完结",
    "CANCELLED_RTO": "异常退回",
    "pending_rtd": "待打单出库",
    "pending_labels": "待打单",
    "pending_handover": "待交接",
    "packed": "已打包",
    "rtd": "就绪待发",
    "upcoming": "预备订单",
    "processing": "处理中",
    "in_transit": "运输派送中",
    "delivered": "已签收送达",
    "shipments_delivered": "已送达",
    "cancelled": "已取消",
    "returned": "已退货",
    "PACKING": "正在打包",
    "SHIPPED": "货代起运",
    "PLANNING": "正在打包"
}

def format_status_zh(code: Optional[str]) -> str:
    if not code:
        return "-"
    return STATUS_ZH_MAP.get(code, code)

def format_change_type_zh(code: Optional[str]) -> str:
    if not code:
        return "系统流转"
    return CHANGE_TYPE_MAP.get(code, code)

def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "<1分钟"
    mins = int(seconds // 60)
    hours = mins // 60
    days = hours // 24
    rem_hours = hours % 24
    rem_mins = mins % 60
    if days > 0:
        return f"{days}天{rem_hours}小时"
    if hours > 0:
        return f"{hours}小时{rem_mins}分"
    return f"{mins}分钟"

def calc_dispatch_countdown(dispatch_by_date: Optional[datetime], is_finished: bool = False) -> Optional[Dict[str, Any]]:
    if not dispatch_by_date or is_finished:
        return None
    now = datetime.now()
    if dispatch_by_date >= now:
        diff = dispatch_by_date - now
        days = diff.days
        hours = diff.seconds // 3600
        text = f"剩余 {days}天{hours}小时" if days > 0 else f"剩余 {hours}小时"
        is_urgent = (days == 0 and hours <= 12)
        return {
            "text": text,
            "status": "URGENT" if is_urgent else "NORMAL",
            "is_overdue": False,
            "is_urgent": is_urgent,
            "days": days,
            "hours": hours
        }
    else:
        diff = now - dispatch_by_date
        days = diff.days
        hours = diff.seconds // 3600
        text = f"已逾期 {days}天{hours}小时" if days > 0 else f"已逾期 {hours}小时"
        return {
            "text": text,
            "status": "OVERDUE",
            "is_overdue": True,
            "is_urgent": True,
            "days": days,
            "hours": hours
        }


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

class ProcurementRecordItem(BaseModel):
    sn: Optional[str] = ""
    purchase_sn: Optional[str] = ""
    cost: float = 0.0
    purchase_cost: float = 0.0
    tracking_no: Optional[str] = ""
    domestic_tracking: Optional[str] = ""
    notes: Optional[str] = ""

class UpdateProcurementRecordsRequest(BaseModel):
    order_id: int
    records: Optional[List[ProcurementRecordItem]] = None
    procurement_records: Optional[List[ProcurementRecordItem]] = None

class UpdatePoStatusRequest(BaseModel):
    status: Optional[str] = None
    po_status: Optional[str] = None
    sync_member_orders: bool = False

class AllocateStockRequest(BaseModel):
    target_order_id: int
    buffer_stock_id: Optional[int] = None
    stock_id: Optional[int] = None
    relabeling_fee_cny: Optional[float] = 0.0
    notes: Optional[str] = None

class CreateBufferStockRequest(BaseModel):
    store_id: int
    sku_id: str
    fsn: Optional[str] = None
    title: str
    image_url: Optional[str] = None
    quantity: int = 1
    location: str = "SA_WAREHOUSE"
    status: str = "AVAILABLE"
    purchase_cost_cny: Optional[float] = 0.0
    head_haul_cost_cny: Optional[float] = 0.0
    relabeling_fee_cny: Optional[float] = 0.0
    notes: Optional[str] = None

class UpdateBufferStockStatusRequest(BaseModel):
    status: str  # AVAILABLE, REALLOCATED, REFUNDED_1688, SCRAPPED
    notes: Optional[str] = None

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
                MakroOrder.domestic_tracking_no.ilike(s),
                MakroOrder.raw_items_json.ilike(s),
                MakroOrder.procurement_records_json.ilike(s)
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

    # 预加载南非海外现货库存 (SA_WAREHOUSE & AVAILABLE) 便于匹配待发订单
    available_sa_stocks_db = db.query(MakroBufferStock).filter(
        MakroBufferStock.store_id == store.id,
        MakroBufferStock.location == "SA_WAREHOUSE",
        MakroBufferStock.status == "AVAILABLE"
    ).all()
    sa_stock_map = {}
    for st_item in available_sa_stocks_db:
        if st_item.sku_id not in sa_stock_map:
            sa_stock_map[st_item.sku_id] = []
        sa_stock_map[st_item.sku_id].append({
            "id": st_item.id,
            "sku_id": st_item.sku_id,
            "fsn": st_item.fsn or "",
            "title": st_item.title,
            "quantity": st_item.quantity,
            "source_order_sn": st_item.source_order_sn or "-",
            "purchase_cost_cny": st_item.purchase_cost_cny,
            "head_haul_cost_cny": st_item.head_haul_cost_cny,
            "relabeling_fee_cny": st_item.relabeling_fee_cny or 0.0,
            "created_at": st_item.created_at.strftime("%Y-%m-%d %H:%M") if st_item.created_at else ""
        })

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

        # 头程运费: 按单单商品重量进行计算 (专项5: 未填写重量的订单转到异常退回时，头程运费默认为0)
        if is_abnormal and not is_measured:
            head_haul_cost = 0.0
        elif final_total_weight_g > 0:
            head_haul_cost = round((final_total_weight_g / 1000.0) * effective_air_rate, 2)
        elif is_measured and final_total_weight_g == 0:
            head_haul_cost = 0.0
        else:
            head_haul_cost = round(float(o.independent_head_haul or 0.0), 2)

        p_cost = round(float(o.purchase_cost or 0.0), 2)
        total_cost_cny = round(p_cost + allocated_domestic + head_haul_cost + allocated_loss, 2)
        net_profit_cny = round(payout_cny - total_cost_cny, 2)

        # 发货倒计时与时效警告 (专项4)
        is_order_finished = st_ful in ["COMPLETED", "completed", "delivered", "shipments_delivered"]
        cd_info = calc_dispatch_countdown(o.dispatch_by_date, is_order_finished)

        # 匹配南非海外现货 (未完结且未调拨订单)
        order_matching_stocks = []
        if st_ful in ["NEW", "PURCHASED", "PO_PACKING"] and o.fulfillment_type != "SA_STOCK_ALLOCATION":
            for itm in order_items:
                s_code = itm.get("sku_id") or itm.get("fsn")
                if s_code and s_code in sa_stock_map:
                    order_matching_stocks.extend(sa_stock_map[s_code])

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
            "procurement_records": (json.loads(o.procurement_records_json) if o.procurement_records_json else ([{"sn": o.purchase_order_sn or "", "cost": p_cost, "tracking_no": o.domestic_tracking_no or "", "notes": o.purchase_notes or ""}] if (o.purchase_order_sn or p_cost > 0) else [])),
            "fulfillment_type": o.fulfillment_type or "DIRECT_PROCUREMENT",
            "allocated_stock_id": o.allocated_stock_id,
            "reallocated_from_order_sn": o.reallocated_from_order_sn or "",
            "has_matching_sa_stock": len(order_matching_stocks) > 0,
            "matching_sa_stock_count": sum(x.get("quantity", 1) for x in order_matching_stocks),
            "matching_sa_stocks": order_matching_stocks,
            "remaining_dispatch_time": cd_info,
            "dispatch_time_status": cd_info["status"] if cd_info else "NONE",
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
        "PO_PACKING": base_store_query.filter(MakroOrder.fulfillment_status == "PO_PACKING").count(),
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

        # 头程运费按重计算 (专项5: 未称重的异常退回订单头程归0)
        x_st = x.fulfillment_status or "NEW"
        x_is_abnormal = x_st in ["CANCELLED_RTO", "cancelled", "returned"]
        x_is_measured = bool(getattr(x, "is_weight_measured", False))
        if x_is_abnormal and not x_is_measured:
            pass
        else:
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
        "NEW", "PURCHASED", "PO_PACKING", "SENT_TO_FORWARDER",
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
        # 专项 5: 未填写重量的订单转到异常退回时，头程运费默认为 0.00
        if req.fulfillment_status == "CANCELLED_RTO" and not bool(getattr(o, "is_weight_measured", False)):
            o.independent_head_haul = 0.0
        db.add(MakroOrderStatusLog(
            order_id=o.id,
            order_sn=o.order_id,
            change_type="FULFILLMENT_STATUS",
            from_status=format_status_zh(old_st),
            to_status=format_status_zh(req.fulfillment_status),
            operator=operator_name,
            note="人工手动流转状态"
        ))

        # 核心机制: 订单取消时按所处物理阶段自动归集至滞留库存池
        if req.fulfillment_status == "CANCELLED_RTO" and old_st != "CANCELLED_RTO":
            loc = None
            if old_st in ["ARRIVED_SA", "SA_PICKUP"]:
                loc = "SA_WAREHOUSE"
            elif old_st == "AIR_FREIGHT":
                loc = "IN_TRANSIT_AIR"
            elif old_st in ["PURCHASED", "PO_PACKING", "SENT_TO_FORWARDER"]:
                loc = "DOMESTIC_FORWARDER"
            
            if loc and o.raw_items_json:
                try:
                    raw_items = json.loads(o.raw_items_json)
                    for itm in raw_items:
                        pd = itm.get("product_details") or {}
                        itm_sku = itm.get("sku_id") or itm.get("sku") or itm.get("fsn") or "UNKNOWN_SKU"
                        itm_title = itm.get("title") or pd.get("title") or f"商品 ({itm_sku})"
                        itm_img = itm.get("image_url") or pd.get("product_image") or pd.get("large_product_image") or ""
                        itm_qty = int(itm.get("quantity") or 1)
                        itm_fsn = itm.get("fsn") or pd.get("fsn") or pd.get("product_id") or ""
                        
                        db.add(MakroBufferStock(
                            store_id=o.store_id,
                            sku_id=itm_sku,
                            fsn=itm_fsn,
                            title=itm_title,
                            image_url=itm_img,
                            quantity=itm_qty,
                            location=loc,
                            status="AVAILABLE",
                            source_order_id=o.id,
                            source_order_sn=o.order_id,
                            source_po_id=o.po_id,
                            purchase_cost_cny=float(o.purchase_cost or 0.0),
                            head_haul_cost_cny=float(o.independent_head_haul or 0.0),
                            notes=f"订单取消滞留: 原履约阶段 [{format_status_zh(old_st)}]，实物归入 {format_status_zh(loc)}"
                        ))
                    
                    db.add(MakroOrderStatusLog(
                        order_id=o.id,
                        order_sn=o.order_id,
                        change_type="BUFFER_STOCK_INFLOW",
                        from_status=format_status_zh(old_st),
                        to_status="滞留入库",
                        operator="SYSTEM",
                        note=f"货品自动入库至滞留现货池 ({format_status_zh(loc)})"
                    ))
                except Exception as ex:
                    logger.warning(f"订单 #{o.order_id} 自动归集滞留现货异常: {ex}")

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
# 3.01 录入/更新订单多笔采购单明细与采购金额实时求和 (专项 1)
# =========================================================================
@router.put("/update-procurement-records", summary="录入订单多笔采购单号、单价与物流单号")
def update_order_procurement_records(
    req: UpdateProcurementRecordsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    order = db.query(MakroOrder).filter(MakroOrder.id == req.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    _verify_store_access(current_user, order.store_id, db)

    records_input = req.records if req.records is not None else (req.procurement_records or [])
    records_list = []
    total_cost = 0.0
    for r in records_input:
        sn_val = (r.purchase_sn or r.sn or "").strip()
        cost_val = max(0.0, round(float(r.purchase_cost if r.purchase_cost > 0 else (r.cost or 0.0)), 2))
        track_val = (r.domestic_tracking or r.tracking_no or "").strip()
        notes_val = (r.notes or "").strip()
        total_cost += cost_val
        records_list.append({
            "purchase_sn": sn_val,
            "sn": sn_val,
            "purchase_cost": cost_val,
            "cost": cost_val,
            "domestic_tracking": track_val,
            "tracking_no": track_val,
            "notes": notes_val
        })
    
    total_cost = round(total_cost, 2)
    order.procurement_records_json = json.dumps(records_list, ensure_ascii=False)
    order.purchase_cost = total_cost
    if records_list:
        order.purchase_order_sn = records_list[0]["sn"]
        order.domestic_tracking_no = records_list[0]["tracking_no"]
        order.purchase_notes = records_list[0]["notes"]
    else:
        order.purchase_order_sn = ""
        order.domestic_tracking_no = ""
        order.purchase_notes = ""

    db.add(MakroOrderStatusLog(
        order_id=order.id,
        order_sn=order.order_id,
        change_type="PROCUREMENT_UPDATE",
        from_status=None,
        to_status=f"采购总额: ¥{total_cost}",
        operator=current_user.username if current_user else "MANUAL",
        note=f"录入多笔采购记录 (共 {len(records_list)} 笔，总额 ¥{total_cost})"
    ))

    db.commit()
    db.refresh(order)
    return {
        "success": True,
        "order_id": order.id,
        "purchase_cost": total_cost,
        "procurement_records": records_list,
        "message": f"订单 #{order.order_id} 成功保存 {len(records_list)} 笔采购记录，采购总成本: ¥{total_cost}"
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
        now = datetime.now()
        user_name = current_user.username if current_user else "MANUAL"
        assigned_orders = db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).all()
        for o in assigned_orders:
            o.po_id = po.id
            if o.fulfillment_status in ["NEW", "PURCHASED"]:
                old_st = o.fulfillment_status
                o.fulfillment_status = "PO_PACKING"
                o.fulfillment_updated_at = now
                db.add(MakroOrderStatusLog(
                    order_id=o.id,
                    order_sn=o.order_id,
                    change_type="PO_ASSIGNMENT",
                    from_status=format_status_zh(old_st),
                    to_status=format_status_zh("PO_PACKING"),
                    operator=user_name,
                    note=f"加入批次 [{po.po_number}]，自动流转至 [PO打包]"
                ))

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

    now = datetime.now()
    user_name = current_user.username if current_user else "MANUAL"
    orders_to_update = db.query(MakroOrder).filter(MakroOrder.id.in_(req.order_ids)).all()
    count = len(orders_to_update)
    for o in orders_to_update:
        o.po_id = req.po_id
        if req.po_id:
            if o.fulfillment_status in ["NEW", "PURCHASED"]:
                old_st = o.fulfillment_status
                o.fulfillment_status = "PO_PACKING"
                o.fulfillment_updated_at = now
                db.add(MakroOrderStatusLog(
                    order_id=o.id,
                    order_sn=o.order_id,
                    change_type="PO_ASSIGNMENT",
                    from_status=format_status_zh(old_st),
                    to_status=format_status_zh("PO_PACKING"),
                    operator=user_name,
                    note=f"加入批次 [{po.po_number}]，自动流转至 [PO打包]"
                ))
        else:
            if o.fulfillment_status == "PO_PACKING":
                o.fulfillment_status = "PURCHASED"
                o.fulfillment_updated_at = now
                db.add(MakroOrderStatusLog(
                    order_id=o.id,
                    order_sn=o.order_id,
                    change_type="PO_REMOVAL",
                    from_status=format_status_zh("PO_PACKING"),
                    to_status=format_status_zh("PURCHASED"),
                    operator=user_name,
                    note="移出 PO 批次，自动恢复为 [已采购]"
                ))

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
    old_ful = order.fulfillment_status
    if order.fulfillment_status == "PO_PACKING":
        order.fulfillment_status = "PURCHASED"
        order.fulfillment_updated_at = datetime.now()

    # 记录审计日志
    try:
        log_entry = MakroOrderStatusLog(
            order_id=order.id,
            order_sn=order.order_id,
            change_type="PO_REMOVAL",
            from_status=format_status_zh(old_ful),
            to_status=format_status_zh(order.fulfillment_status),
            operator=getattr(current_user, "username", "SYSTEM"),
            note=f"从批次 [{po.po_number}] 剔除，自动恢复为 [{format_status_zh(order.fulfillment_status)}]"
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

# =========================================================================
# 4.01 更新 PO 批次当前物流状态并记录流转时间轴 (专项 8)
# =========================================================================
@router.put("/po/{po_id}/status", summary="更新 PO 批次当前物流状态并记录流转时间轴")
def update_po_status(
    po_id: int,
    req: UpdatePoStatusRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="PO 批次不存在")
    if po.store_id:
        _verify_store_access(current_user, po.store_id, db)

    now = datetime.now()
    new_st = (req.po_status or req.status or "PACKING").strip()
    old_status = po.status or "PACKING"
    last_updated = po.status_updated_at or po.created_at or now
    delta_sec = max(0, (now - last_updated).total_seconds())
    dur_str = format_duration(delta_sec)

    timeline = []
    if po.status_timeline_json:
        try:
            timeline = json.loads(po.status_timeline_json)
        except Exception:
            pass

    timeline.append({
        "status": format_status_zh(new_st),
        "from_status": old_status,
        "from_status_zh": format_status_zh(old_status),
        "to_status": new_st,
        "to_status_zh": format_status_zh(new_st),
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "interval_from_prev": dur_str,
        "duration": dur_str,
        "duration_seconds": int(delta_sec),
        "operator": current_user.username if current_user else "MANUAL"
    })

    po.status = new_st
    po.status_updated_at = now
    po.status_timeline_json = json.dumps(timeline, ensure_ascii=False)

    synced_count = 0
    if req.sync_member_orders:
        target_order_st = None
        if req.status == "PACKING":
            target_order_st = "PO_PACKING"
        elif req.status == "SENT_TO_FORWARDER":
            target_order_st = "SENT_TO_FORWARDER"
        elif req.status == "AIR_FREIGHT":
            target_order_st = "AIR_FREIGHT"
        elif req.status == "ARRIVED_SA":
            target_order_st = "ARRIVED_SA"
        elif req.status == "COMPLETED":
            target_order_st = "COMPLETED"

        if target_order_st:
            member_orders = db.query(MakroOrder).filter(
                MakroOrder.po_id == po.id,
                ~MakroOrder.fulfillment_status.in_(["CANCELLED_RTO", "cancelled", "returned"])
            ).all()
            for o in member_orders:
                old_o_st = o.fulfillment_status
                o.fulfillment_status = target_order_st
                o.fulfillment_updated_at = now
                db.add(MakroOrderStatusLog(
                    order_id=o.id,
                    order_sn=o.order_id,
                    change_type="FULFILLMENT_STATUS",
                    from_status=format_status_zh(old_o_st),
                    to_status=format_status_zh(target_order_st),
                    operator=current_user.username if current_user else "MANUAL",
                    note=f"PO批次 [{po.po_number}] 状态变更联动同步"
                ))
                synced_count += 1

    db.commit()
    return {
        "success": True,
        "po_id": po.id,
        "status": po.status,
        "po_status": format_status_zh(po.status),
        "status_zh": format_status_zh(po.status),
        "status_updated_at": po.status_updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_since_prev": dur_str,
        "duration_text": dur_str,
        "status_timeline": timeline,
        "synced_member_orders": synced_count,
        "message": f"PO [{po.po_number}] 状态已变更为 [{format_status_zh(po.status)}]"
    }

@router.get("/po/list", summary="查询 PO 批次列表及财务利润总账 (支持对账分析)")
def list_purchase_orders(
    store_id: Optional[int] = Query(None, description="店铺 ID 过滤"),
    search: Optional[str] = Query(None, description="搜索PO编号、提单号、包含的订单号"),
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

    if search and search.strip():
        s_term = f"%{search.strip()}%"
        matching_order_po_ids = [r[0] for r in db.query(MakroOrder.po_id).filter(MakroOrder.order_id.ilike(s_term), MakroOrder.po_id.isnot(None)).distinct().all()]
        query = query.filter(
            or_(
                MakroPurchaseOrder.po_number.ilike(s_term),
                MakroPurchaseOrder.air_waybill_no.ilike(s_term),
                MakroPurchaseOrder.name.ilike(s_term),
                MakroPurchaseOrder.forwarder_name.ilike(s_term),
                MakroPurchaseOrder.id.in_(matching_order_po_ids)
            )
        )

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
        status_summary = {}

        for o in orders:
            st_ful = o.fulfillment_status or "NEW"
            st_zh = format_status_zh(st_ful)
            status_summary[st_zh] = status_summary.get(st_zh, 0) + 1
            st_ful = o.fulfillment_status or "NEW"
            is_abnormal = st_ful in ["CANCELLED_RTO", "cancelled", "returned"]
            if not is_abnormal:
                zar_n = (float(o.total_amount or 0.0) * (1.0 - c_rate / 100.0)) - s_fee
                order_payout_cny = round(max(0.0, zar_n) * e_rate, 2)
            else:
                order_payout_cny = 0.0
            total_payout_cny += order_payout_cny
            total_purchase_cny += float(o.purchase_cost or 0.0)

            # 累计该单按重理论头程运费 (专项 5: 未称重异常退回为0)
            is_w_measured = bool(getattr(o, "is_weight_measured", False))
            if is_abnormal and not is_w_measured:
                o_head_haul = 0.0
            else:
                o_rate = float(o.air_freight_rate) if (o.air_freight_rate and o.air_freight_rate > 0) else global_air_rate
                if o.total_weight_g is not None and o.total_weight_g > 0:
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

            is_order_finished = st_ful in ["COMPLETED", "completed", "delivered", "shipments_delivered"]
            cd_info = calc_dispatch_countdown(o.dispatch_by_date, is_order_finished)

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
                "fulfillment_status_zh": st_zh,
                "image_url": first_img_url,
                "product_title": first_title,
                "sku": first_sku,
                "dispatch_by_date": o.dispatch_by_date.strftime("%Y-%m-%d %H:%M") if o.dispatch_by_date else "-",
                "remaining_dispatch_time": cd_info,
                "dispatch_time_status": cd_info["status"] if cd_info else "NONE"
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

        last_updated = p.status_updated_at or p.created_at or datetime.now()
        dur_sec = max(0, (datetime.now() - last_updated).total_seconds())
        time_in_status_str = format_duration(dur_sec)

        timeline = []
        if p.status_timeline_json:
            try:
                timeline = json.loads(p.status_timeline_json)
            except Exception:
                pass

        results.append({
            "id": p.id,
            "po_number": p.po_number,
            "name": p.name,
            "status": p.status or "PACKING",
            "status_zh": format_status_zh(p.status or "PACKING"),
            "status_updated_at": p.status_updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.status_updated_at else (p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else ""),
            "time_in_current_status": time_in_status_str,
            "status_summary": status_summary,
            "status_timeline": timeline,
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

    # 解绑订单并回退状态
    orders_to_revert = db.query(MakroOrder).filter(MakroOrder.po_id == po.id).all()
    now = datetime.now()
    user_name = current_user.username if current_user else "MANUAL"
    for o in orders_to_revert:
        o.po_id = None
        if o.fulfillment_status == "PO_PACKING":
            o.fulfillment_status = "PURCHASED"
            o.fulfillment_updated_at = now
            db.add(MakroOrderStatusLog(
                order_id=o.id,
                order_sn=o.order_id,
                change_type="PO_REMOVAL",
                from_status=format_status_zh("PO_PACKING"),
                to_status=format_status_zh("PURCHASED"),
                operator=user_name,
                note=f"PO 批次 [{po.po_number}] 解散，自动恢复为 [已采购]"
            ))

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

    hist_items = []
    for log in logs:
        from_val = log.from_status or log.old_value
        to_val = log.to_status or log.new_value
        from_zh = format_status_zh(from_val) if from_val else "-"
        to_zh = format_status_zh(to_val) if to_val else "-"

        hist_items.append({
            "id": log.id,
            "change_type": log.change_type,
            "change_type_zh": format_change_type_zh(log.change_type),
            "old_value": from_val,
            "from_status": from_val,
            "from_status_zh": from_zh,
            "new_value": to_val,
            "to_status": to_val,
            "to_status_zh": to_zh,
            "operator": log.operator or "系统",
            "notes": log.note or log.notes or "-",
            "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else ""
        })

    return {
        "success": True,
        "order_id": order.id,
        "order_sn": order.order_id,
        "history": hist_items
    }


# =========================================================================
# 7. 南非滞留现货与缓冲库存池管理 (海外库存调拨引擎)
# =========================================================================
@router.get("/buffer-stocks", summary="查询海外滞留现货与缓冲库存池")
def list_buffer_stocks(
    store_id: Optional[int] = Query(None),
    location: Optional[str] = Query(None),  # SA_WAREHOUSE, IN_TRANSIT_AIR, DOMESTIC_FORWARDER
    status: Optional[str] = Query(None),    # AVAILABLE, REALLOCATED, REFUNDED_1688, SCRAPPED
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(MakroBufferStock)
    if store_id:
        _verify_store_access(current_user, store_id, db)
        query = query.filter(MakroBufferStock.store_id == store_id)
    if location and location != "all":
        query = query.filter(MakroBufferStock.location == location)
    if status and status != "all":
        query = query.filter(MakroBufferStock.status == status)
    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                MakroBufferStock.sku_id.ilike(s),
                MakroBufferStock.fsn.ilike(s),
                MakroBufferStock.title.ilike(s),
                MakroBufferStock.source_order_sn.ilike(s),
                MakroBufferStock.reallocated_to_order_sn.ilike(s)
            )
        )
    
    stocks = query.order_by(MakroBufferStock.id.desc()).all()
    
    # 统计全局汇总指标
    base_all = db.query(MakroBufferStock)
    if store_id:
        base_all = base_all.filter(MakroBufferStock.store_id == store_id)
    
    available_sa_stocks = base_all.filter(MakroBufferStock.location == "SA_WAREHOUSE", MakroBufferStock.status == "AVAILABLE").all()
    total_sa_qty = sum(s.quantity for s in available_sa_stocks)
    total_sa_val = round(sum((s.purchase_cost_cny + s.head_haul_cost_cny) * s.quantity for s in available_sa_stocks), 2)
    
    in_transit_qty = sum(s.quantity for s in base_all.filter(MakroBufferStock.location == "IN_TRANSIT_AIR", MakroBufferStock.status == "AVAILABLE").all())
    domestic_qty = sum(s.quantity for s in base_all.filter(MakroBufferStock.location == "DOMESTIC_FORWARDER", MakroBufferStock.status == "AVAILABLE").all())
    reallocated_qty = sum(s.quantity for s in base_all.filter(MakroBufferStock.status == "REALLOCATED").all())
    recovered_val = round(sum((s.purchase_cost_cny + s.head_haul_cost_cny) * s.quantity for s in base_all.filter(MakroBufferStock.status == "REALLOCATED").all()), 2)
    
    summary = {
        "available_sa_count": total_sa_qty,
        "available_sa_value_cny": total_sa_val,
        "in_transit_air_count": in_transit_qty,
        "domestic_forwarder_count": domestic_qty,
        "reallocated_count": reallocated_qty,
        "recovered_value_cny": recovered_val
    }

    # 预加载当前所有处于 NEW / PURCHASED / PO_PACKING 的未完结订单，用于实时反向匹配
    pending_orders = db.query(MakroOrder).filter(
        MakroOrder.fulfillment_status.in_(["NEW", "PURCHASED", "PO_PACKING"]),
        ~MakroOrder.status.in_(["completed", "delivered", "shipments_delivered"])
    ).all()

    sku_to_pending_orders = {}
    for po_ord in pending_orders:
        if po_ord.raw_items_json:
            try:
                for itm in json.loads(po_ord.raw_items_json):
                    s_id = itm.get("sku_id") or itm.get("sku") or itm.get("fsn") or ""
                    if s_id:
                        if s_id not in sku_to_pending_orders:
                            sku_to_pending_orders[s_id] = []
                        sku_to_pending_orders[s_id].append({
                            "id": po_ord.id,
                            "order_id": po_ord.order_id,
                            "order_date": po_ord.order_date.strftime("%Y-%m-%d %H:%M") if po_ord.order_date else "",
                            "fulfillment_status": po_ord.fulfillment_status,
                            "fulfillment_status_zh": format_status_zh(po_ord.fulfillment_status),
                            "buyer_name": po_ord.buyer_name or "-",
                            "purchase_order_sn": po_ord.purchase_order_sn or ""
                        })
            except Exception:
                pass

    items = []
    now = datetime.now()
    for st in stocks:
        days_stranded = max(0, (now - st.created_at).days)
        match_orders = sku_to_pending_orders.get(st.sku_id, [])
        items.append({
            "id": st.id,
            "store_id": st.store_id,
            "sku_id": st.sku_id,
            "fsn": st.fsn or "",
            "title": st.title,
            "image_url": st.image_url or "",
            "quantity": st.quantity,
            "quantity_available": st.quantity if st.status == "AVAILABLE" else 0,
            "quantity_original": st.quantity,
            "unit_cost_cny": st.purchase_cost_cny,
            "location": st.location,
            "location_zh": "南非本地仓现货" if st.location == "SA_WAREHOUSE" else ("国际空运在途" if st.location == "IN_TRANSIT_AIR" else "国内货代滞留"),
            "status": st.status,
            "status_zh": "闲置可用" if st.status == "AVAILABLE" else ("已调拨履约" if st.status == "REALLOCATED" else ("已1688退款" if st.status == "REFUNDED_1688" else "已报废/核销")),
            "source_order_id": st.source_order_id,
            "source_order_sn": st.source_order_sn or "-",
            "source_po_id": st.source_po_id,
            "source_po_number": st.source_po_number or "-",
            "reallocated_to_order_id": st.reallocated_to_order_id,
            "reallocated_to_order_sn": st.reallocated_to_order_sn or "-",
            "reallocated_at": st.reallocated_at.strftime("%Y-%m-%d %H:%M") if st.reallocated_at else "",
            "purchase_cost_cny": st.purchase_cost_cny,
            "head_haul_cost_cny": st.head_haul_cost_cny,
            "relabeling_fee_cny": st.relabeling_fee_cny,
            "total_asset_cny": round((st.purchase_cost_cny + st.head_haul_cost_cny) * st.quantity, 2),
            "days_stranded": days_stranded,
            "notes": st.notes or "",
            "matching_orders": match_orders,
            "has_matching_orders": len(match_orders) > 0,
            "created_at": st.created_at.strftime("%Y-%m-%d %H:%M") if st.created_at else ""
        })

    return {
        "success": True,
        "summary": summary,
        "total": len(items),
        "items": items
    }

@router.post("/allocate-stock", summary="使用海外滞留现货一键转单调拨履约新订单")
def allocate_buffer_stock(
    req: AllocateStockRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    target_order = db.query(MakroOrder).filter(MakroOrder.id == req.target_order_id).first()
    if not target_order:
        raise HTTPException(status_code=404, detail="目标新订单不存在")
    _verify_store_access(current_user, target_order.store_id, db)

    stock_id_to_use = req.buffer_stock_id or req.stock_id
    if not stock_id_to_use:
        raise HTTPException(status_code=400, detail="必须指定库存 ID (stock_id 或 buffer_stock_id)")
    stock = db.query(MakroBufferStock).filter(MakroBufferStock.id == stock_id_to_use).first()
    if not stock:
        raise HTTPException(status_code=404, detail="指定的滞留库存不存在")
    if stock.status != "AVAILABLE":
        raise HTTPException(status_code=400, detail=f"该库存状态为 [{stock.status}]，非闲置可用状态，无法调拨")

    now = datetime.now()
    user_name = current_user.username if current_user else "MANUAL"

    # 更新库存状态
    stock.status = "REALLOCATED"
    stock.reallocated_to_order_id = target_order.id
    stock.reallocated_to_order_sn = target_order.order_id
    stock.reallocated_at = now
    if req.relabeling_fee_cny is not None:
        stock.relabeling_fee_cny = max(0.0, round(float(req.relabeling_fee_cny), 2))
    if req.notes:
        stock.notes = f"{stock.notes or ''} [调拨备注: {req.notes.strip()}]"

    # 更新目标订单
    old_target_st = target_order.fulfillment_status or "NEW"
    if stock.location == "SA_WAREHOUSE":
        target_order.fulfillment_status = "ARRIVED_SA"
        target_order.fulfillment_type = "SA_STOCK_ALLOCATION"
    elif stock.location == "IN_TRANSIT_AIR":
        target_order.fulfillment_status = "AIR_FREIGHT"
        target_order.fulfillment_type = "SA_STOCK_ALLOCATION"
    else:
        target_order.fulfillment_status = "SENT_TO_FORWARDER"
        target_order.fulfillment_type = "DOMESTIC_STOCK_ALLOCATION"

    target_order.fulfillment_updated_at = now
    target_order.allocated_stock_id = stock.id
    target_order.reallocated_from_order_sn = stock.source_order_sn

    # 继承成本 (原1688采购成本 + 原头程运费 + 换标费)
    target_order.purchase_cost = round(float(stock.purchase_cost_cny or 0.0), 2)
    # 南非本地现货调拨免除国际头程空运费，仅计入本地换标操作费
    target_order.independent_head_haul = round(float(stock.relabeling_fee_cny or 0.0), 2)
    target_order.fulfillment_type = "SA_BUFFER_STOCK"
    stock.quantity = 0

    db.add(MakroOrderStatusLog(
        order_id=target_order.id,
        order_sn=target_order.order_id,
        change_type="STOCK_ALLOCATION",
        from_status=format_status_zh(old_target_st),
        to_status=format_status_zh(target_order.fulfillment_status),
        operator=user_name,
        note=f"调拨使用滞留现货 (原单 #{stock.source_order_sn or '-'}), 状态跃迁至 [{format_status_zh(target_order.fulfillment_status)}]"
    ))

    # 若原来源订单存在，写入被调拨日志
    if stock.source_order_id:
        db.add(MakroOrderStatusLog(
            order_id=stock.source_order_id,
            order_sn=stock.source_order_sn or str(stock.source_order_id),
            change_type="STOCK_REALLOCATED_OUT",
            from_status="滞留现货",
            to_status="成功调拨",
            operator=user_name,
            note=f"滞留商品已成功调拨给新订单 #{target_order.order_id} 履约，货品损失已挽回"
        ))

    db.commit()

    has_1688 = bool(target_order.purchase_order_sn and target_order.purchase_order_sn.strip())
    refund_msg = ""
    if has_1688:
        refund_msg = f"⚠️ 注意：该订单此前曾录入 1688 采购单号 [{target_order.purchase_order_sn}]，现已成功转用南非本地现货！请务必立即前往 1688 申请退款退货，避免国内重复采购与资金损失！"

    return {
        "success": True,
        "target_order_id": target_order.id,
        "target_order_sn": target_order.order_id,
        "new_fulfillment_status": target_order.fulfillment_status,
        "new_fulfillment_status_zh": format_status_zh(target_order.fulfillment_status),
        "allocated_stock_id": stock.id,
        "has_1688_to_refund": has_1688,
        "refund_warning": refund_msg,
        "message": f"成功将滞留现货调拨给订单 #{target_order.order_id}！订单已直通 [{format_status_zh(target_order.fulfillment_status)}]！"
    }

@router.get("/procurement-summary", summary="智能采购工作台汇总 (按SKU聚合订单需求并扣减现货计算净需采购量)")
def get_procurement_summary(
    store_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(MakroOrder).filter(
        MakroOrder.fulfillment_status.in_(["NEW", "PURCHASED", "PO_PACKING"]),
        ~MakroOrder.status.in_(["completed", "delivered", "shipments_delivered", "cancelled"])
    )
    if store_id:
        _verify_store_access(current_user, store_id, db)
        query = query.filter(MakroOrder.store_id == store_id)
    
    orders = query.all()

    # 预加载可用滞留库存 (SA_WAREHOUSE 与 DOMESTIC_FORWARDER)
    buf_stocks = db.query(MakroBufferStock).filter(MakroBufferStock.status == "AVAILABLE").all()
    sku_sa_stock_map = {}
    sku_dom_stock_map = {}
    for bs in buf_stocks:
        if bs.location == "SA_WAREHOUSE":
            sku_sa_stock_map[bs.sku_id] = sku_sa_stock_map.get(bs.sku_id, 0) + bs.quantity
        elif bs.location == "DOMESTIC_FORWARDER":
            sku_dom_stock_map[bs.sku_id] = sku_dom_stock_map.get(bs.sku_id, 0) + bs.quantity

    sku_aggregated = {}
    for o in orders:
        if o.raw_items_json:
            try:
                for itm in json.loads(o.raw_items_json):
                    sku = itm.get("sku_id") or itm.get("sku") or itm.get("fsn") or ""
                    if not sku:
                        continue
                    pd = itm.get("product_details") or {}
                    qty = int(itm.get("quantity") or 1)
                    title = itm.get("title") or pd.get("title") or f"商品 ({sku})"
                    img = itm.get("image_url") or pd.get("product_image") or pd.get("large_product_image") or ""
                    fsn = itm.get("fsn") or pd.get("fsn") or pd.get("product_id") or ""

                    if sku not in sku_aggregated:
                        sku_aggregated[sku] = {
                            "sku_id": sku,
                            "fsn": fsn,
                            "title": title,
                            "image_url": img,
                            "total_demand": 0,
                            "sa_stock": sku_sa_stock_map.get(sku, 0),
                            "domestic_stock": sku_dom_stock_map.get(sku, 0),
                            "net_procurement_needed": 0,
                            "orders": []
                        }
                    sku_aggregated[sku]["total_demand"] += qty
                    sku_aggregated[sku]["orders"].append({
                        "order_id": o.id,
                        "order_sn": o.order_id,
                        "order_date": o.order_date.strftime("%Y-%m-%d %H:%M") if o.order_date else "",
                        "quantity": qty,
                        "fulfillment_status": o.fulfillment_status,
                        "fulfillment_status_zh": format_status_zh(o.fulfillment_status),
                        "purchase_cost": o.purchase_cost or 0.0,
                        "purchase_order_sn": o.purchase_order_sn or ""
                    })
            except Exception:
                pass

    result_list = []
    total_demand_all = 0
    total_net_buy_all = 0
    total_sa_deducted_all = 0

    for sku, data in sku_aggregated.items():
        net_buy = max(0, data["total_demand"] - data["sa_stock"] - data["domestic_stock"])
        data["net_procurement_needed"] = net_buy
        total_demand_all += data["total_demand"]
        total_net_buy_all += net_buy
        total_sa_deducted_all += min(data["total_demand"], data["sa_stock"])
        result_list.append(data)

    # 排序：净采量高的排在前面
    result_list.sort(key=lambda x: x["net_procurement_needed"], reverse=True)

    return {
        "success": True,
        "total_skus": len(result_list),
        "total_order_demand": total_demand_all,
        "total_net_buy": total_net_buy_all,
        "total_sa_deducted": total_sa_deducted_all,
        "items": result_list
    }

@router.post("/buffer-stocks/create", summary="手动录入海外现货或盘点库存")
def create_buffer_stock(
    req: CreateBufferStockRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _verify_store_access(current_user, req.store_id, db)
    stock = MakroBufferStock(
        store_id=req.store_id,
        sku_id=req.sku_id.strip(),
        fsn=(req.fsn or "").strip(),
        title=req.title.strip(),
        image_url=req.image_url or "",
        quantity=max(1, req.quantity),
        location=req.location,
        status=req.status,
        purchase_cost_cny=max(0.0, round(float(req.purchase_cost_cny or 0.0), 2)),
        head_haul_cost_cny=max(0.0, round(float(req.head_haul_cost_cny or 0.0), 2)),
        relabeling_fee_cny=max(0.0, round(float(req.relabeling_fee_cny or 0.0), 2)),
        notes=req.notes or ""
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return {
        "success": True,
        "stock_id": stock.id,
        "message": f"成功录入现货库存 [{stock.sku_id}]，件数: {stock.quantity}"
    }

@router.put("/buffer-stocks/{stock_id}/status", summary="更新滞留现货状态 (核销/报废/1688已退款)")
def update_buffer_stock_status(
    stock_id: int,
    req: UpdateBufferStockStatusRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    stock = db.query(MakroBufferStock).filter(MakroBufferStock.id == stock_id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="库存记录不存在")
    _verify_store_access(current_user, stock.store_id, db)

    valid_statuses = ["AVAILABLE", "REALLOCATED", "REFUNDED_1688", "SCRAPPED"]
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"无效状态: {req.status}")

    old_st = stock.status
    stock.status = req.status
    if req.notes:
        stock.notes = f"{stock.notes or ''} [{req.notes.strip()}]"

    db.commit()
    return {
        "success": True,
        "stock_id": stock.id,
        "old_status": old_st,
        "new_status": stock.status,
        "message": f"库存状态已由 [{old_st}] 更新为 [{stock.status}]"
    }
