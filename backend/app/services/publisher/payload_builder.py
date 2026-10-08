# -*- coding: utf-8 -*-
"""
Makro (Flipkart SaaS) 刊登 Payload 组装器与类目回退自愈
"""

import re
import json
import time
import logging
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from app.config import settings
from app.models.setting import SystemSetting
from app.models.product import Product, ProductVariant
from app.constants.protected_ips import PROTECTED_ENTERTAINMENT_IPS
from app.constants.makro_attrs import LISTING_ONLY_ATTRS
from app.services.publisher.attribute_adapter import format_attribute_value_and_qualifier
from app.services.cleaner.device_rules import truncate_title_safely
from app.services.vertical_service import VerticalService

logger = logging.getLogger(__name__)


def get_setting_val(db: Session, key: str, default: str) -> str:
    s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return s.value if s and s.value else default


def get_safe_fallback_vertical(vertical: str, product: Product) -> Tuple[str, str]:
    """当类目在平台创建草稿报 500 崩溃时，根据商品标题关键词自动智能推导安全的备用类目"""
    fallback_v = "cases_covers"
    t_lower = (product.takealot_title or "").lower()
    if any(k in t_lower for k in ["craft", "bead", "mold", "mould", "resin", "diy", "clay", "art", "jewelry"]):
        fallback_v = "art_craft_kit"
    elif any(k in t_lower for k in ["knit", "loom", "sew", "crochet", "stitch"]):
        fallback_v = "sewing_kit"
    elif any(k in t_lower for k in ["garden", "prun", "trowel", "rake", "trimmer", "plant"]):
        fallback_v = "garden_tool_set"
    elif any(k in t_lower for k in ["car", "vehicle", "seat"]):
        fallback_v = "vehicle_cover"
    elif any(k in t_lower for k in ["coffee", "kitchen", "cooker", "dehydrator", "food"]):
        fallback_v = "coffee_maker"
    elif any(k in t_lower for k in ["torch", "light", "lamp"]):
        fallback_v = "torch"
    elif any(k in t_lower for k in ["tool", "plier", "wrench", "screw"]):
        fallback_v = "plier"

    return VerticalService.resolve_vertical(fallback_v)


