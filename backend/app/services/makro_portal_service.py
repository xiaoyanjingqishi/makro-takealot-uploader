import logging
import requests
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple, Callable
from sqlalchemy.orm import Session
from ..models.store import Store, ProductStoreListing
from ..models.makro_listing import MakroListing
from ..models.makro_order import MakroOrder
from ..models.makro_order_status_log import MakroOrderStatusLog
from ..utils.date_utils import parse_to_beijing_time
from ..models.product import Product
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.setting import SystemSetting

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
        search_text: str = "",
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """查询指定状态下的商品列表与各状态总数"""
        url = f"{MAKRO_HOST}/napi/listing/listingsDataForStates"
        req_headers = headers if headers is not None else cls._build_headers(store)

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

        resp = requests.post(url, headers=req_headers, json=payload, timeout=25)
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
        location_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        调用 Makro 官方 POST /napi/listing/getInventoryByLocation 获取商品的仓库真实库存与占用库存
        items 格式: [{"listing_id": "LST...", "service_profile": "NON_FBF"}, ...]
        返回格式: {"LST...": {"quantity": 500, "reserved": 0, "loc_id": "..."}, ...}
        """
        if not items:
            return {}

        loc_id = location_id or (store.default_location_id if store else None)
        url = f"{MAKRO_HOST}/napi/listing/getInventoryByLocation"
        extra_headers = {"x-location-id": loc_id} if loc_id else {}
        if headers is not None:
            req_headers = dict(headers)
            req_headers.update(extra_headers)
        else:
            req_headers = cls._build_headers(store, extra_headers)

        result_map = {}
        chunk_size = 100
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            payload = {"fetch_inventory_requests": chunk}
            try:
                resp = requests.post(url, headers=req_headers, json=payload, timeout=25)
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

        from .proxy_service import ProxyPoolService
        from concurrent.futures import ThreadPoolExecutor, as_completed

        chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
        if not chunks:
            return {"success_count": 0, "failed_count": 0, "results": {}}

        total_success = 0
        total_failed = 0
        all_results = {}

        def _update_single_chunk(chunk):
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
            c_success = 0
            c_failed = 0
            c_results = {}
            try:
                resp = ProxyPoolService.resilient_request("POST", url, headers=headers, json=payload, timeout=35, use_proxy=False)
                if resp.status_code == 200:
                    res_data = resp.json()
                    top_status = str(res_data.get("status", "")).upper() if isinstance(res_data, dict) else ""
                    for sku in payload:
                        sku_res = res_data.get(sku, {}) if isinstance(res_data, dict) else {}
                        s_status = ""
                        s_msg = ""
                        if isinstance(sku_res, dict):
                            s_status = str(sku_res.get("status", "")).upper()
                            s_msg = sku_res.get("message") or ""
                        elif isinstance(sku_res, str):
                            s_status = sku_res.upper()

                        if s_status in ["SUCCESS", "CREATED", "UPDATED"] or (not s_status and top_status in ["SUCCESS", "OK", "200"]):
                            c_success += 1
                            c_results[sku] = {"status": "SUCCESS"}
                        else:
                            c_failed += 1
                            c_results[sku] = {
                                "status": "FAILED",
                                "detail": s_msg or f"状态: {s_status or top_status or '更新未被网关确认'}"
                            }
                else:
                    for sku in payload:
                        c_failed += 1
                        c_results[sku] = {"status": "FAILED", "detail": f"HTTP {resp.status_code}: {resp.text[:120]}"}
            except Exception as e:
                for sku in payload:
                    c_failed += 1
                    c_results[sku] = {"status": "FAILED", "detail": str(e)}
            return c_success, c_failed, c_results

        # 最多并发 5 个工作线程，大幅提升回写效率
        max_workers = min(5, len(chunks))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = [executor.submit(_update_single_chunk, c) for c in chunks]
            for fut in as_completed(future_to_chunk):
                c_succ, c_fail, c_res = fut.result()
                total_success += c_succ
                total_failed += c_fail
                all_results.update(c_res)

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
    def _save_listings_batch(
        cls,
        db: Session,
        store_id: int,
        seller_id: str,
        default_brand: str,
        listings_raw: List[Dict[str, Any]],
        inv_map: Dict[str, Any],
        st: str
    ) -> int:
        """
        批量高效入库 MakroListing 与联动 StoreProductReprice 监控底册
        采用 SQL IN 预加载与纯标量入参，杜绝跨线程 ORM 懒加载与数据库锁争用
        """
        if not listings_raw:
            return 0

        from ..models.store_product_reprice import StoreProductReprice
        from ..models.makro_piggyback import MakroPiggybackItem
        from ..models.product import Product

        sku_ids = [r.get("sku_id") for r in listings_raw if r.get("sku_id")]
        if not sku_ids:
            return 0

        # 1. 批量加载已有 MakroListing (单个 SQL IN 查询)
        existing_listings = {
            item.sku_id: item
            for item in db.query(MakroListing)
            .filter(MakroListing.store_id == store_id, MakroListing.sku_id.in_(sku_ids))
            .all()
        }

        # 2. 批量加载已有 MakroPiggybackItem (单个 SQL IN 查询)
        existing_pb = {
            pb.seller_sku: pb
            for pb in db.query(MakroPiggybackItem)
            .filter(MakroPiggybackItem.store_id == store_id, MakroPiggybackItem.seller_sku.in_(sku_ids))
            .all()
        }

        # 3. 批量关联本地选品商品 (单个 SQL IN 查询)
        matched_products = {
            p.sku_id: p.id
            for p in db.query(Product.sku_id, Product.id)
            .filter(Product.sku_id.in_(sku_ids))
            .all()
        }

        saved_count = 0
        for raw in listings_raw:
            sku_id = raw.get("sku_id")
            if not sku_id:
                continue

            existing = existing_listings.get(sku_id)
            if not existing:
                existing = MakroListing(
                    store_id=store_id,
                    seller_id=seller_id,
                    sku_id=sku_id
                )
                db.add(existing)
                existing_listings[sku_id] = existing

            existing.product_id = raw.get("product_id")
            existing.listing_id = raw.get("listing_id")
            existing.title = raw.get("title") or raw.get("product_id")
            existing.brand = raw.get("brand") or default_brand
            existing.vertical = raw.get("vertical")
            existing.vertical_display_name = raw.get("vertical_display_name")
            existing.image_url = raw.get("imageUrl")
            existing.internal_state = raw.get("internal_state") or st
            existing.ssp = float(raw.get("ssp") or 0.0)
            existing.mrp = float(raw.get("mrp") or 0.0)

            # 官方真实库存绑定与安全继承保护
            lst_id = raw.get("listing_id")
            inv_info = inv_map.get(lst_id) if lst_id else None
            pb_item = existing_pb.get(sku_id)

            if inv_info and "quantity" in inv_info:
                existing.inventory = int(inv_info["quantity"])
            elif raw.get("inventory") is not None:
                existing.inventory = int(raw.get("inventory"))
            else:
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

            # 关联本地选品商品
            if sku_id in matched_products:
                existing.local_product_id = matched_products[sku_id]

            existing.synced_at = datetime.now()
            saved_count += 1

        # 先 flush 保证新加入的 MakroListing 获得自增 id
        db.flush()

        # 4. 批量同步 StoreProductReprice 监控底册
        s_strat = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_default_strategy").first()
        def_strategy = s_strat.value.strip() if s_strat and s_strat.value else "MINUS_1"

        listing_ids = [existing_listings[s].id for s in sku_ids if s in existing_listings and existing_listings[s].id]
        existing_reps = {
            r.listing_id: r
            for r in db.query(StoreProductReprice)
            .filter(StoreProductReprice.listing_id.in_(listing_ids))
            .all()
        }

        for raw in listings_raw:
            sku_id = raw.get("sku_id")
            if not sku_id or sku_id not in existing_listings:
                continue
            lst = existing_listings[sku_id]
            rep = existing_reps.get(lst.id)
            curr_st = lst.internal_state
            pb = existing_pb.get(sku_id)

            if rep:
                if curr_st == "ACTIVE":
                    if rep.monitor_status == "OFFLINE_SUSPENDED":
                        rep.monitor_status = "ACTIVE"
                else:
                    db.delete(rep)
            else:
                if curr_st == "ACTIVE":
                    src_t = "PIGGYBACK" if pb else ("SELF_PUBLISHED" if lst.local_product_id else "STORE_SYNCED")
                    m_st = "ACTIVE" if pb else "PENDING_CONFIG"
                    new_rep = StoreProductReprice(
                        store_id=store_id,
                        listing_id=lst.id,
                        seller_sku=lst.sku_id,
                        makro_product_id=lst.product_id or "",
                        source_type=src_t,
                        source_piggyback_id=pb.id if pb else None,
                        target_fsn=pb.makro_product_id if pb else lst.product_id,
                        target_makro_url=pb.makro_url if pb else None,
                        auto_reprice=bool(pb.auto_reprice) if pb else False,
                        price_strategy=pb.price_strategy if pb else def_strategy,
                        min_price_floor=float(pb.min_price_floor or 0.0) if pb else 0.0,
                        max_price_ceiling=float(pb.max_price_ceiling or 0.0) if pb else 0.0,
                        compliance_status=pb.compliance_status if (pb and pb.compliance_status) else "PENDING_CHECK",
                        compliance_details=pb.compliance_details if (pb and pb.compliance_details) else None,
                        monitor_status=m_st
                    )
                    db.add(new_rep)

        db.commit()
        return saved_count

    @classmethod
    def sync_store_listings(
        cls,
        store: Store,
        db: Session,
        concurrency: int = 8,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled_fn: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """
        全量同步 Makro 店铺商品至本地数据库 (单页100件 + 动态算页 + 多线程并发版)
        - 单页 100 件 (batch_size=100)
        - 探测第 0 页获取 count 并动态计算总页数 total_pages = ceil(count / 100)
        - ThreadPoolExecutor 多线程并发抓取剩余分页与真实库存
        - 流式批处理入库，避免 SQLite 锁争用
        """
        import math
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 提取标量配置与预编译请求头，避免跨线程触碰 ORM 实例
        store_id = store.id
        store_name = store.name
        seller_id = store.seller_id
        default_brand = store.default_brand
        loc_id = store.default_location_id
        portal_headers = cls._build_headers(store)

        states_to_sync = ["ACTIVE", "READY_FOR_ACTIVATION", "INACTIVE", "INACTIVATED_BY_FLIPKART", "ARCHIVED"]
        PAGE_SIZE = 100
        total_synced = 0
        state_counts = {}
        pending_page_tasks = []

        # 阶段 1: 并发探测各状态的第 0 页 (batch_size=100)
        p0_results = {}
        def _fetch_probe_p0(st: str):
            try:
                data = cls.fetch_listings_data_for_states(
                    store=store,
                    internal_state=st,
                    page_no=0,
                    page_size=PAGE_SIZE,
                    headers=portal_headers
                )
                cnt = data.get("count", 0)
                raw_items = data.get("listing_data_response", [])
                inv_map = {}
                if raw_items:
                    inv_requests = [
                        {"listing_id": r["listing_id"], "service_profile": r.get("service_profile") or "NON_FBF"}
                        for r in raw_items if r.get("listing_id")
                    ]
                    if inv_requests:
                        inv_map = cls.fetch_inventory_by_location(
                            store=store,
                            items=inv_requests,
                            location_id=loc_id,
                            headers=portal_headers
                        )
                return st, cnt, raw_items, inv_map
            except Exception as e:
                logger.warning(f"探测店铺 [{store_name}] 状态 {st} 第 0 页异常: {e}")
                return st, 0, [], {}

        with ThreadPoolExecutor(max_workers=min(5, len(states_to_sync))) as probe_exec:
            probe_futs = [probe_exec.submit(_fetch_probe_p0, s) for s in states_to_sync]
            for fut in as_completed(probe_futs):
                st, cnt, items, inv_map = fut.result()
                state_counts[st] = cnt
                if items:
                    p0_results[st] = (items, inv_map)
                if cnt > PAGE_SIZE:
                    total_pages = math.ceil(cnt / PAGE_SIZE)
                    for p_no in range(1, total_pages):
                        pending_page_tasks.append((st, p_no))

        grand_total = sum(state_counts.values())
        if progress_callback:
            progress_callback(0, grand_total, f"已探明全店共 {grand_total} 件商品，正在入库第 0 页...")

        # 立即入库各状态第 0 页商品 (使用标量参数)
        for st, (items, inv_map) in p0_results.items():
            if is_cancelled_fn and is_cancelled_fn():
                break
            saved = cls._save_listings_batch(db, store_id, seller_id, default_brand, items, inv_map, st)
            total_synced += saved
            if progress_callback:
                progress_callback(total_synced, grand_total, f"已入库 [{st}] 第 1 页 ({len(items)} 件)")

        # 阶段 2: 多线程并发拉取剩余所有分页
        if pending_page_tasks and not (is_cancelled_fn and is_cancelled_fn()):
            def _fetch_single_page(st_name: str, p_idx: int):
                for attempt in range(3):
                    if is_cancelled_fn and is_cancelled_fn():
                        return None
                    try:
                        p_data = cls.fetch_listings_data_for_states(
                            store=store,
                            internal_state=st_name,
                            page_no=p_idx,
                            page_size=PAGE_SIZE,
                            headers=portal_headers
                        )
                        raw = p_data.get("listing_data_response", [])
                        inv_map = {}
                        if raw:
                            inv_reqs = [
                                {"listing_id": r["listing_id"], "service_profile": r.get("service_profile") or "NON_FBF"}
                                for r in raw if r.get("listing_id")
                            ]
                            if inv_reqs:
                                inv_map = cls.fetch_inventory_by_location(
                                    store=store,
                                    items=inv_reqs,
                                    location_id=loc_id,
                                    headers=portal_headers
                                )
                        return {"state": st_name, "page_no": p_idx, "items": raw, "inv_map": inv_map}
                    except Exception as ex:
                        if attempt < 2:
                            time.sleep(1.0 * (attempt + 1))
                        else:
                            logger.error(f"抓取店铺 [{store_name}] 状态 {st_name} 第 {p_idx} 页失败: {ex}")
                            return {"state": st_name, "page_no": p_idx, "items": [], "inv_map": {}, "error": str(ex)}

            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as page_exec:
                page_futs = [page_exec.submit(_fetch_single_page, s, p) for s, p in pending_page_tasks]
                for fut in as_completed(page_futs):
                    if is_cancelled_fn and is_cancelled_fn():
                        break
                    res = fut.result()
                    if not res or not res.get("items"):
                        continue
                    saved = cls._save_listings_batch(db, store_id, seller_id, default_brand, res["items"], res["inv_map"], res["state"])
                    total_synced += saved
                    if progress_callback:
                        progress_callback(total_synced, grand_total, f"正在同步 [{res['state']}] 第 {res['page_no']+1} 页 ({total_synced}/{grand_total})")

        return {
            "success": True,
            "total_synced": total_synced,
            "state_counts": state_counts
        }

    @classmethod
    def sync_store_orders(
        cls,
        store: Store,
        db: Session,
        progress_callback: Optional[Any] = None,
        is_cancelled_fn: Optional[Any] = None
    ) -> Dict[str, Any]:
        """将 Makro 店铺当前订单同步至本地数据库"""
        counts_res = cls.fetch_order_state_counts(store)
        counts = counts_res.get("counts", {})

        total_synced = 0
        groups = ["shipments_to_pack", "shipments_to_handover", "shipments_in_transit", "shipments_delivered"]

        for grp in groups:
            if is_cancelled_fn and is_cancelled_fn():
                break
            try:
                page_num = 1
                page_size = 25
                while True:
                    if is_cancelled_fn and is_cancelled_fn():
                        break
                    data = cls.fetch_orders(store, status_group=grp, page_num=page_num, page_size=page_size)
                    items = data.get("items", [])
                    if not items:
                        break

                    for item in items:
                        # 拦截官方接口未装配完成/异常空壳运单 (enrichment_error 或无任何 order_items 实体)
                        if item.get("enrichment_error") or not item.get("order_items"):
                            logger.warning(f"跳过 Makro 官方异常空壳运单 (ID: {item.get('id')}, enrichment_error={item.get('enrichment_error')})，不落库占位")
                            continue

                        order_items = item.get("order_items", [])
                        order_id = item.get("id")
                        if order_items and order_items[0].get("order_id"):
                            order_id = order_items[0].get("order_id")

                        is_new_order = False
                        existing = (
                            db.query(MakroOrder)
                            .filter(MakroOrder.store_id == store.id, MakroOrder.order_id == str(order_id))
                            .first()
                        )
                        if not existing:
                            is_new_order = True
                            existing = MakroOrder(
                                store_id=store.id,
                                seller_id=store.seller_id,
                                order_id=str(order_id),
                                fulfillment_status="NEW",
                                fulfillment_updated_at=datetime.now()
                            )
                            db.add(existing)

                        old_status = existing.status

                        existing.shipment_id = item.get("id")
                        existing.service_profile = item.get("service_profile", "NON_FBF")
                        existing.payment_type = item.get("payment_type", "prepaid")

                        # 送达时间解析 (规整转为北京时间)
                        delivered_iso = item.get("delivered_date")
                        if delivered_iso:
                            existing.delivered_date = parse_to_beijing_time(delivered_iso)

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

                        # 金额 (多品合单必须累加求和全部 order_items，避免仅计入首品)
                        if order_items:
                            existing.total_amount = round(sum(
                                float((oi.get("pricing") or {}).get("total_price") or oi.get("price") or 0.0)
                                for oi in order_items
                            ), 2)

                        # 官方是否已打单同步
                        if item.get("is_label_printed") or item.get("is_mps"):
                            existing.label_printed = True

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

                        # 时效 (将印度时区 +05:30 准确规整计算为标准北京时间入库)
                        if item.get("dispatch_by_date"):
                            existing.dispatch_by_date = parse_to_beijing_time(item["dispatch_by_date"])

                        if order_items and order_items[0].get("order_date"):
                            existing.order_date = parse_to_beijing_time(order_items[0]["order_date"])

                        existing.raw_items_json = json.dumps(order_items, ensure_ascii=False)
                        existing.raw_payload_json = json.dumps(item, ensure_ascii=False)
                        existing.synced_at = datetime.now()

                        # 状态流转审计日志记录
                        if is_new_order:
                            db.flush()
                            db.add(MakroOrderStatusLog(
                                order_id=existing.id,
                                order_sn=str(order_id),
                                change_type="INITIAL_SYNC",
                                from_status=None,
                                to_status=existing.fulfillment_status or "NEW",
                                operator="Makro 同步",
                                note=f"官方订单首次抓取同步入库 (官方状态: {existing.status})"
                            ))
                        elif old_status and old_status != existing.status:
                            db.add(MakroOrderStatusLog(
                                order_id=existing.id,
                                order_sn=str(order_id),
                                change_type="OFFICIAL_STATUS",
                                from_status=old_status,
                                to_status=existing.status,
                                operator="Makro 同步",
                                note=f"官方订单状态变更为: {existing.status}"
                            ))

                        total_synced += 1

                    db.commit()
                    if progress_callback:
                        progress_callback(total_synced, 0, f"正在同步 [{grp}] 第 {page_num} 页订单 (已同步 {total_synced} 条)")
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

    # =========================================================================
    # 店铺商品官方原生批量上下架与原因管控 (Activation & Deactivation)
    # =========================================================================
    @staticmethod
    def _build_listing_bulk_request_for_listing(listing: MakroListing, store: Store, listing_status: str = "ACTIVE") -> Dict[str, Any]:
        """为 MakroListing 构建提交给官方 create-update-listings 接口的标准 payload 项"""
        ssp_f = float(listing.ssp or 199.0)
        mrp_f = float(listing.mrp or 0.0)
        if mrp_f < ssp_f:
            mrp_f = round(max(ssp_f * 1.5, ssp_f + 30.0), 2)
        safe_mrp_f = max(mrp_f, round(ssp_f * 1.5, 2), ssp_f + 10.0)
        ssp_val = str(int(ssp_f)) if ssp_f.is_integer() else str(round(ssp_f, 2))
        mrp_val = str(int(safe_mrp_f)) if safe_mrp_f.is_integer() else str(round(safe_mrp_f, 2))
        pkg_len = str(int(listing.length)) if listing.length and float(listing.length).is_integer() else str(listing.length or 15)
        pkg_brd = str(int(listing.breadth)) if listing.breadth and float(listing.breadth).is_integer() else str(listing.breadth or 10)
        pkg_hgt = str(int(listing.height)) if listing.height and float(listing.height).is_integer() else str(listing.height or 5)
        pkg_wgt = str(listing.weight or 0.5)

        return {
            "attributeValues": {
                "sku_id": [{"value": listing.sku_id, "qualifier": ""}],
                "listing_status": [{"value": listing_status, "qualifier": ""}],
                "mrp": [{"value": mrp_val, "qualifier": "INR"}],
                "flipkart_selling_price": [{"value": ssp_val, "qualifier": "INR"}],
                "service_profile": [{"value": "NON_FBF", "qualifier": ""}],
                "shipping_days": [{"value": "14", "qualifier": "DAY"}],
                "forbid_shipping": [{"qualifier": "", "value": "none"}],
                "country_of_origin": [{"value": "CN", "qualifier": ""}],
                "manufacturer_details": [{"value": "General", "qualifier": ""}],
                "packer_details": [{"value": store.default_brand or "Generic", "qualifier": ""}]
            },
            "context": {
                "ignore_warnings": False
            },
            "productId": listing.product_id or "",
            "skuId": listing.sku_id,
            "packages": [
                {
                    "id": {"value": "packages-0"},
                    "length": {"value": pkg_len, "qualifier": "CM"},
                    "breadth": {"value": pkg_brd, "qualifier": "CM"},
                    "height": {"value": pkg_hgt, "qualifier": "CM"},
                    "weight": {"value": pkg_wgt, "qualifier": "KG"},
                    "sku_id": {"value": listing.sku_id, "qualifier": ""}
                }
            ]
        }

    @classmethod
    def deactivate_store_listings(
        cls,
        store: Store,
        sku_ids: List[str],
        reason_category: str = "MANUAL_OFFLINE",
        note: Optional[str] = None,
        db: Session = None,
        location_id: Optional[str] = None,
        chunk_size: int = 20
    ) -> Dict[str, Any]:
        """
        批量下架店铺商品：
        1. 官方 create-update-listings: listing_status='INACTIVE'
        2. 官方 updateListingsInventory: 0 清零库存
        3. 本地 MakroListing 记录状态、下架原因类别、备注与时间
        4. 联动 StoreProductReprice 置为 OFFLINE_SUSPENDED
        """
        if not sku_ids:
            return {"success": True, "success_count": 0, "failed_count": 0, "message": "未指定 SKU"}

        if not store.seller_id or not store.fk_csrf_token or not store.cookie:
            raise ValueError(f"店铺「{store.name}」凭据未配置完整，无法下架商品。")

        from .proxy_service import ProxyPoolService
        from ..models.store_product_reprice import StoreProductReprice

        headers = cls._build_headers(store)
        create_url = f"{MAKRO_HOST}/napi/listing/create-update-listings?sellerId={store.seller_id}"
        target_loc = location_id or store.default_location_id

        listings = db.query(MakroListing).filter(
            MakroListing.store_id == store.id,
            MakroListing.sku_id.in_(sku_ids)
        ).all()
        listing_map = {l.sku_id: l for l in listings}

        success_skus = []
        failed_skus = []
        errors = []

        from concurrent.futures import ThreadPoolExecutor, as_completed

        chunks = [sku_ids[i:i + chunk_size] for i in range(0, len(sku_ids), chunk_size)]

        def _process_deactivate_chunk(chunk_skus):
            chunk_listings = [listing_map[s] for s in chunk_skus if s in listing_map]
            if not chunk_listings:
                return [], [], []
            bulk_requests = [
                cls._build_listing_bulk_request_for_listing(l, store, listing_status="INACTIVE")
                for l in chunk_listings
            ]
            payload = {
                "bulkRequests": bulk_requests,
                "sellerId": store.seller_id
            }
            c_success = []
            c_failed = []
            c_errors = []
            try:
                resp = ProxyPoolService.resilient_request("POST", create_url, headers=headers, json=payload, timeout=35, use_proxy=False)
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                res_json = resp.json()
                bulk_res = res_json.get("result", {}).get("bulkResponse", [])
                res_by_sku = {r.get("skuID") or r.get("skuId"): r for r in bulk_res if r.get("skuID") or r.get("skuId")}

                for l in chunk_listings:
                    r = res_by_sku.get(l.sku_id, {})
                    st = r.get("status")
                    g_errs = r.get("globalErrors", [])
                    a_errs = r.get("attributeErrors", {})
                    if st in ["created", "updated", "success"] or (st and not g_errs and not a_errs):
                        c_success.append(l.sku_id)
                    else:
                        err_msg = "; ".join(g_errs) if g_errs else str(a_errs)
                        c_failed.append(l.sku_id)
                        c_errors.append(f"{l.sku_id}: {err_msg}")
            except Exception as req_err:
                logger.error(f"批量下架调用异常: {req_err}")
                for l in chunk_listings:
                    c_failed.append(l.sku_id)
                    c_errors.append(f"{l.sku_id}: {str(req_err)}")
            return c_success, c_failed, c_errors

        max_workers = min(5, len(chunks)) if chunks else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = [executor.submit(_process_deactivate_chunk, c) for c in chunks]
            for fut in as_completed(future_to_chunk):
                c_succ, c_fail, c_errs = fut.result()
                success_skus.extend(c_succ)
                failed_skus.extend(c_fail)
                errors.extend(c_errs)

        # 针对下架成功的 SKU，批量清零官方库存
        if success_skus and target_loc:
            try:
                inv_items = [{"sku_id": s, "product_id": listing_map[s].product_id or "", "inventory": 0} for s in success_skus if s in listing_map]
                cls.batch_update_inventory(store=store, items=inv_items, location_id=target_loc)
            except Exception as inv_err:
                logger.warning(f"下架库存清零异常: {inv_err}")

        # 本地数据库批量更新
        if success_skus:
            now = datetime.now()
            for s in success_skus:
                l = listing_map.get(s)
                if l:
                    l.internal_state = "INACTIVE"
                    l.inventory = 0
                    l.deactivation_reason_category = reason_category
                    l.deactivation_note = note
                    l.deactivated_at = now

            # 联动跟价监控：已下架商品直接物理硬删除，移出监控底册
            db.query(StoreProductReprice).filter(
                StoreProductReprice.store_id == store.id,
                StoreProductReprice.seller_sku.in_(success_skus)
            ).delete(synchronize_session=False)

            db.commit()

        return {
            "success": len(failed_skus) == 0,
            "total": len(sku_ids),
            "success_count": len(success_skus),
            "failed_count": len(failed_skus),
            "errors": errors
        }

    @classmethod
    def activate_store_listings(
        cls,
        store: Store,
        sku_ids: List[str],
        recovery_inventory: int = 500,
        db: Session = None,
        location_id: Optional[str] = None,
        chunk_size: int = 20
    ) -> Dict[str, Any]:
        """
        批量上架激活店铺商品：
        1. 官方 create-update-listings: listing_status='ACTIVE'
        2. 官方 updateListingsInventory: 恢复指定库存
        3. 本地 MakroListing 记录状态、清除下架原因
        4. 联动 StoreProductReprice 唤醒为 ACTIVE (非 PROHIBITED)
        """
        if not sku_ids:
            return {"success": True, "success_count": 0, "failed_count": 0, "message": "未指定 SKU"}

        if not store.seller_id or not store.fk_csrf_token or not store.cookie:
            raise ValueError(f"店铺「{store.name}」凭据未配置完整，无法上架商品。")

        from .proxy_service import ProxyPoolService
        from ..models.store_product_reprice import StoreProductReprice

        headers = cls._build_headers(store)
        create_url = f"{MAKRO_HOST}/napi/listing/create-update-listings?sellerId={store.seller_id}"
        target_loc = location_id or store.default_location_id

        listings = db.query(MakroListing).filter(
            MakroListing.store_id == store.id,
            MakroListing.sku_id.in_(sku_ids)
        ).all()
        listing_map = {l.sku_id: l for l in listings}

        success_skus = []
        failed_skus = []
        errors = []

        from concurrent.futures import ThreadPoolExecutor, as_completed

        chunks = [sku_ids[i:i + chunk_size] for i in range(0, len(sku_ids), chunk_size)]

        def _process_activate_chunk(chunk_skus):
            chunk_listings = [listing_map[s] for s in chunk_skus if s in listing_map]
            if not chunk_listings:
                return [], [], []
            bulk_requests = [
                cls._build_listing_bulk_request_for_listing(l, store, listing_status="ACTIVE")
                for l in chunk_listings
            ]
            payload = {
                "bulkRequests": bulk_requests,
                "sellerId": store.seller_id
            }
            c_success = []
            c_failed = []
            c_errors = []
            try:
                resp = ProxyPoolService.resilient_request("POST", create_url, headers=headers, json=payload, timeout=35, use_proxy=False)
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                res_json = resp.json()
                bulk_res = res_json.get("result", {}).get("bulkResponse", [])
                res_by_sku = {r.get("skuID") or r.get("skuId"): r for r in bulk_res if r.get("skuID") or r.get("skuId")}

                for l in chunk_listings:
                    r = res_by_sku.get(l.sku_id, {})
                    st = r.get("status")
                    g_errs = r.get("globalErrors", [])
                    a_errs = r.get("attributeErrors", {})
                    if st in ["created", "updated", "success"] or (st and not g_errs and not a_errs):
                        c_success.append(l.sku_id)
                    else:
                        err_msg = "; ".join(g_errs) if g_errs else str(a_errs)
                        c_failed.append(l.sku_id)
                        c_errors.append(f"{l.sku_id}: {err_msg}")
            except Exception as req_err:
                logger.error(f"批量上架调用异常: {req_err}")
                for l in chunk_listings:
                    c_failed.append(l.sku_id)
                    c_errors.append(f"{l.sku_id}: {str(req_err)}")
            return c_success, c_failed, c_errors

        max_workers = min(5, len(chunks)) if chunks else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = [executor.submit(_process_activate_chunk, c) for c in chunks]
            for fut in as_completed(future_to_chunk):
                c_succ, c_fail, c_errs = fut.result()
                success_skus.extend(c_succ)
                failed_skus.extend(c_fail)
                errors.extend(c_errs)

        # 针对上架成功的 SKU，批量回写库存
        if success_skus and target_loc:
            try:
                inv_items = [{"sku_id": s, "product_id": listing_map[s].product_id or "", "inventory": recovery_inventory} for s in success_skus if s in listing_map]
                cls.batch_update_inventory(store=store, items=inv_items, location_id=target_loc)
            except Exception as inv_err:
                logger.warning(f"上架库存恢复异常: {inv_err}")

        # 本地数据库批量更新
        if success_skus:
            for s in success_skus:
                l = listing_map.get(s)
                if l:
                    l.internal_state = "ACTIVE"
                    l.inventory = recovery_inventory
                    l.deactivation_reason_category = None
                    l.deactivation_note = None
                    l.deactivated_at = None

            # 联动唤醒监控状态为 ACTIVE (排除合规禁售 PROHIBITED)
            db.query(StoreProductReprice).filter(
                StoreProductReprice.store_id == store.id,
                StoreProductReprice.seller_sku.in_(success_skus),
                StoreProductReprice.compliance_status != "PROHIBITED"
            ).update({StoreProductReprice.monitor_status: "ACTIVE"}, synchronize_session=False)

            db.commit()

        return {
            "success": len(failed_skus) == 0,
            "total": len(sku_ids),
            "success_count": len(success_skus),
            "failed_count": len(failed_skus),
            "errors": errors
        }
