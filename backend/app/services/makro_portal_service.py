import logging
import requests
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from ..models.store import Store, ProductStoreListing
from ..models.makro_listing import MakroListing
from ..models.makro_order import MakroOrder
from ..models.product import Product
from ..models.makro_piggyback import MakroPiggybackItem

logger = logging.getLogger(__name__)

MAKRO_HOST = "https://seller.makro.co.za"

class MakroPortalService:
    """
    对接 Makro 卖家官方网关的核心服务 (涵盖在线商品、库存改动、审核追踪、订单与时效同步)
    """

    @staticmethod
    def _build_headers(store: Store, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": MAKRO_HOST,
            "referer": f"{MAKRO_HOST}/index.html",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest"
        }
        if store.fk_csrf_token:
            headers["fk-csrf-token"] = store.fk_csrf_token.strip()
        if store.cookie:
            headers["cookie"] = store.cookie.strip()
        if store.default_location_id:
            headers["x-location-id"] = store.default_location_id.strip()

        if extra:
            headers.update(extra)
        return headers

    @classmethod
    def fetch_listings_data_for_states(
        cls,
        store: Store,
        internal_state: str = "ACTIVE",
        page_no: int = 0,
        page_size: int = 30,
        search_text: str = ""
    ) -> Dict[str, Any]:
        """查询指定状态下的商品列表与各状态总数"""
        url = f"{MAKRO_HOST}/napi/listing/listingsDataForStates"
        headers = cls._build_headers(store)

        payload = {
            "search_text": search_text,
            "column": {
                "pagination": {
                    "batch_no": page_no,
                    "batch_size": page_size
                }
            }
        }
        if internal_state and internal_state != "ALL":
            payload["search_filters"] = {"internal_state": internal_state}
            if internal_state == "ACTIVE":
                payload["column"]["sort"] = {"column_name": "demand_weight", "sort_by": "DESC"}
            elif internal_state == "READY_FOR_ACTIVATION":
                payload["column"]["sort"] = {"column_name": "potential_rfa_unit", "sort_by": "DESC"}

        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            raise Exception(f"Makro 接口响应异常 (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()

    @classmethod
    def fetch_in_progress_listings(
        cls,
        store: Store,
        page_no: int = 0,
        page_size: int = 20,
        state: Optional[str] = None,
        sku_id: Optional[str] = None,
        vertical: Optional[str] = None
    ) -> Dict[str, Any]:
        """查询在途审核中的 Listing 审核状态 (含创建FSN中、QC失败驳回原因等)"""
        url = f"{MAKRO_HOST}/napi/listing/inProgressSingle?sellerId={store.seller_id}"
        headers = cls._build_headers(store)

        must_filters = []
        if state and state != "ALL":
            must_filters.append({"state": state})
        if sku_id:
            must_filters.append({"skuId": sku_id})
        if vertical:
            must_filters.append({"vertical": vertical})

        payload = {
            "batchNo": page_no,
            "batchSize": page_size,
            "searchContext": {
                "sortAttributes": ["lastModified"],
                "sortOrder": "DESC",
                "filter": {
                    "must": [{"match": must_filters}] if must_filters else [{"match": []}]
                }
            },
            "sellerId": store.seller_id
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            raise Exception(f"Makro 审核接口响应异常 (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()

    @classmethod
    def fetch_inventory_by_location(
        cls,
        store: Store,
        items: List[Dict[str, str]],
        location_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        调用 Makro 官方 POST /napi/listing/getInventoryByLocation 获取商品的仓库真实库存与占用库存
        items 格式: [{"listing_id": "LST...", "service_profile": "NON_FBF"}, ...]
        返回格式: {"LST...": {"quantity": 500, "reserved": 0, "loc_id": "..."}, ...}
        """
        if not items:
            return {}

        loc_id = location_id or store.default_location_id
        url = f"{MAKRO_HOST}/napi/listing/getInventoryByLocation"
        extra_headers = {"x-location-id": loc_id} if loc_id else {}
        headers = cls._build_headers(store, extra_headers)

        result_map = {}
        chunk_size = 40
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            payload = {"fetch_inventory_requests": chunk}
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=25)
                if resp.status_code == 200:
                    data = resp.json()
                    resp_dict = data.get("response", {})
                    for lst_id, details in resp_dict.items():
                        if isinstance(details, dict):
                            stocks = details.get("stock_count", [])
                            if stocks and isinstance(stocks, list) and isinstance(stocks[0], dict):
                                result_map[lst_id] = {
                                    "quantity": int(stocks[0].get("quantity", 0)),
                                    "reserved": int(stocks[0].get("reserved", 0)),
                                    "loc_id": stocks[0].get("loc_id") or loc_id
                                }
                            else:
                                result_map[lst_id] = {"quantity": 0, "reserved": 0, "loc_id": loc_id}
            except Exception as e:
                logger.warning(f"获取官方分仓库存异常: {e}")

        return result_map

    @classmethod
    def parse_in_progress_item(cls, raw_item: Dict[str, Any]) -> Dict[str, Any]:
        """深度解析官方 inProgressSingle 单条数据结构，提取标题、品牌、价格、图片与错误诊断"""
        groups = raw_item.get("groups", [])
        g0 = groups[0] if groups else {}
        cat_entity = g0.get("catalogRequestEntity", {})
        cat_attr = cat_entity.get("catalogAttributes", {})
        list_entity = g0.get("listingRequestEntity", {})
        list_attr = list_entity.get("listingAttributes", {})

        # 基础标识
        sku_id = raw_item.get("skuId") or g0.get("skuId") or list_entity.get("skuId")
        request_id = g0.get("requestId") or raw_item.get("groupId") or ""
        txn_id = g0.get("txnId") or ""
        vertical = raw_item.get("vertical") or g0.get("vertical") or cat_entity.get("vertical") or ""
        raw_state = raw_item.get("state") or g0.get("state") or "IN_PROGRESS"
        raw_upper = str(raw_state).upper()

        # 对齐 Makro 官方前端标准映射字典 (listings.8bfc91ea9d8e3cae250e.js)
        # To: { draft: ["DRAFT"], qc_in_progress: ["SUBMITTED", "FSN_CREATION_IN_PROGRESS", "FSN_CREATION_COMPLETED", "LISTING_CREATION_IN_PROGRESS", "LISTING_REQUEST_SUBMITTED"], qc_failed: ["QC_FAILED"], listing_creation_error: ["LISTING_CREATION_ERROR"], qc_successful: ["LISTING_CREATION_COMPLETED", "COMPLETED"] }
        if raw_upper in ["LISTING_CREATION_COMPLETED", "COMPLETED", "QC_PASSED", "APPROVED"]:
            state = "QC_PASSED"
        elif raw_upper in ["DRAFT"]:
            state = "DRAFT"
        elif raw_upper in ["QC_FAILED", "LISTING_CREATION_ERROR"]:
            state = "QC_FAILED"
        else:
            # 官方规范：FSN_CREATION_COMPLETED 仅表示基础属性建档生成 FSN，Listing 仍处于平台质检流程中
            state = "QC_IN_PROGRESS"

        delete_allowed = raw_item.get("deleteAllowed", False)

        # 标题与品牌
        brand_list = cat_attr.get("brand", [])
        brand = brand_list[0].get("value") if brand_list and isinstance(brand_list[0], dict) else ""

        model_list = cat_attr.get("model_number", [])
        model_number = model_list[0].get("value") if model_list and isinstance(model_list[0], dict) else ""

        desc_list = cat_attr.get("description", [])
        description = desc_list[0].get("value") if desc_list and isinstance(desc_list[0], dict) else ""

        title = model_number or description[:120] or f"{brand} {vertical}".strip() or "未命名商品"

        # 主图提取
        images_dict = cat_entity.get("images", {})
        images = list(images_dict.values()) if isinstance(images_dict, dict) else (images_dict if isinstance(images_dict, list) else [])
        image_url = images[0] if images else None

        # 价格提取
        fsp = 0.0
        fsp_list = list_attr.get("flipkart_selling_price", [])
        if fsp_list and isinstance(fsp_list[0], dict):
            try:
                fsp = float(fsp_list[0].get("value", 0))
            except (ValueError, TypeError):
                pass

        mrp = 0.0
        mrp_list = list_attr.get("mrp", [])
        if mrp_list and isinstance(mrp_list[0], dict):
            try:
                mrp = float(mrp_list[0].get("value", 0))
            except (ValueError, TypeError):
                pass

        # 错误与驳回智能解析 (针对 DRAFT 或异常项)
        err_details = g0.get("errorDetails", {}) or {}
        error_summary = []
        has_errors = False

        # 1. 目录系统与人工校验错误
        cat_errs = err_details.get("catalogErrors", {}) or {}
        for err_section_key in ["systemValidationErrors", "manualValidationErrors"]:
            sec = cat_errs.get(err_section_key, {}) or {}
            # 属性错误
            for attr_k, attr_err_list in sec.get("attributeErrors", {}).items():
                has_errors = True
                err_msg = ""
                if attr_err_list and isinstance(attr_err_list, list) and isinstance(attr_err_list[0], dict):
                    err_msg = attr_err_list[0].get("errorString", "")
                if "is missing, please provide a value" in err_msg:
                    error_summary.append(f"缺失必填属性: {attr_k}")
                elif "attribute value should match with the allowed set" in err_msg:
                    error_summary.append(f"属性值不符: {attr_k}")
                elif "has no attribute called" in err_msg:
                    error_summary.append(f"类目无此属性: {attr_k}")
                else:
                    error_summary.append(f"属性错误: {attr_k}")
            # 全局错误 (如图片缺失)
            for g_err in sec.get("globalErrors", []):
                has_errors = True
                err_msg = g_err.get("errorString", "") if isinstance(g_err, dict) else ""
                if "Image count" in err_msg:
                    error_summary.append("缺少必填主图")
                else:
                    error_summary.append(err_msg[:30] if err_msg else "目录校验失败")

        # 2. Listing 校验错误 (如包装尺寸、制造方信息等)
        list_errs = err_details.get("listingErrors", {}) or {}
        for sec_k in ["systemValidationErrors"]:
            sec = list_errs.get(sec_k, {}) or {}
            for attr_k, attr_err_list in sec.get("attributeErrors", {}).items():
                has_errors = True
                error_summary.append(f"缺失刊登属性: {attr_k}")
            for g_err in sec.get("globalErrors", []):
                has_errors = True
                err_msg = g_err.get("errorString", "") if isinstance(g_err, dict) else ""
                if "package_length" in err_msg:
                    error_summary.append("缺少包装尺寸/重量")
                elif "SKU ID cannot be empty" in err_msg:
                    error_summary.append("SKU ID缺失")
                else:
                    error_summary.append(err_msg[:30] if err_msg else "刊登校验错误")

        if state == "DRAFT" and not error_summary:
            error_summary.append("草稿/待修改")

        # 去重
        dedup_summary = list(dict.fromkeys(error_summary))

        created_on = g0.get("createdOn") or raw_item.get("createdOn")
        last_modified = g0.get("lastModified") or raw_item.get("lastModified")

        return {
            "sku_id": sku_id,
            "request_id": request_id,
            "txn_id": txn_id,
            "vertical": vertical,
            "state": state,
            "raw_state": raw_state,
            "title": title,
            "brand": brand,
            "image_url": image_url,
            "fsp": fsp,
            "mrp": mrp,
            "has_errors": has_errors or (state == "DRAFT"),
            "error_summary": dedup_summary,
            "error_details": err_details,
            "delete_allowed": delete_allowed,
            "official_created_on": created_on,
            "official_last_modified": last_modified
        }

    @classmethod
    def update_inventory(
        cls,
        store: Store,
        sku_id: str,
        product_id: str,
        new_inventory: int,
        location_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """修改指定 SKU 的库存数量"""
        return cls.batch_update_inventory(
            store=store,
            items=[{"sku_id": sku_id, "product_id": product_id, "inventory": new_inventory}],
            location_id=location_id
        )

    @classmethod
    def batch_update_inventory(
        cls,
        store: Store,
        items: List[Dict[str, Any]],
        location_id: Optional[str] = None,
        chunk_size: int = 40
    ) -> Dict[str, Any]:
        """批量修改指定 SKU 集合的库存数量 (分批并发或分块回写 Makro 官方网关)
        items 格式: [{'sku_id': '...', 'product_id': '...', 'inventory': 999}, ...]
        """
        target_loc = location_id or store.default_location_id
        if not target_loc:
            raise Exception("未找到店铺仓库 Location ID，无法更新库存。请在多店铺管理中绑定仓库。")

        if not items:
            return {"success_count": 0, "failed_count": 0, "results": {}}

        url = f"{MAKRO_HOST}/napi/sfx/updateListingsInventory?sellerId={store.seller_id}&locationId={target_loc}"
        headers = cls._build_headers(store, {"x-location-id": target_loc})

        total_success = 0
        total_failed = 0
        all_results = {}

        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            payload = {}
            for it in chunk:
                sku = it["sku_id"]
                p_id = it.get("product_id") or ""
                inv = int(it.get("inventory", 0))
                payload[sku] = {
                    "product_id": p_id,
                    "locations": [
                        {
                            "id": target_loc,
                            "inventory": inv
                        }
                    ]
                }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=35)
                if resp.status_code == 200:
                    res_data = resp.json()
                    for sku in payload:
                        sku_res = res_data.get(sku, {})
                        if isinstance(sku_res, dict) and sku_res.get("status") == "SUCCESS":
                            total_success += 1
                            all_results[sku] = {"status": "SUCCESS"}
                        else:
                            total_failed += 1
                            all_results[sku] = {
                                "status": "FAILED",
                                "detail": sku_res.get("message", "更新失败") if isinstance(sku_res, dict) else str(sku_res)
                            }
                else:
                    for sku in payload:
                        total_failed += 1
                        all_results[sku] = {"status": "FAILED", "detail": f"HTTP {resp.status_code}"}
            except Exception as e:
                for sku in payload:
                    total_failed += 1
                    all_results[sku] = {"status": "FAILED", "detail": str(e)}

        return {
            "success_count": total_success,
            "failed_count": total_failed,
            "results": all_results
        }

    @classmethod
    def fetch_order_state_counts(cls, store: Store) -> Dict[str, Any]:
        """获取各状态订单数量统计 (用于顶部徽章展示)"""
        url = f"{MAKRO_HOST}/napi/my-orders/state-counts?state=seller_easyship&serviceProfile=seller-fulfilled&sellerId={store.seller_id}"
        headers = cls._build_headers(store)

        resp = requests.get(url, headers=headers, timeout=25)
        if resp.status_code != 200:
            raise Exception(f"获取订单计数异常 (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()

    @classmethod
    def fetch_orders(
        cls,
        store: Store,
        status_group: str = "shipments_in_transit",
        page_num: int = 1,
        page_size: int = 25
    ) -> Dict[str, Any]:
        """获取订单列表 (基于 Makro 官方 V3 引擎契约)"""
        url = f"{MAKRO_HOST}/napi/my-orders/fetch?sellerId={store.seller_id}"
        headers = cls._build_headers(store)

        now_iso = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S.000+05:30")

        if status_group == "shipments_to_pack":
            params = {
                "seller_id": store.seller_id,
                "dispatch_after_date": {"to": now_iso}
            }
        elif status_group == "shipments_to_handover":
            params = {"seller_id": store.seller_id}
        elif status_group == "shipments_in_transit":
            params = {
                "seller_id": store.seller_id,
                "status": {"picked_up": "true", "dispatched": "true", "shipped": "true"}
            }
        elif status_group == "shipments_delivered":
            params = {"seller_id": store.seller_id}
        elif status_group == "shipments_upcoming":
            params = {"seller_id": store.seller_id}
        else:
            params = {"seller_id": store.seller_id}

        payload = {
            "status": status_group,
            "payload": {
                "pagination": {"page_num": page_num, "page_size": page_size},
                "params": params
            },
            "sellerId": store.seller_id
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=35)
        if resp.status_code != 200:
            raise Exception(f"获取订单列表异常 (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()

    # =========================================================================
    # 同步与持久化落地 (Sync Workers)
    # =========================================================================
    @classmethod
    def sync_store_listings(cls, store: Store, db: Session) -> Dict[str, Any]:
        """将 Makro 店铺当前全量在线商品同步至本地数据库"""
        states_to_sync = ["ACTIVE", "READY_FOR_ACTIVATION", "INACTIVE", "INACTIVATED_BY_FLIPKART", "ARCHIVED"]
        total_synced = 0
        state_counts = {}

        discovered_location_id = None

        for st in states_to_sync:
            batch_no = 0
            batch_size = 30
            st_count = 0
            while True:
                data = cls.fetch_listings_data_for_states(
                    store=store,
                    internal_state=st,
                    page_no=batch_no,
                    page_size=batch_size
                )
                if not state_counts:
                    state_counts = data.get("internal_state_count_map", {})

                listings_raw = data.get("listing_data_response", [])
                if not listings_raw:
                    break

                # 批量预加载官方真实仓库库存 (精准对接 POST /napi/listing/getInventoryByLocation)
                inv_requests = [
                    {"listing_id": r["listing_id"], "service_profile": r.get("service_profile") or "NON_FBF"}
                    for r in listings_raw if r.get("listing_id")
                ]
                inv_map = cls.fetch_inventory_by_location(store=store, items=inv_requests)

                for raw in listings_raw:
                    sku_id = raw.get("sku_id")
                    if not sku_id:
                        continue

                    # 尝试自动提取包装 location ID
                    if not discovered_location_id and raw.get("packages"):
                        for pkg in raw["packages"].values():
                            pass

                    # 查询本地是否已有该 Listing 快照
                    existing = (
                        db.query(MakroListing)
                        .filter(MakroListing.store_id == store.id, MakroListing.sku_id == sku_id)
                        .first()
                    )
                    if not existing:
                        existing = MakroListing(
                            store_id=store.id,
                            seller_id=store.seller_id,
                            sku_id=sku_id
                        )
                        db.add(existing)

                    existing.product_id = raw.get("product_id")
                    existing.listing_id = raw.get("listing_id")
                    existing.title = raw.get("title") or raw.get("product_id")
                    existing.brand = raw.get("brand") or store.default_brand
                    existing.vertical = raw.get("vertical")
                    existing.vertical_display_name = raw.get("vertical_display_name")
                    existing.image_url = raw.get("imageUrl")
                    existing.internal_state = raw.get("internal_state") or st
                    existing.ssp = float(raw.get("ssp") or 0.0)
                    existing.mrp = float(raw.get("mrp") or 0.0)

                    # 官方真实库存绑定与安全继承保护
                    lst_id = raw.get("listing_id")
                    inv_info = inv_map.get(lst_id) if lst_id else None
                    if inv_info and "quantity" in inv_info:
                        existing.inventory = int(inv_info["quantity"])
                    elif raw.get("inventory") is not None:
                        existing.inventory = int(raw.get("inventory"))
                    else:
                        pb_item = db.query(MakroPiggybackItem).filter(
                            MakroPiggybackItem.store_id == store.id,
                            MakroPiggybackItem.seller_sku == sku_id
                        ).first()
                        if pb_item and pb_item.inventory:
                            existing.inventory = pb_item.inventory
                        elif existing.inventory and existing.inventory > 0:
                            pass
                        elif (raw.get("internal_state") or st) == "ACTIVE":
                            existing.inventory = 500
                        else:
                            existing.inventory = existing.inventory or 0

                    # 尺寸重量
                    if raw.get("packages"):
                        for pkg in raw["packages"].values():
                            existing.weight = pkg.get("weight")
                            existing.length = pkg.get("length")
                            existing.breadth = pkg.get("breadth")
                            existing.height = pkg.get("height")
                            break

                    # 下架原因
                    existing.deactivation_reasons = json.dumps(raw.get("reason_for_deactivation", []), ensure_ascii=False)
                    existing.archival_reasons = json.dumps(raw.get("reason_for_archival", []), ensure_ascii=False)

                    # 双向关联本地选品商品 (通过 sku_id, makro_sku_id 或多店铺 ProductStoreListing)
                    local_p = db.query(Product).filter(
                        (Product.sku_id == sku_id) | 
                        (Product.makro_sku_id == sku_id) |
                        Product.store_listings.any(ProductStoreListing.makro_sku_id == sku_id)
                    ).first()
                    if local_p:
                        existing.local_product_id = local_p.id

                    existing.synced_at = datetime.now()
                    st_count += 1
                    total_synced += 1

                db.commit()
                if len(listings_raw) < batch_size:
                    break
                batch_no += 1

        return {
            "success": True,
            "total_synced": total_synced,
            "state_counts": state_counts
        }

    @classmethod
    def sync_store_orders(cls, store: Store, db: Session) -> Dict[str, Any]:
        """将 Makro 店铺当前订单同步至本地数据库"""
        counts_res = cls.fetch_order_state_counts(store)
        counts = counts_res.get("counts", {})

        total_synced = 0
        groups = ["shipments_to_pack", "shipments_to_handover", "shipments_in_transit", "shipments_delivered"]

        for grp in groups:
            try:
                page_num = 1
                page_size = 25
                while True:
                    data = cls.fetch_orders(store, status_group=grp, page_num=page_num, page_size=page_size)
                    items = data.get("items", [])
                    if not items:
                        break

                    for item in items:
                        order_items = item.get("order_items", [])
                        order_id = item.get("id")
                        if order_items and order_items[0].get("order_id"):
                            order_id = order_items[0].get("order_id")

                        existing = (
                            db.query(MakroOrder)
                            .filter(MakroOrder.store_id == store.id, MakroOrder.order_id == str(order_id))
                            .first()
                        )
                        if not existing:
                            existing = MakroOrder(
                                store_id=store.id,
                                seller_id=store.seller_id,
                                order_id=str(order_id)
                            )
                            db.add(existing)

                        existing.shipment_id = item.get("id")
                        existing.service_profile = item.get("service_profile", "NON_FBF")
                        existing.payment_type = item.get("payment_type", "prepaid")

                        # 送达时间解析
                        delivered_iso = item.get("delivered_date")
                        if delivered_iso:
                            try:
                                clean_dt = delivered_iso.split(".")[0]
                                existing.delivered_date = datetime.fromisoformat(clean_dt)
                            except Exception:
                                pass

                        # 状态判断与 SLA 预警归正
                        raw_item_status = order_items[0].get("status") if order_items else None
                        if grp == "shipments_delivered" or existing.delivered_date:
                            existing.status = "delivered"
                            existing.is_sla_breached = False  # 已送达完成的订单解除超时报警
                        elif grp == "shipments_in_transit":
                            existing.status = raw_item_status or "in_transit"
                            existing.is_sla_breached = bool(item.get("is_sla_breached", False))
                        elif grp == "shipments_to_pack":
                            existing.status = raw_item_status or "pending_labels"
                            existing.is_sla_breached = bool(item.get("is_sla_breached", False))
                        elif grp == "shipments_to_handover":
                            existing.status = raw_item_status or "pending_handover"
                            existing.is_sla_breached = bool(item.get("is_sla_breached", False))
                        else:
                            existing.status = raw_item_status or grp
                            existing.is_sla_breached = bool(item.get("is_sla_breached", False))

                        # 金额
                        if order_items:
                            pricing = order_items[0].get("pricing", {})
                            existing.total_amount = float(pricing.get("total_price") or 0.0)

                        # 买家
                        buyer = item.get("buyer", {})
                        existing.buyer_name = f"{buyer.get('first_name', '')} {buyer.get('last_name', '')}".strip()
                        addr = buyer.get("shipping_address", {})
                        existing.shipping_city = addr.get("city")
                        existing.shipping_state = addr.get("state")
                        existing.shipping_pincode = addr.get("pincode")
                        existing.shipping_address_line1 = addr.get("line1")
                        existing.shipping_address_full = json.dumps(addr, ensure_ascii=False)
                        existing.buyer_phone = addr.get("phone")

                        # 物流
                        tracking = item.get("tracking", {})
                        existing.tracking_id = tracking.get("tracking_id")
                        existing.courier_name = (
                            tracking.get("delivery_vendor_display_name")
                            or tracking.get("pickup_vendor_display_name")
                            or tracking.get("courier_name")
                        )
                        existing.delivery_vendor = tracking.get("delivery_vendor_display_name")
                        existing.pickup_vendor = tracking.get("pickup_vendor_display_name")

                        # 时效
                        if item.get("dispatch_by_date"):
                            try:
                                # 格式如 2026-09-21T03:29:00.000+05:30
                                clean_t = item["dispatch_by_date"].split(".")[0]
                                existing.dispatch_by_date = datetime.fromisoformat(clean_t)
                            except Exception:
                                pass

                        if order_items and order_items[0].get("order_date"):
                            try:
                                clean_ot = order_items[0]["order_date"].split(".")[0]
                                existing.order_date = datetime.fromisoformat(clean_ot)
                            except Exception:
                                pass

                        existing.raw_items_json = json.dumps(order_items, ensure_ascii=False)
                        existing.raw_payload_json = json.dumps(item, ensure_ascii=False)
                        existing.synced_at = datetime.now()
                        total_synced += 1

                    db.commit()
                    if not data.get("has_more") or len(items) < page_size:
                        break
                    page_num += 1
            except Exception as e:
                logger.warning(f"同步店铺 {store.name} 订单分组 {grp} 略过或失败: {e}")

        return {
            "success": True,
            "total_synced": total_synced,
            "state_counts": counts
        }