def build_makro_payload(
    db: Session,
    product: Product,
    request_id: str,
    txn_id: str,
    req_id: str,
    images_map: Dict[str, str],
    client: Optional[Any] = None,
    draft_resp: Optional[Dict[str, Any]] = None,
    variant: Optional[ProductVariant] = None,
    target_store: Optional[Any] = None,
    vertical: Optional[str] = None
) -> Dict[str, Any]:
    """
    基于抓包逆向结果精准构建 Makro (Flipkart SaaS) submit 请求体
    动态根据类目定义过滤与补齐必填属性（支持特定变体专属参数与多变体聚合）
    """
    seller_id = (
        (target_store.seller_id if target_store and getattr(target_store, "seller_id", None) else None)
        or (client.seller_id if client and getattr(client, "seller_id", None) else None)
        or get_setting_val(db, "seller_id", settings.DEFAULT_SELLER_ID)
    )
    # 确定目标店铺刊登品牌与源品牌
    target_brand = (
        (target_store.default_brand.strip() if target_store and getattr(target_store, "default_brand", None) and target_store.default_brand.strip() else None)
        or get_setting_val(db, "default_brand", settings.DEFAULT_BRAND)
        or product.makro_brand
        or "Beishi"
    )
    brand = target_brand
    source_brand = (product.makro_brand or "").strip() or get_setting_val(db, "default_brand", settings.DEFAULT_BRAND) or "Beishi"

    raw_vertical = vertical or product.makro_vertical or "bath_towel"
    valid_vertical, vid = VerticalService.resolve_vertical(raw_vertical)
    vertical = valid_vertical
    if product.makro_vertical != valid_vertical:
        product.makro_vertical = valid_vertical

    shipping_days = get_setting_val(db, "shipping_days", settings.DEFAULT_SHIPPING_DAYS)
    country_of_origin = get_setting_val(db, "country_of_origin", settings.DEFAULT_COUNTRY_OF_ORIGIN)
    manufacturer = get_setting_val(db, "manufacturer_details", settings.DEFAULT_MANUFACTURER)
    packer = get_setting_val(db, "packer_details", settings.DEFAULT_PACKER)

    # 包装参数
    pkg_dims = json.loads(product.makro_package_dimensions) if product.makro_package_dimensions else {}
    pkg_len = str(pkg_dims.get("length", get_setting_val(db, "default_pkg_length", "20")))
    pkg_breadth = str(pkg_dims.get("breadth", get_setting_val(db, "default_pkg_breadth", "15")))
    pkg_height = str(pkg_dims.get("height", get_setting_val(db, "default_pkg_height", "5")))
    pkg_weight = str(pkg_dims.get("weight", get_setting_val(db, "default_pkg_weight", "0.5")))

    # 目标变体 SKU 与价格 (全店铺统一复用首次生成的 Makro SKU)
    existing_makro_sku = (variant.makro_sku_id if variant and variant.makro_sku_id else None) or product.makro_sku_id
    if not existing_makro_sku:
        if hasattr(product, "store_listings") and product.store_listings:
            for sl in product.store_listings:
                if sl.makro_sku_id:
                    existing_makro_sku = sl.makro_sku_id
                    break

    if existing_makro_sku:
        sku_id = existing_makro_sku
    else:
        base_sku = (variant.sku_id if variant and variant.sku_id else None) or product.sku_id or f"BS-{product.id}"
        ts_suffix = str(int(time.time()))[-4:]
        sku_id = f"{base_sku}-{ts_suffix}" if not base_sku.endswith(ts_suffix) else base_sku
        if variant:
            variant.makro_sku_id = sku_id
        product.makro_sku_id = sku_id
        try:
            db.commit()
        except Exception:
            pass

    var_selling = (variant.makro_selling_price if variant and variant.makro_selling_price else None) or product.makro_selling_price
    var_mrp = (variant.makro_mrp if variant and variant.makro_mrp else None) or product.makro_mrp
    selling_price = str(int(var_selling or 199))
    mrp_price = str(int(var_mrp or 299))

    # 获取类目官方属性定义以做严格过滤 (仅提取 CATALOG 属性，杜绝 LISTING 级属性混入 Catalog)
    allowed_attrs = {}
    if client:
        try:
            v_def = client.get_vertical_definition(vertical)
            for item in v_def.get("entityDefinitionMap", {}).get(vertical, {}).get("definitionList", []):
                if item.get("source") == "LISTING":
                    continue
                name = item.get("attributeName")
                if name and name not in LISTING_ONLY_ATTRS:
                    allowed_attrs[name] = item
        except Exception as e:
            logger.warning(f"获取类目 {vertical} 属性定义失败: {e}")

    # 动态根据目标店铺刊登品牌替换商品标题与描述
    raw_title = product.makro_title or product.takealot_title or ""
    store_title = raw_title
    if source_brand and target_brand and source_brand.lower() != target_brand.lower():
        store_title = re.sub(rf'^\s*{re.escape(source_brand)}\b', target_brand, store_title, flags=re.I)
        store_title = re.sub(rf'\b{re.escape(source_brand)}\b', target_brand, store_title, flags=re.I)

    # 兜底：如果标题未以 target_brand 开头，且未包含 target_brand，则规范加上 target_brand 前缀
    if target_brand and not store_title.lower().startswith(target_brand.lower()):
        clean_prefix = re.sub(r'^(Generic|Beishi|[a-zA-Z0-9_\-]+)\s*[\'’s]*\s*[-_:]*\s*', '', store_title, flags=re.I)
        store_title = f"{target_brand} {clean_prefix.strip()}"

    # 严格移除/替换受保护IP角色词，杜绝侵权词在标题中出现
    for ip in PROTECTED_ENTERTAINMENT_IPS:
        store_title = re.sub(rf'\b{re.escape(ip)}\b', 'Party', store_title, flags=re.I)

    raw_desc = str(product.makro_description or "")
    store_desc = raw_desc
    if source_brand and target_brand and source_brand.lower() != target_brand.lower():
        store_desc = re.sub(rf'\b{re.escape(source_brand)}\b', target_brand, store_desc, flags=re.I)

    # Catalog 属性组装
    user_attrs = json.loads(product.makro_catalog_attributes) if product.makro_catalog_attributes else {}
    catalog_attrs = {}

    # 1. 保留合法属性，并替换其中出现的旧品牌名
    for k, v_list in user_attrs.items():
        if k in LISTING_ONLY_ATTRS:
            continue
        if allowed_attrs and k not in allowed_attrs:
            continue
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

    # 2. 保证 brand 属性 100% 设为当前目标店铺刊登品牌
    catalog_attrs["brand"] = [{"value": target_brand, "qualifier": None}]

    # 3. 保证 description 存在并替换为目标店铺品牌
    if (not allowed_attrs or "description" in allowed_attrs) and "description" not in catalog_attrs and store_desc:
        catalog_attrs["description"] = [{"value": store_desc, "qualifier": None}]

    # 4. 全品类智能自适应必填字段抽取与补全
    full_text = f"{store_title} {product.takealot_title or ''} {store_desc} {product.takealot_specs or ''}".lower()

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

    if vertical == "costume_wear":
        char_val = "Party"
        if "character" in catalog_attrs and catalog_attrs["character"]:
            raw_c = str(catalog_attrs["character"][0].get("value") or "").strip()
            if raw_c and not any(ip in raw_c.lower() for ip in PROTECTED_ENTERTAINMENT_IPS):
                char_val = raw_c
        catalog_attrs["character"] = [{"value": char_val, "qualifier": None}]

        if not allowed_attrs or "theme" in allowed_attrs:
            if "theme" not in catalog_attrs:
                catalog_attrs["theme"] = [{"value": "Party & Celebration", "qualifier": None}]
        if not allowed_attrs or "type" in allowed_attrs:
            if "type" not in catalog_attrs:
                catalog_attrs["type"] = [{"value": "Costume", "qualifier": None}]
        if not allowed_attrs or "occasion" in allowed_attrs:
            if "occasion" not in catalog_attrs:
                catalog_attrs["occasion"] = [{"value": "Party", "qualifier": None}]
        if not allowed_attrs or "ideal_for" in allowed_attrs:
            if "ideal_for" not in catalog_attrs:
                catalog_attrs["ideal_for"] = [{"value": "Men & Women", "qualifier": None}]

    if "warranty_summary" in allowed_attrs and "warranty_summary" not in catalog_attrs:
        catalog_attrs["warranty_summary"] = [{"value": "1 Year Manufacturer Warranty", "qualifier": None}]
    if "warranty_service_type" in allowed_attrs and "warranty_service_type" not in catalog_attrs:
        catalog_attrs["warranty_service_type"] = [{"value": "Customer Support", "qualifier": None}]

    # 通用必填项兜底
    clean_model_title = store_title
    brands_to_clean = {target_brand, source_brand, get_setting_val(db, "default_brand", settings.DEFAULT_BRAND), "Beishi"}
    for b_to_clean in brands_to_clean:
        if b_to_clean:
            clean_model_title = re.sub(rf'^\s*{re.escape(b_to_clean)}\s*[\'’s]*\s*[-_:]*\s*', '', clean_model_title, flags=re.I)
            clean_model_title = re.sub(rf'\b{re.escape(b_to_clean)}\b', '', clean_model_title, flags=re.I).strip(' -_,:;')
    for ip in PROTECTED_ENTERTAINMENT_IPS:
        clean_model_title = re.sub(rf'\b{re.escape(ip)}\b', 'Party', clean_model_title, flags=re.I).strip(' -_,:;')

    smart_defaults = {
        "model_name": truncate_title_safely(clean_model_title or f"Standard {vertical}", 120),
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

    if allowed_attrs and "model_number" in allowed_attrs:
        raw_mn = clean_model_title or f"STD-{product.id}"
        catalog_attrs["model_number"] = [{"value": raw_mn[:250], "qualifier": None}]

    # 变体专有属性精确覆盖
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
                val_s, q_s = format_attribute_value_and_qualifier(cap_k, cap_val, None, def_item, brand=brand)
                catalog_attrs[cap_k] = [{"value": val_s, "qualifier": q_s}]

    # 针对 Makro 草稿报错中指明的缺失字段补充
    missing_attrs = []
    if draft_resp and isinstance(draft_resp, dict):
        attr_errs = draft_resp.get("errorDetails", {}).get("catalogErrors", {}).get("systemValidationErrors", {}).get("attributeErrors", {})
        missing_attrs = list(attr_errs.keys())

    for missing_k in missing_attrs:
        if missing_k not in catalog_attrs and missing_k in allowed_attrs:
            def_item = allowed_attrs[missing_k]
            allowed_vals = [x.strip() for x in (def_item.get("allowedValues") or "").split("||") if x.strip()]
            val = allowed_vals[0] if allowed_vals else (def_item.get("exampleValue") or "Standard")

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

            val_s, q_s = format_attribute_value_and_qualifier(missing_k, val, qual, def_item, brand=brand)
            catalog_attrs[missing_k] = [{"value": val_s, "qualifier": q_s}]

    # 全量清洗与校验所有属性的值与 Qualifier
    for attr_k, attr_v_list in list(catalog_attrs.items()):
        if attr_k in allowed_attrs:
            def_item = allowed_attrs[attr_k]
            if attr_v_list and isinstance(attr_v_list, list) and len(attr_v_list) > 0:
                cur_val = attr_v_list[0].get("value")
                cur_qual = attr_v_list[0].get("qualifier")
                norm_val, norm_qual = format_attribute_value_and_qualifier(attr_k, cur_val, cur_qual, def_item, brand=brand)
                catalog_attrs[attr_k] = [{"value": norm_val, "qualifier": norm_qual}]

    # 全量属性文本清洗：杜绝受保护IP
    for attr_k, attr_v_list in catalog_attrs.items():
        if attr_v_list and isinstance(attr_v_list, list):
            for item in attr_v_list:
                v = item.get("value")
                if isinstance(v, str):
                    for ip in PROTECTED_ENTERTAINMENT_IPS:
                        if re.search(rf'\b{re.escape(ip)}\b', v, flags=re.I):
                            item["value"] = re.sub(rf'\b{re.escape(ip)}\b', 'Party', v, flags=re.I).strip()

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
