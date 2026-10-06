import json
import logging
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from ..models.makro_piggyback import MakroPiggybackItem
from ..models.store import Store
from ..models.makro_listing import MakroListing
from ..models.task import TaskLog
from ..services.compliance_service import ComplianceService
from ..services.audit_logger import record_audit_log

logger = logging.getLogger(__name__)

MAKRO_HOST = "https://seller.makro.co.za"

class MakroPiggybackService:
    """
    Makro 智能跟品挂靠与合规质检执行引擎
    完全基于真实抓包协议 (makro跟品协议.har) 落地全流程挂靠发布与库存即时激活
    """

    @classmethod
    def eval_price_by_strategy(
        cls,
        base_price: float,
        strategy: str = "MINUS_1",
        custom_delta: Optional[float] = None,
        min_floor: float = 0.0
    ) -> float:
        """
        统一跟价公式计算引擎：
        支持:
          - MINUS_X (如 MINUS_1 -> -1.0, MINUS_0.5 / MINUS_0_5 -> -0.5, MINUS_2 -> -2.0)
          - PERCENT_X (如 PERCENT_2 -> -2%, PERCENT_5 -> -5%, PERCENT_1 -> -1%)
          - CUSTOM (配合 custom_delta 浮点，或 strategy="CUSTOM:-1.5")
          - MANUAL / MATCH_PRICE (平价零差额跟卖)
        """
        price = float(base_price or 0.0)
        floor = float(min_floor or 0.0)
        if price <= 0:
            return floor if floor > 0 else 1.0

        st = (strategy or "MINUS_1").strip().upper()

        if st.startswith("MINUS_"):
            try:
                num_str = st[6:].replace("_", ".")
                val = float(num_str)
                calc_p = max(price - val, 1.0)
            except Exception:
                calc_p = max(price - 1.0, 1.0)
        elif st.startswith("PERCENT_"):
            try:
                num_str = st[8:].replace("_", ".")
                pct = float(num_str)
                calc_p = max(round(price * (1.0 - pct / 100.0), 2), 1.0)
            except Exception:
                calc_p = max(round(price * 0.98, 2), 1.0)
        elif st.startswith("CUSTOM:") or st.startswith("OFFSET:"):
            try:
                val = float(st.split(":")[1])
                calc_p = max(round(price + val, 2), 1.0)
            except Exception:
                calc_p = price
        elif st == "CUSTOM" and custom_delta is not None:
            calc_p = max(round(price + custom_delta, 2), 1.0)
        elif st in ["MANUAL", "MATCH_PRICE", "SAME", "0"]:
            calc_p = price
        else:
            calc_p = max(price - 1.0, 1.0)

        if floor > 0:
            calc_p = max(calc_p, floor)

        return round(calc_p, 2)

    @classmethod
    def calculate_price(
        cls,
        original_price: float,
        strategy: str = "MINUS_1",
        min_floor: float = 0.0,
        original_mrp: float = 0.0,
        custom_delta: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        计算跟品建议售价与 MRP
        """
        price = float(original_price or 0.0)
        floor = float(min_floor or 0.0)

        if price <= 0:
            target_p = floor if floor > 0 else 199.0
        else:
            target_p = cls.eval_price_by_strategy(
                base_price=price,
                strategy=strategy,
                custom_delta=custom_delta,
                min_floor=floor
            )

        # MRP 设定: 保持原 MRP 或略高 30%~50%
        if original_mrp and original_mrp > target_p:
            target_mrp = original_mrp
        else:
            target_mrp = round(target_p * 1.5, 2)

        return round(target_p, 2), round(target_mrp, 2)

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
    def check_compliance_for_item(
        cls,
        item: MakroPiggybackItem,
        db: Session
    ) -> Dict[str, Any]:
        """
        对指定跟品商品调用多模型双轮交叉质检 (第1轮文本大牌红线与白牌豁免 + 第2轮主图视觉多模态图审)
        """
        compliance_service = ComplianceService.from_db(db)

        # 整理待检数据 (全面对齐 ComplianceService 输入要素)
        prod_dict = {
            "id": item.id,
            "title": item.title,
            "makro_title": item.title,
            "brand": item.brand or "Generic",
            "makro_brand": item.brand or "Generic",
            "category": item.vertical or "general",
            "images": [item.image_url] if item.image_url else [],
            "raw_images": [item.image_url] if item.image_url else [],
            "is_piggyback": True,
            "original_seller": item.original_seller or ""
        }

        try:
            res = compliance_service.check_product(
                product_data=prod_dict,
                check_image=bool(item.image_url),
                check_ai_title=True
            )
            status = res.get("compliance_status", "SAFE")
            item.compliance_status = status
            item.compliance_details = json.dumps(res, ensure_ascii=False)
            db.commit()
            db.refresh(item)
            return res
        except Exception as e:
            logger.error(f"跟品商品 (ID: {item.id}) AI 合规检测异常: {e}", exc_info=True)
            item.compliance_status = "RISK"
            err_res = {
                "compliance_status": "RISK",
                "summary": f"合规检测接口异常，建议人工核验: {str(e)}",
                "risk_keywords": [],
                "prohibited_items": [],
                "reconciliation_summary": f"AI 检测调用异常: {str(e)}"
            }
            item.compliance_details = json.dumps(err_res, ensure_ascii=False)
            db.commit()
            return err_res

    @classmethod
    def publish_piggyback_listing(
        cls,
        item: MakroPiggybackItem,
        store: Store,
        db: Session
    ) -> Dict[str, Any]:
        """
        执行真正的 Makro 官方挂靠跟品刊登 (依照 makro跟品协议.har 实测协议)
        """
        # ★★★ 1. 强力合规风控闸口 ★★★
        if item.compliance_status == "PROHIBITED":
            raise ValueError(f"【合规红线拦截】商品「{item.title[:40]}...」已被 AI 判定为红线侵权或平台违禁品，系统彻底禁止挂靠跟品！")

        if not store.seller_id or not store.fk_csrf_token or not store.cookie:
            raise ValueError(f"店铺「{store.name}」凭据未配置完整 (缺少 seller_id/fk_csrf_token/cookie)，请先同步登录态。")

        target_loc = getattr(item, "location_id", None) or store.default_location_id
        if not target_loc:
            raise ValueError(f"店铺「{store.name}」未配置仓库 Location ID，无法设置库存与物流。")

        headers = cls._build_headers(store)

        # 2. 资格与品牌核验 (预警非致命)
        try:
            if item.vertical:
                elig_url = f"{MAKRO_HOST}/napi/listing/sellerEligible?vertical={item.vertical}&sellerId={store.seller_id}"
                resp_elig = requests.get(elig_url, headers=headers, timeout=15)
                if resp_elig.status_code == 200:
                    elig_data = resp_elig.json()
                    if elig_data.get("eligibility") is False:
                        logger.warning(f"店铺 {store.name} 对类目 {item.vertical} 资格返回为 false")

            if item.brand and item.vertical:
                appr_url = f"{MAKRO_HOST}/napi/regulation/approvalStatus?vertical={item.vertical}&brand={item.brand}&sellerId={store.seller_id}"
                resp_appr = requests.get(appr_url, headers=headers, timeout=15)
                if resp_appr.status_code == 200:
                    appr_data = resp_appr.json()
                    if appr_data.get("approvalStatus") not in ["APPROVED", "AUTO_APPROVED"]:
                        logger.warning(f"品牌 {item.brand} 审批状态为: {appr_data.get('approvalStatus')}")
        except Exception as pre_err:
            logger.warning(f"跟品资格前置校验跳过或异常: {pre_err}")

        # 3. 组装挂靠核心载荷 (完全对齐 makro跟品协议.har Entry 7)
        create_url = f"{MAKRO_HOST}/napi/listing/create-update-listings?sellerId={store.seller_id}"

        # 价格取整数或合法浮点，依据抓包货币为 INR
        ssp_val = str(int(item.target_price)) if item.target_price.is_integer() else str(item.target_price)
        mrp_val = str(int(item.target_mrp)) if item.target_mrp.is_integer() else str(item.target_mrp)
        lead_time = str(item.lead_time_days or 14)
        pkg_len = str(int(item.length)) if item.length and item.length.is_integer() else str(item.length or 15)
        pkg_brd = str(int(item.breadth)) if item.breadth and item.breadth.is_integer() else str(item.breadth or 10)
        pkg_hgt = str(int(item.height)) if item.height and item.height.is_integer() else str(item.height or 5)
        pkg_wgt = str(item.weight or 0.2)

        payload = {
            "bulkRequests": [
                {
                    "attributeValues": {
                        "sku_id": [{"value": item.seller_sku, "qualifier": ""}],
                        "listing_status": [{"value": "ACTIVE", "qualifier": ""}],
                        "mrp": [{"value": mrp_val, "qualifier": "INR"}],
                        "flipkart_selling_price": [{"value": ssp_val, "qualifier": "INR"}],
                        "service_profile": [{"value": "NON_FBF", "qualifier": ""}],
                        "shipping_days": [{"value": lead_time, "qualifier": "DAY"}],
                        "forbid_shipping": [{"qualifier": "", "value": "none"}],
                        "country_of_origin": [{"value": "CN", "qualifier": ""}],
                        "manufacturer_details": [{"value": "General", "qualifier": ""}],
                        "packer_details": [{"value": store.default_brand or "Generic", "qualifier": ""}]
                    },
                    "context": {
                        "ignore_warnings": False
                    },
                    "productId": item.makro_product_id,
                    "skuId": item.seller_sku,
                    "packages": [
                        {
                            "id": {"value": "packages-0"},
                            "length": {"value": pkg_len, "qualifier": "CM"},
                            "breadth": {"value": pkg_brd, "qualifier": "CM"},
                            "height": {"value": pkg_hgt, "qualifier": "CM"},
                            "weight": {"value": pkg_wgt, "qualifier": "KG"},
                            "sku_id": {"value": item.seller_sku, "qualifier": ""}
                        }
                    ]
                }
            ],
            "sellerId": store.seller_id
        }

        item.status = "SUBMITTING"
        db.commit()

        try:
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"Makro create-update-listings 挂靠接口响应 HTTP {resp.status_code}: {resp.text[:300]}")

            res_json = resp.json()
            bulk_res = res_json.get("result", {}).get("bulkResponse", [])
            if not bulk_res:
                raise Exception(f"Makro 接口未返回有效的 bulkResponse: {res_json}")

            single_res = bulk_res[0]
            lst_status = single_res.get("status")
            listing_id = single_res.get("listingID")
            global_errs = single_res.get("globalErrors", [])
            attr_errs = single_res.get("attributeErrors", {})

            if lst_status not in ["created", "updated", "success"] and not listing_id:
                err_msg = "; ".join(global_errs) if global_errs else str(attr_errs)
                raise Exception(f"挂靠未成功 (状态: {lst_status}): {err_msg}")

            # 4. 立即激活库存回写 (updateListingsInventory)
            inv_url = f"{MAKRO_HOST}/napi/sfx/updateListingsInventory?sellerId={store.seller_id}&locationId={target_loc}"
            inv_headers = cls._build_headers(store, {"x-location-id": target_loc})
            inv_payload = {
                item.seller_sku: {
                    "product_id": item.makro_product_id,
                    "locations": [
                        {
                            "id": target_loc,
                            "inventory": int(item.inventory or 99)
                        }
                    ]
                }
            }
            try:
                requests.post(inv_url, headers=inv_headers, json=inv_payload, timeout=20)
            except Exception as inv_e:
                logger.warning(f"跟品后即时库存回写发生异常 (可由后台定时器补齐): {inv_e}")

            # 5. 更新本地跟品记录
            item.status = "ACTIVE"
            item.makro_listing_id = listing_id
            item.error_message = None
            db.commit()

            # 6. 联动同步写入 makro_listings 统一管理表
            try:
                existing_listing = db.query(MakroListing).filter(
                    MakroListing.store_id == store.id,
                    MakroListing.sku_id == item.seller_sku
                ).first()
                if not existing_listing:
                    existing_listing = MakroListing(
                        store_id=store.id,
                        seller_id=store.seller_id,
                        sku_id=item.seller_sku
                    )
                    db.add(existing_listing)

                existing_listing.product_id = item.makro_product_id
                existing_listing.listing_id = listing_id
                existing_listing.title = item.title
                existing_listing.brand = item.brand
                existing_listing.vertical = item.vertical
                existing_listing.image_url = item.image_url
                existing_listing.internal_state = "ACTIVE"
                existing_listing.ssp = item.target_price
                existing_listing.mrp = item.target_mrp
                existing_listing.inventory = item.inventory
                existing_listing.weight = item.weight
                existing_listing.length = item.length
                existing_listing.breadth = item.breadth
                existing_listing.height = item.height
                existing_listing.synced_at = datetime.now()
                db.commit()
            except Exception as ml_err:
                logger.warning(f"写入 makro_listings 失败: {ml_err}")

            return {
                "success": True,
                "listing_id": listing_id,
                "sku_id": item.seller_sku,
                "status": "ACTIVE"
            }

        except Exception as e:
            item.status = "FAILED"
            item.error_message = str(e)
            db.commit()
            raise e
