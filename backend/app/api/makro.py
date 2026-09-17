import re
import json
import time
import uuid
import logging
from typing import Dict, Any, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from ..database import get_db
from ..models.product import Product, ProductVariant
from ..models.setting import SystemSetting
from ..models.task import TaskLog
from ..schemas.setting import SyncCredentialsRequest
from ..schemas.product import BatchPublishRequest
from ..services.makro_client import MakroClient
from ..services.vertical_service import VerticalService
from ..services.task_manager import task_manager, TaskManager
from ..services.audit_logger import record_audit_log
from ..config import settings
from .products import _format_product

router = APIRouter(prefix="/makro", tags=["Makro上品引擎"])
logger = logging.getLogger(__name__)

def _get_setting_val(db: Session, key: str, default: str) -> str:
    s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return s.value if s and s.value else default

def _format_attribute_value_and_qualifier(
    attr_name: str,
    raw_val: Any,
    raw_qual: Optional[str],
    def_item: Optional[dict],
    brand: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    根据 Makro 官方类目属性定义，自动解析并规范化属性值与单位限定符 (Qualifier)
    """
    val_str = str(raw_val).strip() if raw_val is not None else ""
    qual = raw_qual

    # 1. 严防品牌名污染 model_number 与 model_name (Makro 官方限制: Brand name should not be part of the attribute value)
    if attr_name in ["model_number", "model_name"] and brand:
        val_str = re.sub(rf'^\s*{re.escape(brand)}\s*[-_:]*\s*', '', val_str, flags=re.I)
        val_str = re.sub(rf'\b{re.escape(brand)}\b', '', val_str, flags=re.I).strip(' -_,:;')
        if not val_str:
            val_str = "STD-01"

    if not def_item:
        return val_str, qual

    qual_vals = [x.strip() for x in (def_item.get("qualifierAllowedValues") or "").split("||") if x.strip()]
    default_qual = def_item.get("defaultQualifier") or (qual_vals[0] if qual_vals else None)
    allowed_vals = [x.strip() for x in (def_item.get("allowedValues") or "").split("||") if x.strip()]

    # 2. 处理容量/存储相关字段 (storage_capacity, capacity, internal_storage, ram, etc.)
    if attr_name in ["storage_capacity", "capacity", "internal_storage", "ram", "memory"]:
        m = re.search(r'([\d\.]+)\s*([a-zA-Z]+)?', val_str)
        if m:
            num_val = float(m.group(1))
            unit = (m.group(2) or "").upper()
            
            if unit in ["TB", "T"]:
                qual = "TB" if "TB" in qual_vals else default_qual
                val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
            elif unit in ["GB", "G"]:
                if num_val >= 1000 and "TB" in qual_vals:
                    qual = "TB"
                    tb_val = num_val / 1000.0
                    val_str = str(int(tb_val)) if tb_val.is_integer() else str(tb_val)
                else:
                    qual = "GB" if "GB" in qual_vals else default_qual
                    val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
            elif unit in ["MB", "M"]:
                qual = "MB" if "MB" in qual_vals else default_qual
                val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
            else:
                qual = default_qual
                val_str = str(int(num_val)) if num_val.is_integer() else str(num_val)
        elif qual_vals and not qual:
            qual = default_qual

        if allowed_vals and val_str not in allowed_vals:
            matched = next((av for av in allowed_vals if av == val_str), allowed_vals[0])
            val_str = matched

        return val_str, qual

    # 3. 处理尺寸长度 (length, width, height, depth, overall_length)
    if attr_name in ["overall_length", "width", "length", "height", "depth"]:
        if not qual or qual not in qual_vals:
            qual = next((q for q in qual_vals if q.lower() == "cm"), default_qual or "cm")
        return val_str, qual

    # 4. 处理重量 (weight)
    if attr_name in ["weight"]:
        if not qual or qual not in qual_vals:
            qual = next((q for q in qual_vals if q.lower() in ["g", "kg"]), default_qual or "g")
        return val_str, qual

    # 5. 处理速度 (speed, read_speed, write_speed)
    if attr_name in ["speed", "read_speed", "write_speed"]:
        if not qual or qual not in qual_vals:
            qual = next((q for q in qual_vals if "mbps" in q.lower() or "mb/s" in q.lower()), default_qual)
        return val_str, qual

    # 6. 通用 Qualifier 强制匹配 (只要属性定义了 qualifierAllowedValues)
    if qual_vals:
        if not qual or qual not in qual_vals:
            qual = default_qual or qual_vals[0]

    # 7. 通用枚举值 AllowedValues 匹配
    if allowed_vals and val_str not in allowed_vals:
        matched = next((av for av in allowed_vals if val_str.lower() in av.lower() or av.lower() in val_str.lower()), allowed_vals[0])
        val_str = matched

    return val_str, qual

def _build_makro_payload(
    db: Session,
    product: Product,
    request_id: str,
    txn_id: str,
    req_id: str,
    images_map: Dict[str, str],
    client: Optional[MakroClient] = None,
    draft_resp: Optional[Dict[str, Any]] = None,
    variant: Optional[ProductVariant] = None,
    target_store: Optional[Any] = None
) -> Dict[str, Any]:
    """
    基于抓包逆向结果精准构建 Makro (Flipkart SaaS) submit 请求体
    动态根据类目定义过滤与补齐必填属性（支持特定变体专属参数与多变体聚合）
    """
    seller_id = (
        (target_store.seller_id if target_store and getattr(target_store, "seller_id", None) else None)
        or (client.seller_id if client and getattr(client, "seller_id", None) else None)
        or _get_setting_val(db, "seller_id", settings.DEFAULT_SELLER_ID)
    )
    # 确定目标店铺刊登品牌与源品牌
    target_brand = (
        (target_store.default_brand.strip() if target_store and getattr(target_store, "default_brand", None) and target_store.default_brand.strip() else None)
        or _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND)
        or product.makro_brand
        or "Beishi"
    )
    brand = target_brand
    source_brand = (product.makro_brand or "").strip() or _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND) or "Beishi"

    raw_vertical = product.makro_vertical or "bath_towel"
    valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
    vertical = valid_vertical
    
    shipping_days = _get_setting_val(db, "shipping_days", settings.DEFAULT_SHIPPING_DAYS)
    country_of_origin = _get_setting_val(db, "country_of_origin", settings.DEFAULT_COUNTRY_OF_ORIGIN)
    manufacturer = _get_setting_val(db, "manufacturer_details", settings.DEFAULT_MANUFACTURER)
    packer = _get_setting_val(db, "packer_details", settings.DEFAULT_PACKER)

    # 包装参数
    pkg_dims = json.loads(product.makro_package_dimensions) if product.makro_package_dimensions else {}
    pkg_len = str(pkg_dims.get("length", _get_setting_val(db, "default_pkg_length", "20")))
    pkg_breadth = str(pkg_dims.get("breadth", _get_setting_val(db, "default_pkg_breadth", "15")))
    pkg_height = str(pkg_dims.get("height", _get_setting_val(db, "default_pkg_height", "5")))
    pkg_weight = str(pkg_dims.get("weight", _get_setting_val(db, "default_pkg_weight", "0.5")))

    # 目标变体 SKU 与价格 (确保 SKU 唯一以规避 SKU_ALREADY_USED 限制)
    base_sku = (variant.sku_id if variant and variant.sku_id else None) or product.sku_id or f"BS-{product.id}"
    ts_suffix = str(int(time.time()))[-4:]
    sku_id = f"{base_sku}-{ts_suffix}" if not base_sku.endswith(ts_suffix) else base_sku

    var_selling = (variant.makro_selling_price if variant and variant.makro_selling_price else None) or product.makro_selling_price
    var_mrp = (variant.makro_mrp if variant and variant.makro_mrp else None) or product.makro_mrp
    selling_price = str(int(var_selling or 199))
    mrp_price = str(int(var_mrp or 299))

    # 获取类目官方属性定义以做严格过滤
    allowed_attrs = {}
    if client:
        try:
            v_def = client.get_vertical_definition(vertical)
            for item in v_def.get("entityDefinitionMap", {}).get(vertical, {}).get("definitionList", []):
                name = item.get("attributeName")
                if name:
                    allowed_attrs[name] = item
        except Exception as e:
            logger.warning(f"获取类目 {vertical} 属性定义失败: {e}")

    # ★★★ 动态根据目标店铺刊登品牌替换商品标题与描述 ★★★
    raw_title = product.makro_title or product.takealot_title or ""
    store_title = raw_title
    if source_brand and target_brand and source_brand.lower() != target_brand.lower():
        store_title = re.sub(rf'^\s*{re.escape(source_brand)}\b', target_brand, store_title, flags=re.I)
        store_title = re.sub(rf'\b{re.escape(source_brand)}\b', target_brand, store_title, flags=re.I)

    # 兜底：如果标题未以 target_brand 开头，且未包含 target_brand，则规范加上 target_brand 前缀
    if target_brand and not store_title.lower().startswith(target_brand.lower()):
        clean_prefix = re.sub(r'^(Generic|Beishi|[a-zA-Z0-9_\-]+)\s*[\'’s]*\s*[-_:]*\s*', '', store_title, flags=re.I)
        store_title = f"{target_brand} {clean_prefix.strip()}"

    raw_desc = str(product.makro_description or "")
    store_desc = raw_desc
    if source_brand and target_brand and source_brand.lower() != target_brand.lower():
        store_desc = re.sub(rf'\b{re.escape(source_brand)}\b', target_brand, store_desc, flags=re.I)

    # Catalog 属性组装
    user_attrs = json.loads(product.makro_catalog_attributes) if product.makro_catalog_attributes else {}
    catalog_attrs = {}

    # 1. 保留合法属性，并替换其中出现的旧品牌名
    for k, v_list in user_attrs.items():
        if allowed_attrs and k not in allowed_attrs:
            continue  # 抛弃该类目不支持的属性 (如 plier 下的 colour/size)
        if isinstance(v_list, list) and len(v_list) > 0:
            val = v_list[0].get("value")
            q = v_list[0].get("qualifier")
        else:
            val = str(v_list)
            q = None

        val_str = str(val) if val is not None else ""
        if k not in ["model_name", "model_number"] and source_brand and target_brand and source_brand.lower() != target_brand.lower():
            val_str = re.sub(rf'\b{re.escape(source_brand)}\b', target_brand, val_str, flags=re.I)

        if k in ["overall_length", "width", "length", "height", "depth"] and not q:
            q = "cm"
        catalog_attrs[k] = [{"value": val_str, "qualifier": q}]

    # 2. 保证 brand 属性 100% 设为当前目标店铺刊登品牌！
    catalog_attrs["brand"] = [{"value": target_brand, "qualifier": None}]

    # 3. 保证 description 存在 (如类目支持) 并替换为目标店铺品牌
    if (not allowed_attrs or "description" in allowed_attrs) and "description" not in catalog_attrs and store_desc:
        catalog_attrs["description"] = [{"value": store_desc, "qualifier": None}]

    # 4. 全品类智能自适应必填字段抽取与补全
    full_text = f"{store_title} {product.takealot_title or ''} {store_desc} {product.takealot_specs or ''}".lower()

    # A. 针对不同类目的特定字段智能抽取
    # A1. 网络设备 (network_switch / router)
    if "network" in vertical or vertical in ["network_switch", "switch", "router"]:
        if "number_of_ethernet_ports" in allowed_attrs and "number_of_ethernet_ports" not in catalog_attrs:
            ports_match = re.search(r'(\d+)\s*(?:port|ports|-port|x\s*rj45)', full_text)
            ports_val = ports_match.group(1) if ports_match else "16"
            catalog_attrs["number_of_ethernet_ports"] = [{"value": str(ports_val), "qualifier": None}]
        if "speed" in allowed_attrs and "speed" not in catalog_attrs:
            speed_val = "1000" if ("gigabit" in full_text or "1000" in full_text) else "100"
            catalog_attrs["speed"] = [{"value": speed_val, "qualifier": "Mbps"}]
        if "type" in allowed_attrs and "type" not in catalog_attrs:
            sw_type = "Unmanaged"
            if "smart" in full_text: sw_type = "Smart"
            elif "managed" in full_text and "unmanaged" not in full_text: sw_type = "Fully Managed"
            catalog_attrs["type"] = [{"value": sw_type, "qualifier": None}]

    # A2. 智能插座 / 智能开关 / 定时器开关 (smart_switch_plug / electronic_timer_switch)
    if "smart_switch" in vertical or "smart_plug" in vertical or "timer_switch" in vertical:
        if "type" in allowed_attrs and "type" not in catalog_attrs:
            catalog_attrs["type"] = [{"value": "Smart Switch" if "switch" in full_text else "Smart Plug", "qualifier": None}]
        if "maximum_load" in allowed_attrs and "maximum_load" not in catalog_attrs:
            catalog_attrs["maximum_load"] = [{"value": "6000", "qualifier": "W"}]
        if "voltage" in allowed_attrs and "voltage" not in catalog_attrs:
            catalog_attrs["voltage"] = [{"value": "100-240V AC", "qualifier": None}]
        if "installation_method" in allowed_attrs and "installation_method" not in catalog_attrs:
            catalog_attrs["installation_method"] = [{"value": "In-wall", "qualifier": None}]
        if "voice_assistant_compatibility" in allowed_attrs and "voice_assistant_compatibility" not in catalog_attrs:
            catalog_attrs["voice_assistant_compatibility"] = [{"value": "Google Assistant and Alexa", "qualifier": None}]
        if "connectivity" in allowed_attrs and "connectivity" not in catalog_attrs:
            catalog_attrs["connectivity"] = [{"value": "Wi-Fi", "qualifier": None}]
        if "compatible_devices" in allowed_attrs and "compatible_devices" not in catalog_attrs:
            catalog_attrs["compatible_devices"] = [{"value": "Geyser, Water Heater, Home Appliances", "qualifier": None}]

    # 通用质保与售后
    if "warranty_summary" in allowed_attrs and "warranty_summary" not in catalog_attrs:
        catalog_attrs["warranty_summary"] = [{"value": "1 Year Manufacturer Warranty", "qualifier": None}]
    if "warranty_service_type" in allowed_attrs and "warranty_service_type" not in catalog_attrs:
        catalog_attrs["warranty_service_type"] = [{"value": "Customer Support", "qualifier": None}]

    # B. 通用必填项兜底
    # 严格移除所有品牌名以生成合规 model_name 与 model_number (平台规则: Brand name should not be part of attribute value)
    clean_model_title = store_title
    brands_to_clean = {target_brand, source_brand, _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND), "Beishi"}
    for b_to_clean in brands_to_clean:
        if b_to_clean:
            clean_model_title = re.sub(rf'^\s*{re.escape(b_to_clean)}\s*[\'’s]*\s*[-_:]*\s*', '', clean_model_title, flags=re.I)
            clean_model_title = re.sub(rf'\b{re.escape(b_to_clean)}\b', '', clean_model_title, flags=re.I).strip(' -_,:;')

    smart_defaults = {
        "model_name": (clean_model_title or f"Standard {vertical}")[:40],
        "model_number": (clean_model_title or f"STD-{product.id}")[:250],
        "brand_colour": "White" if "white" in full_text else ("Black" if "black" in full_text else ("Yellow" if "yellow" in full_text else "Multicolor")),
        "colour": "White" if "white" in full_text else ("Black" if "black" in full_text else "Multicolor"),
        "packaging_type": "Box" if ("box" in full_text or "network" in vertical) else "Pack",
        "pack_of": "1",
        "sales_package": (store_title or f"1 x {vertical}")[:60],
        "plier_type": "Wire Stripper",
        "overall_length": "20",
        "bath_towel_type": "Cloth",
        "towel_type": "Bath",
        "material": "Metal" if ("network" in vertical or "metal" in full_text) else ("Microfiber" if "towel" in vertical else "ABS Plastic")
    }

    for k, v in smart_defaults.items():
        if allowed_attrs and k not in allowed_attrs:
            continue
        if k not in catalog_attrs:
            q = "cm" if k in ["overall_length", "width", "length"] else None
            catalog_attrs[k] = [{"value": str(v), "qualifier": q}]

    # ★★★ 强制保证 model_number 绝不包含品牌名 (Makro CMS 官方限制: Brand name should not be part of the attribute value)
    if allowed_attrs and "model_number" in allowed_attrs:
        raw_mn = clean_model_title or f"STD-{product.id}"
        catalog_attrs["model_number"] = [{"value": raw_mn[:250], "qualifier": None}]

    # ★★★ 变体专有属性精确覆盖 (容量 1TB/2TB/512GB, 尺码, 颜色, 包装数量等) ★★★
    var_attrs = json.loads(product.variant_attributes) if product.variant_attributes else {}
    if variant and variant.variant_attributes:
        var_attrs = {**var_attrs, **(json.loads(variant.variant_attributes) if variant.variant_attributes else {})}

    var_colour = (variant.colour if variant and variant.colour else None) or product.colour or var_attrs.get("colour")
    var_brand_colour = (variant.brand_colour if variant and variant.brand_colour else None) or product.brand_colour or var_colour
    var_size = (variant.size if variant and variant.size else None) or product.size or var_attrs.get("size")
    var_pack = str((variant.pack_of if variant and variant.pack_of else None) or product.pack_of or var_attrs.get("pack_of") or "1")

    if var_colour and (not allowed_attrs or "colour" in allowed_attrs):
        catalog_attrs["colour"] = [{"value": str(var_colour), "qualifier": None}]
    if var_brand_colour and (not allowed_attrs or "brand_colour" in allowed_attrs):
        catalog_attrs["brand_colour"] = [{"value": str(var_brand_colour), "qualifier": None}]
    if var_size and (not allowed_attrs or "size" in allowed_attrs):
        catalog_attrs["size"] = [{"value": str(var_size), "qualifier": None}]
    if var_pack and (not allowed_attrs or "pack_of" in allowed_attrs):
        catalog_attrs["pack_of"] = [{"value": str(var_pack), "qualifier": None}]

    # 容量/内存参数 (如 1TB, 2TB, 512GB)
    cap_val = var_attrs.get("capacity") or var_attrs.get("storage_capacity")
    if cap_val:
        for cap_k in ["storage_capacity", "capacity", "internal_storage"]:
            if not allowed_attrs or cap_k in allowed_attrs:
                def_item = allowed_attrs.get(cap_k)
                val_s, q_s = _format_attribute_value_and_qualifier(cap_k, cap_val, None, def_item, brand=brand)
                catalog_attrs[cap_k] = [{"value": val_s, "qualifier": q_s}]

    # C. 针对 Makro 草稿报错中指明的任何缺失字段，自动根据官方枚举或定义兜底补充
    missing_attrs = []
    if draft_resp and isinstance(draft_resp, dict):
        attr_errs = draft_resp.get("errorDetails", {}).get("catalogErrors", {}).get("systemValidationErrors", {}).get("attributeErrors", {})
        missing_attrs = list(attr_errs.keys())

    for missing_k in missing_attrs:
        if missing_k not in catalog_attrs and missing_k in allowed_attrs:
            def_item = allowed_attrs[missing_k]
            allowed_vals = [x.strip() for x in (def_item.get("allowedValues") or "").split("||") if x.strip()]
            val = allowed_vals[0] if allowed_vals else (def_item.get("exampleValue") or "Standard")
            
            # 校验并适配数值类型 (DECIMAL / NUMBER)
            attr_type = (def_item.get("attributeType") or "").upper()
            if attr_type in ["DECIMAL", "NUMBER"]:
                try:
                    float(val)
                except (ValueError, TypeError):
                    ex = def_item.get("exampleValue")
                    try:
                        float(ex)
                        val = ex
                    except (ValueError, TypeError):
                        val = "1"

            qual_vals = [x.strip() for x in (def_item.get("qualifierAllowedValues") or "").split("||") if x.strip()]
            qual = qual_vals[0] if qual_vals else (def_item.get("defaultQualifier") or None)
            
            val_s, q_s = _format_attribute_value_and_qualifier(missing_k, val, qual, def_item, brand=brand)
            catalog_attrs[missing_k] = [{"value": val_s, "qualifier": q_s}]

    # D. 全量清洗与校验所有属性的值与 Qualifier，保证 100% 符合官方定义
    for attr_k, attr_v_list in list(catalog_attrs.items()):
        if attr_k in allowed_attrs:
            def_item = allowed_attrs[attr_k]
            if attr_v_list and isinstance(attr_v_list, list) and len(attr_v_list) > 0:
                cur_val = attr_v_list[0].get("value")
                cur_qual = attr_v_list[0].get("qualifier")
                norm_val, norm_qual = _format_attribute_value_and_qualifier(attr_k, cur_val, cur_qual, def_item, brand=brand)
                catalog_attrs[attr_k] = [{"value": norm_val, "qualifier": norm_qual}]

    now_ms = int(time.time() * 1000)

    # 组装完整的 submit Payload
    payload = {
        "txnId": txn_id,
        "reqId": req_id,
        "message": None,
        "requestId": request_id,
        "sellerId": seller_id,
        "skuId": sku_id,
        "vertical": vertical,
        "state": "DRAFT",
        "context": "PRODUCT_LISTING_CREATION",
        "catalogRequestEntity": {
            "catalogAttributes": catalog_attrs,
            "images": images_map,
            "workflow": "STRICT_QC",
            "customAttributeMap": {},
            "fsnMatched": False
        },
        "listingRequestEntity": {
            "skuId": sku_id,
            "sellerId": seller_id,
            "createMatchedFsnListing": True,
            "listingAttributes": {
                "sku_id": [{"value": sku_id, "qualifier": None}],
                "listing_status": [{"value": "ACTIVE", "qualifier": None}],
                "mrp": [{"value": mrp_price, "qualifier": "INR"}],
                "flipkart_selling_price": [{"value": selling_price, "qualifier": "INR"}],
                "service_profile": [{"value": settings.DEFAULT_SERVICE_PROFILE, "qualifier": None}],
                "shipping_days": [{"value": shipping_days, "qualifier": "DAY"}],
                "country_of_origin": [{"value": country_of_origin, "qualifier": None}],
                "manufacturer_details": [{"value": manufacturer, "qualifier": None}],
                "packer_details": [{"value": packer, "qualifier": None}]
            },
            "packages": [
                {
                    "id": {"value": str(now_ms), "qualifier": None},
                    "breadth": {"value": pkg_breadth, "qualifier": "CM"},
                    "length": {"value": pkg_len, "qualifier": "CM"},
                    "height": {"value": pkg_height, "qualifier": "CM"},
                    "weight": {"value": pkg_weight, "qualifier": "KG"},
                    "sku_id": {"value": sku_id, "qualifier": None}
                }
            ]
        },
        "errorDetails": {
            "globalErrors": [],
            "catalogErrors": {
                "manualValidationErrors": {"globalErrors": [], "attributeErrors": {}, "imageErrors": {}, "sizeChartErrors": [], "customAttributeErrors": {}},
                "systemValidationErrors": {"globalErrors": [], "attributeErrors": {}, "imageErrors": {}, "sizeChartErrors": [], "customAttributeErrors": {}}
            },
            "listingErrors": {
                "systemValidationErrors": {"globalErrors": [], "attributeErrors": {}}
            }
        },
        "createdOn": now_ms,
        "lastModified": now_ms,
        "version": 1
    }
    return payload

def _record_store_listing(
    db: Session,
    product_id: int,
    target_store: Any,
    is_success: bool,
    sku_id: Optional[str],
    request_id: Optional[str],
    msg: Optional[str],
    selling_price: Optional[float] = None,
    mrp: Optional[float] = None,
    brand: Optional[str] = None
):
    """记录或更新商品在特定店铺的上架状态与凭据"""
    if not target_store:
        try:
            from ..models.store import Store
            target_store = db.query(Store).filter(Store.is_default == True).first() or db.query(Store).first()
        except Exception:
            pass

    if not target_store or not hasattr(target_store, "id"):
        return

    try:
        from ..models.store import ProductStoreListing
        listing = db.query(ProductStoreListing).filter(
            ProductStoreListing.product_id == product_id,
            ProductStoreListing.store_id == target_store.id
        ).first()
        if not listing:
            listing = ProductStoreListing(
                product_id=product_id,
                store_id=target_store.id
            )
            db.add(listing)

        listing.brand = brand or getattr(target_store, "default_brand", None) or "Beishi"
        listing.status = "SUBMITTED" if is_success else "FAILED"
        if is_success and sku_id:
            listing.makro_sku_id = sku_id
        if request_id:
            listing.makro_request_id = request_id
        listing.makro_submit_error = None if is_success else msg
        if selling_price is not None:
            listing.selling_price = selling_price
        if mrp is not None:
            listing.mrp = mrp
        listing.submitted_at = datetime.utcnow()
        db.commit()
    except Exception as ex:
        logger.error(f"记录 ProductStoreListing 异常: {ex}", exc_info=True)

def _publish_single_product(
    client: MakroClient,
    db: Session,
    product: Product,
    vertical: str,
    vid: Optional[str],
    brand: str,
    target_store: Optional[Any] = None
) -> dict:
    """内部函数：为单个扁平独立商品创建草稿、上传图组并提交 Makro 发布"""
    draft_resp = client.create_draft(vertical=vertical, brand=brand, vid=vid)
    request_id = draft_resp.get("requestId")
    txn_id = draft_resp.get("txnId")
    req_id = draft_resp.get("reqId")

    if not request_id:
        raise ValueError(f"商品 {product.id} 创建草稿失败: {draft_resp}")

    product.makro_request_id = request_id

    # 上传专属图片
    raw_images = json.loads(product.raw_images) if product.raw_images else []
    images_map = {}
    for idx, img_url in enumerate(raw_images[:5]):
        cdn_url = client.upload_image_from_url(img_url, vertical, request_id)
        if cdn_url:
            images_map[str(len(images_map))] = cdn_url

    if not images_map:
        raise ValueError(f"商品 {product.id} 没有可用的有效图片上传")

    product.makro_images = json.dumps(images_map)

    # 组装 Payload 并提交
    payload = _build_makro_payload(
        db, product, request_id, txn_id, req_id, images_map,
        client=client, draft_resp=draft_resp, target_store=target_store
    )
    is_success, err_details, msg = client.submit_product(payload)

    if is_success:
        product.status = "SUBMITTED"
        product.makro_sku_id = payload.get("skuId")
        product.makro_submit_error = None
    else:
        product.status = "FAILED"
        product.makro_submit_error = msg

    db.commit()

    # 写入多店铺独立 Listing 记录
    _record_store_listing(
        db=db,
        product_id=product.id,
        target_store=target_store,
        is_success=is_success,
        sku_id=payload.get("skuId"),
        request_id=request_id,
        msg=msg,
        selling_price=product.makro_selling_price,
        mrp=product.makro_mrp,
        brand=brand
    )

    return {
        "product_id": product.id,
        "sku_id": product.sku_id or product.makro_sku_id,
        "success": is_success,
        "request_id": request_id,
        "message": msg,
        "error_details": err_details
    }

def _publish_single_variant(
    client: MakroClient,
    db: Session,
    product: Product,
    variant: ProductVariant,
    vertical: str,
    vid: Optional[str],
    brand: str,
    target_store: Optional[Any] = None
) -> dict:
    """内部函数：兼容遗留子变体结构发布"""
    draft_resp = client.create_draft(vertical=vertical, brand=brand, vid=vid)
    request_id = draft_resp.get("requestId")
    txn_id = draft_resp.get("txnId")
    req_id = draft_resp.get("reqId")

    if not request_id:
        raise ValueError(f"变体 {variant.sku_id} 创建草稿失败: {draft_resp}")

    variant.makro_request_id = request_id

    var_images = json.loads(variant.images) if variant.images else []
    if not var_images:
        var_images = json.loads(product.raw_images) if product.raw_images else []

    images_map = {}
    for idx, img_url in enumerate(var_images[:5]):
        cdn_url = client.upload_image_from_url(img_url, vertical, request_id)
        if cdn_url:
            images_map[str(len(images_map))] = cdn_url

    if not images_map:
        raise ValueError(f"变体 {variant.sku_id} 没有可用的有效图片上传")

    variant.makro_image_urls = json.dumps(images_map)

    payload = _build_makro_payload(
        db, product, request_id, txn_id, req_id, images_map,
        client=client, draft_resp=draft_resp, variant=variant, target_store=target_store
    )
    is_success, err_details, msg = client.submit_product(payload)

    if is_success:
        variant.status = "SUBMITTED"
        variant.makro_sku_id = payload.get("skuId")
        variant.makro_submit_error = None
    else:
        variant.status = "FAILED"
        variant.makro_submit_error = msg

    db.commit()

    # 写入多店铺独立 Listing 记录
    _record_store_listing(
        db=db,
        product_id=product.id,
        target_store=target_store,
        is_success=is_success,
        sku_id=payload.get("skuId"),
        request_id=request_id,
        msg=msg,
        selling_price=variant.makro_selling_price or product.makro_selling_price,
        mrp=variant.makro_mrp or product.makro_mrp,
        brand=brand
    )

    return {
        "variant_id": variant.id,
        "sku_id": variant.sku_id,
        "success": is_success,
        "request_id": request_id,
        "message": msg,
        "error_details": err_details
    }

@router.post("/publish/{product_id}", summary="自动执行全流程上品到 Makro (支持指定店铺或全部店铺)")
def publish_product_to_makro(
    product_id: int,
    store_id: Optional[int] = None,
    publish_all_stores: bool = False,
    force: bool = False,
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    # 违禁品安全防护
    if product.compliance_status == "PROHIBITED" and not force:
        comp_details = json.loads(product.compliance_details) if product.compliance_details else {}
        items = comp_details.get("prohibited_items", [])
        items_str = "、".join(items) if items else "蓝牙/WiFi/红外/液体"
        raise HTTPException(
            status_code=400,
            detail=f"【违禁品拦截】该商品命中平台禁售规则（包含：{items_str}），严禁直接上品以防店铺封禁！如需强制发布请开启强制开关。"
        )

    # 确定目标店铺列表
    from ..models.store import Store
    target_stores = []
    if publish_all_stores:
        target_stores = db.query(Store).filter(Store.is_active == True).all()
        if not target_stores:
            target_stores = [None]
    elif store_id:
        store = db.query(Store).filter(Store.id == store_id).first()
        if not store:
            raise HTTPException(status_code=404, detail="指定的店铺未找到")
        target_stores = [store]
    else:
        # 默认店铺
        default_store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()
        if not default_store:
            default_store = db.query(Store).filter(Store.is_active == True).first()
        target_stores = [default_store] if default_store else [None]

    raw_vertical = product.makro_vertical or "bath_towel"
    valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
    if valid_vertical != product.makro_vertical:
        product.makro_vertical = valid_vertical
        db.commit()

    store_results = []
    any_success = False

    for s_item in target_stores:
        s_name = s_item.name if s_item else "默认店铺"
        s_id = s_item.id if s_item else None
        client = MakroClient.from_store(s_item) if s_item else MakroClient.from_db(db)
        target_brand = (
            (s_item.default_brand.strip() if s_item and getattr(s_item, "default_brand", None) and s_item.default_brand.strip() else None)
            or _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND)
            or product.makro_brand
            or "Beishi"
        )
        brand = target_brand

        task = TaskLog(
            product_id=product.id,
            task_type="SUBMIT_LISTING",
            status="RUNNING",
            message=f"开始向店铺【{s_name}】(刊登品牌: {brand}) 执行 Makro 上品调用..."
        )
        db.add(task)
        db.commit()

        if not client.cookie:
            msg = f"店铺【{s_name}】未检测到登录态 Cookie！请先在多店铺管理中配置 Cookie。"
            _record_store_listing(
                db=db,
                product_id=product.id,
                target_store=s_item,
                is_success=False,
                sku_id=None,
                request_id=None,
                msg=msg,
                selling_price=product.makro_selling_price,
                mrp=product.makro_mrp,
                brand=brand
            )
            task.status = "FAILED"
            task.message = msg
            task.finished_at = datetime.utcnow()
            db.commit()
            store_results.append({
                "store_id": s_id,
                "store_name": s_name,
                "success": False,
                "message": msg
            })
            continue

        try:
            if product.variants and len(product.variants) > 0:
                v_results = []
                for v in product.variants:
                    try:
                        v_res = _publish_single_variant(client, db, product, v, valid_vertical, vid, brand, target_store=s_item)
                        v_results.append(v_res)
                    except Exception as ex:
                        logger.error(f"变体 {v.sku_id} 在店铺 {s_name} 上架异常: {ex}", exc_info=True)
                        v.status = "FAILED"
                        v.makro_submit_error = str(ex)
                        db.commit()
                        v_results.append({"variant_id": v.id, "sku_id": v.sku_id, "success": False, "message": str(ex)})

                v_succ = sum(1 for r in v_results if r["success"])
                is_store_success = (v_succ == len(product.variants))
                if is_store_success:
                    any_success = True
                    task.status = "SUCCESS"
                    task.message = f"全量成功上架所有 {v_succ} 个变体至【{s_name}】(刊登品牌: {brand})！"
                elif v_succ > 0:
                    any_success = True
                    task.status = "WARNING"
                    task.message = f"部分变体提交成功至【{s_name}】({v_succ}/{len(product.variants)}, 刊登品牌: {brand})"
                else:
                    task.status = "FAILED"
                    task.message = f"店铺【{s_name}】所有变体提交均失败"

                task.detail_logs = json.dumps(v_results, ensure_ascii=False)
                task.finished_at = datetime.utcnow()
                db.commit()

                store_results.append({
                    "store_id": s_id,
                    "store_name": s_name,
                    "success": is_store_success,
                    "message": task.message,
                    "results": v_results
                })
            else:
                # 扁平独立商品直接发布
                p_res = _publish_single_product(client, db, product, valid_vertical, vid, brand, target_store=s_item)
                if p_res["success"]:
                    any_success = True
                    task.status = "SUCCESS"
                    task.message = f"成功上架商品至店铺【{s_name}】(刊登品牌: {brand}, 已提交审核)！"
                else:
                    task.status = "FAILED"
                    task.message = f"店铺【{s_name}】上架失败: {p_res['message']}"

                task.request_id = p_res.get("request_id")
                task.detail_logs = json.dumps(p_res, ensure_ascii=False)
                task.finished_at = datetime.utcnow()
                db.commit()

                store_results.append({
                    "store_id": s_id,
                    "store_name": s_name,
                    "success": p_res["success"],
                    "request_id": p_res.get("request_id"),
                    "message": p_res.get("message")
                })
        except Exception as e:
            logger.error(f"店铺 {s_name} 上架异常: {e}", exc_info=True)
            task.status = "FAILED"
            task.message = f"店铺【{s_name}】上架异常: {e}"
            task.finished_at = datetime.utcnow()
            db.commit()
            store_results.append({
                "store_id": s_id,
                "store_name": s_name,
                "success": False,
                "message": str(e)
            })

    if any_success:
        product.status = "SUBMITTED"
        product.makro_submit_error = None
    elif not product.status or product.status != "SUBMITTED":
        product.status = "FAILED"
        if store_results:
            product.makro_submit_error = store_results[0].get("message")
    db.commit()
    db.refresh(product)

    succ_count = sum(1 for r in store_results if r["success"])
    succ_names = "、".join([r["store_name"] for r in store_results if r["success"]]) or "无"
    record_audit_log(
        task_type="SUBMIT_LISTING",
        status="SUCCESS" if any_success else "FAILED",
        message=f"商品 #{product.id} 完成跨店铺上品 (成功店铺: {succ_names})",
        detail_logs={"product_id": product.id, "store_results": store_results},
        db=db
    )

    return {
        "success": any_success,
        "message": f"跨店铺上品处理完成: 成功 {succ_count}/{len(store_results)} 家店铺",
        "request_id": product.makro_request_id,
        "store_results": store_results,
        "product": _format_product(product)
    }

@router.post("/batch-publish", summary="批量上品到 Makro (支持指定店铺或全部店铺，后台异步执行)")
def batch_publish_products(req: BatchPublishRequest, force: Optional[bool] = None, db: Session = Depends(get_db)):
    if not req.product_ids:
        return {"total": 0, "success": 0, "failed": 0, "results": [], "message": "未选择商品"}

    effective_force = bool(getattr(req, "force", False) or (force is True))

    from ..models.store import Store
    target_stores = []
    if req.publish_all_stores:
        target_stores = db.query(Store).filter(Store.is_active == True).all()
        if not target_stores:
            target_stores = [None]
    elif req.store_id:
        store = db.query(Store).filter(Store.id == req.store_id).first()
        if store:
            target_stores = [store]
    elif req.store_ids:
        target_stores = db.query(Store).filter(Store.id.in_(req.store_ids), Store.is_active == True).all()

    if not target_stores:
        def_s = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first() or db.query(Store).filter(Store.is_active == True).first()
        target_stores = [def_s] if def_s else [None]

    total_ops = len(req.product_ids) * len(target_stores)
    store_names_str = "、".join([s.name if s else "默认店铺" for s in target_stores])
    force_tag = " [强制上架模式]" if effective_force else ""
    task_title = f"批量上品至 Makro [{store_names_str}]{force_tag} (共 {len(req.product_ids)} 件品 × {len(target_stores)} 店铺)"
    task = task_manager.create_task("BATCH_PUBLISH", task_title, total_ops, req.product_ids)
    task_id = task["id"]

    def _worker(tm: TaskManager, tid: str):
        from ..database import SessionLocal
        local_db = SessionLocal()
        try:
            completed_count = 0
            for pid in req.product_ids:
                if tm.is_cancelled(tid):
                    break

                product = local_db.query(Product).filter(Product.id == pid).first()
                if not product:
                    for _ in target_stores:
                        completed_count += 1
                        tm.update_progress(tid, current=completed_count, fail_inc=1, error=f"商品 {pid} 不存在")
                    continue

                p_title = product.takealot_title

                if product.compliance_status == "PROHIBITED" and not effective_force:
                    for _ in target_stores:
                        completed_count += 1
                        tm.update_progress(tid, current=completed_count, current_title=p_title, fail_inc=1, error="违禁品拦截，跳过发布")
                    continue

                raw_vertical = product.makro_vertical or "bath_towel"
                valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)

                for s_item in target_stores:
                    if tm.is_cancelled(tid):
                        break

                    s_name = s_item.name if s_item else "默认店铺"
                    local_client = MakroClient.from_store(s_item) if s_item else MakroClient.from_db(local_db)
                    target_brand = (
                        (s_item.default_brand.strip() if s_item and getattr(s_item, "default_brand", None) and s_item.default_brand.strip() else None)
                        or _get_setting_val(local_db, "default_brand", settings.DEFAULT_BRAND)
                        or product.makro_brand
                        or "Beishi"
                    )
                    brand = target_brand
                    disp_title = f"【{s_name}·{brand}】{p_title[:16]}"

                    if not local_client.cookie:
                        completed_count += 1
                        tm.update_progress(tid, current=completed_count, current_title=disp_title, fail_inc=1, error=f"店铺【{s_name}】未配置 Cookie")
                        _record_store_listing(local_db, product.id, s_item, False, None, None, f"店铺【{s_name}】未配置 Cookie", product.makro_selling_price, product.makro_mrp, brand=brand)
                        continue

                    try:
                        res = _publish_single_product(local_client, local_db, product, valid_vertical, vid, brand, target_store=s_item)
                        completed_count += 1
                        if res["success"]:
                            tm.update_progress(tid, current=completed_count, current_title=disp_title, success_inc=1)
                        else:
                            tm.update_progress(tid, current=completed_count, current_title=disp_title, fail_inc=1, error=res.get("message"))
                    except Exception as ex:
                        logger.error(f"商品 {pid} 在店铺 {s_name} 批量上架异常: {ex}", exc_info=True)
                        completed_count += 1
                        tm.update_progress(tid, current=completed_count, current_title=disp_title, fail_inc=1, error=str(ex))

            t_now = tm.get_task(tid)
            succ = t_now["success_count"] if t_now else 0
            fail = t_now["fail_count"] if t_now else 0
            status = "CANCELLED" if tm.is_cancelled(tid) else ("SUCCESS" if fail == 0 else ("FAILED" if succ == 0 else "SUCCESS"))
            tm.finish_task(tid, status=status, message=f"批量上品完成: 成功 {succ} 次, 失败 {fail} 次")
        finally:
            local_db.close()

    task_manager.start_task(task_id, _worker)

    return {
        "task_id": task_id,
        "status": "RUNNING",
        "total": total_ops,
        "message": f"已在后台启动多店铺批量上品至 Makro (共 {total_ops} 次任务分发)"
    }

@router.post("/publish-variant/{variant_id}", summary="单变体独立上品或重新上架到 Makro")
def publish_single_variant_to_makro(
    variant_id: int,
    store_id: Optional[int] = None,
    force: bool = False,
    db: Session = Depends(get_db)
):
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="变体未找到")
    product = variant.product
    if not product:
        raise HTTPException(status_code=404, detail="所属商品未找到")

    if product.compliance_status == "PROHIBITED" and not force:
        raise HTTPException(status_code=400, detail="【违禁品拦截】该商品命中平台禁售规则！")

    from ..models.store import Store
    target_store = None
    if store_id:
        target_store = db.query(Store).filter(Store.id == store_id).first()
    if not target_store:
        target_store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()
    if not target_store:
        target_store = db.query(Store).filter(Store.is_active == True).first()

    client = MakroClient.from_store(target_store) if target_store else MakroClient.from_db(db)
    if not client.cookie:
        raise HTTPException(status_code=400, detail="未检测到 Makro 登录态 Cookie！请先在多店铺管理中配置 Cookie。")

    raw_vertical = product.makro_vertical or "bath_towel"
    valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
    target_brand = (
        (target_store.default_brand.strip() if target_store and getattr(target_store, "default_brand", None) and target_store.default_brand.strip() else None)
        or _get_setting_val(db, "default_brand", settings.DEFAULT_BRAND)
        or product.makro_brand
        or "Beishi"
    )
    brand = target_brand

    try:
        res = _publish_single_variant(client, db, product, variant, valid_vertical, vid, brand, target_store=target_store)
        all_submitted = all(v.status == "SUBMITTED" for v in product.variants)
        any_submitted = any(v.status == "SUBMITTED" for v in product.variants)

        if all_submitted:
            product.status = "SUBMITTED"
            product.makro_submit_error = None
        elif any_submitted:
            product.status = "PARTIAL_SUBMITTED"
        db.commit()

        return {
            "success": res["success"],
            "message": res["message"],
            "result": res,
            "variant": {
                "id": variant.id,
                "sku_id": variant.sku_id,
                "status": variant.status,
                "makro_request_id": variant.makro_request_id,
                "makro_submit_error": variant.makro_submit_error
            }
        }
    except Exception as e:
        logger.error(f"单变体上架异常: {e}", exc_info=True)
        variant.status = "FAILED"
        variant.makro_submit_error = str(e)
        db.commit()
        return {"success": False, "message": str(e), "variant_id": variant.id}

@router.get("/build-payload/{product_id}", summary="预览构建的 Makro 上品请求体 (可用于调试或插件代发)")
def get_submit_payload_preview(
    product_id: int,
    variant_id: Optional[int] = None,
    store_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品未找到")

    mock_req_id = product.makro_request_id or "REQMOCK12345678"
    mock_txn = f"TXN-{product.id}"
    mock_req = f"REQ-{product.id}"
    images = json.loads(product.makro_images) if product.makro_images else {"0": "https://www.makro.co.za/asset/cms/sample"}
    
    target_variant = None
    if variant_id:
        target_variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()

    from ..models.store import Store
    target_store = None
    if store_id:
        target_store = db.query(Store).filter(Store.id == store_id).first()
    if not target_store:
        target_store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()

    payload = _build_makro_payload(db, product, mock_req_id, mock_txn, mock_req, images, variant=target_variant, target_store=target_store)
    return payload

@router.post("/sync-credentials", summary="从浏览器插件同步 Makro 登录态凭据")
def sync_credentials(req: SyncCredentialsRequest, db: Session = Depends(get_db)):
    updates = {}
    if req.seller_id:
        updates["seller_id"] = req.seller_id
    if req.fk_csrf_token:
        updates["fk_csrf_token"] = req.fk_csrf_token
    if req.cookie:
        updates["cookie"] = req.cookie

    for k, v in updates.items():
        item = db.query(SystemSetting).filter(SystemSetting.key == k).first()
        if item:
            item.value = v
        else:
            db.add(SystemSetting(key=k, value=v))

    db.commit()
    return {"message": "凭据同步成功", "synced_keys": list(updates.keys())}
