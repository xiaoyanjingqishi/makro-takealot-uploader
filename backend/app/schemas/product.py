from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class VariantCreate(BaseModel):
    sku_id: Optional[str] = None
    takealot_variant_id: Optional[str] = None
    variant_title: Optional[str] = None
    variant_attributes: Optional[Dict[str, Any]] = None
    specs: Optional[Dict[str, Any]] = None
    barcode: Optional[str] = None
    size: Optional[str] = None
    colour: Optional[str] = None
    brand_colour: Optional[str] = None
    pack_of: Optional[str] = "1"
    takealot_price: float = 0.0
    images: List[str] = []

class TakealotCollectRequest(BaseModel):
    takealot_id: Optional[str] = None
    takealot_url: str
    takealot_title: str
    takealot_price: float = 0.0
    takealot_brand: Optional[str] = None
    takealot_category: Optional[str] = None
    takealot_description: Optional[str] = None
    takealot_specs: Optional[Dict[str, Any]] = None
    raw_images: List[str] = []
    variants: List[VariantCreate] = []
    user_id: Optional[int] = None
    collector_username: Optional[str] = None

class ProductVariantResponse(BaseModel):
    id: int
    sku_id: str
    takealot_variant_id: Optional[str] = None
    variant_title: Optional[str] = None
    variant_attributes: Optional[Dict[str, Any]] = None
    specs: Optional[Dict[str, Any]] = None
    barcode: Optional[str] = None
    size: Optional[str] = None
    colour: Optional[str] = None
    brand_colour: Optional[str] = None
    pack_of: Optional[str] = "1"
    takealot_price: float
    makro_selling_price: float
    makro_mrp: float
    images: List[str] = []
    makro_image_urls: List[str] = []
    status: str
    makro_request_id: Optional[str] = None
    makro_submit_error: Optional[str] = None

    class Config:
        from_attributes = True

class ProductResponse(BaseModel):
    id: int
    takealot_id: Optional[str] = None
    takealot_url: Optional[str] = None
    takealot_title: str
    takealot_price: float
    takealot_brand: Optional[str] = None
    takealot_category: Optional[str] = None
    takealot_description: Optional[str] = None
    takealot_specs: Optional[Dict[str, Any]] = None
    raw_images: List[str] = []
    
    status: str
    makro_vertical: Optional[str] = "bath_towel"
    makro_vertical_zh: Optional[str] = None
    makro_title: Optional[str] = None
    takealot_title_zh: Optional[str] = None
    makro_title_zh: Optional[str] = None
    seo_keywords: Optional[List[str]] = []
    clean_mode: Optional[str] = "text"
    makro_description: Optional[str] = None
    makro_brand: Optional[str] = "Beishi"
    makro_selling_price: float
    makro_mrp: float
    
    makro_catalog_attributes: Optional[Dict[str, Any]] = None
    makro_listing_attributes: Optional[Dict[str, Any]] = None
    makro_package_dimensions: Optional[Dict[str, Any]] = None
    makro_images: Optional[Dict[str, str]] = None
    
    group_code: Optional[str] = None
    sku_id: Optional[str] = None
    barcode: Optional[str] = None
    variant_attributes: Optional[Dict[str, Any]] = None
    colour: Optional[str] = None
    size: Optional[str] = None
    brand_colour: Optional[str] = None
    pack_of: Optional[str] = "1"
    makro_sku_id: Optional[str] = None
    makro_request_id: Optional[str] = None
    makro_submit_error: Optional[str] = None
    compliance_status: Optional[str] = "PENDING_CHECK"
    compliance_details: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    variants: List[ProductVariantResponse] = []
    store_listings: List[Dict[str, Any]] = []

    class Config:
        from_attributes = True

class ProductUpdateRequest(BaseModel):
    makro_vertical: Optional[str] = None
    makro_title: Optional[str] = None
    takealot_title_zh: Optional[str] = None
    makro_title_zh: Optional[str] = None
    makro_brand: Optional[str] = None
    makro_selling_price: Optional[float] = None
    makro_mrp: Optional[float] = None
    makro_description: Optional[str] = None
    makro_catalog_attributes: Optional[Dict[str, Any]] = None
    makro_package_dimensions: Optional[Dict[str, Any]] = None
    group_code: Optional[str] = None
    sku_id: Optional[str] = None
    barcode: Optional[str] = None
    variant_attributes: Optional[Dict[str, Any]] = None
    colour: Optional[str] = None
    size: Optional[str] = None
    brand_colour: Optional[str] = None
    pack_of: Optional[str] = None

class BatchCleanRequest(BaseModel):
    product_ids: List[int]
    ai_provider: Optional[str] = None  # 'qwen' or 'deepseek'
    clean_mode: Optional[str] = None   # 'text' or 'vision'
    concurrency: Optional[int] = 20    # 并发工作线程数 (默认20线程并发)

class BatchPublishRequest(BaseModel):
    product_ids: List[int]
    store_id: Optional[int] = None
    publish_all_stores: Optional[bool] = False
    store_ids: Optional[List[int]] = None
    force: Optional[bool] = False
    concurrency: Optional[int] = None

class BatchDeleteRequest(BaseModel):
    product_ids: List[int]


def format_product_entity(p: Any) -> dict:
    """集中式商品详情实体序列化，保持与前端契约 100% 一致"""
    import json
    from ..services.translation_service import TranslationService

    specs = json.loads(p.takealot_specs) if getattr(p, "takealot_specs", None) else {}
    raw_images = json.loads(p.raw_images) if getattr(p, "raw_images", None) else []
    cat_attrs = json.loads(p.makro_catalog_attributes) if getattr(p, "makro_catalog_attributes", None) else {}
    list_attrs = json.loads(p.makro_listing_attributes) if getattr(p, "makro_listing_attributes", None) else {}
    pkg_dims = json.loads(p.makro_package_dimensions) if getattr(p, "makro_package_dimensions", None) else {}
    makro_imgs = json.loads(p.makro_images) if getattr(p, "makro_images", None) else {}
    var_attrs = json.loads(p.variant_attributes) if getattr(p, "variant_attributes", None) else {}

    variants_data = []
    if hasattr(p, "variants") and p.variants:
        for v in p.variants:
            v_imgs = json.loads(v.images) if v.images else []
            v_makro_imgs = json.loads(v.makro_image_urls) if v.makro_image_urls else []
            v_attrs = json.loads(v.variant_attributes) if v.variant_attributes else {}
            v_specs = json.loads(v.specs) if v.specs else {}
            variants_data.append({
                "id": v.id,
                "sku_id": v.sku_id,
                "takealot_variant_id": v.takealot_variant_id,
                "variant_title": v.variant_title,
                "variant_attributes": v_attrs,
                "specs": v_specs,
                "barcode": v.barcode,
                "size": v.size,
                "colour": v.colour,
                "brand_colour": v.brand_colour,
                "pack_of": v.pack_of,
                "takealot_price": v.takealot_price,
                "makro_selling_price": v.makro_selling_price,
                "makro_mrp": v.makro_mrp,
                "images": v_imgs,
                "makro_image_urls": v_makro_imgs,
                "status": v.status,
                "makro_request_id": v.makro_request_id,
                "makro_submit_error": v.makro_submit_error
            })

    store_listings_data = []
    if hasattr(p, "store_listings") and p.store_listings:
        for sl in p.store_listings:
            store_listings_data.append({
                "id": sl.id,
                "store_id": sl.store_id,
                "store_name": sl.store.name if sl.store else f"店铺#{sl.store_id}",
                "brand": getattr(sl, "brand", None) or (sl.store.default_brand if sl.store else "Beishi"),
                "status": sl.status,
                "makro_sku_id": sl.makro_sku_id,
                "makro_request_id": sl.makro_request_id,
                "makro_submit_error": sl.makro_submit_error,
                "selling_price": sl.selling_price,
                "mrp": sl.mrp,
                "submitted_at": sl.submitted_at
            })

    seo_kw = []
    if getattr(p, "seo_keywords", None):
        try:
            seo_kw = json.loads(p.seo_keywords) if isinstance(p.seo_keywords, str) else p.seo_keywords
        except Exception:
            seo_kw = []

    comp_parsed = json.loads(p.compliance_details) if getattr(p, "compliance_details", None) else None
    creator = getattr(p, "creator", None)
    return {
        "id": p.id,
        "takealot_id": p.takealot_id,
        "takealot_url": p.takealot_url,
        "takealot_title": p.takealot_title,
        "takealot_price": p.takealot_price,
        "takealot_brand": p.takealot_brand,
        "takealot_category": p.takealot_category,
        "takealot_description": p.takealot_description,
        "takealot_specs": specs,
        "raw_images": raw_images,
        "status": p.status,
        "previous_status": getattr(p, "previous_status", None),
        "makro_vertical": p.makro_vertical,
        "makro_vertical_zh": TranslationService.get_vertical_zh(p.makro_vertical) if p.makro_vertical else None,
        "makro_title": p.makro_title,
        "takealot_title_zh": getattr(p, "takealot_title_zh", None),
        "makro_title_zh": getattr(p, "makro_title_zh", None),
        "seo_keywords": seo_kw,
        "clean_mode": getattr(p, "clean_mode", "text") or "text",
        "makro_description": p.makro_description,
        "makro_brand": p.makro_brand,
        "makro_selling_price": p.makro_selling_price,
        "makro_mrp": p.makro_mrp,
        "makro_catalog_attributes": cat_attrs,
        "makro_listing_attributes": list_attrs,
        "makro_package_dimensions": pkg_dims,
        "makro_images": makro_imgs,
        "group_code": p.group_code,
        "sku_id": p.sku_id,
        "barcode": p.barcode,
        "variant_attributes": var_attrs,
        "size": p.size,
        "colour": p.colour,
        "brand_colour": p.brand_colour,
        "pack_of": p.pack_of,
        "makro_sku_id": p.makro_sku_id,
        "makro_request_id": p.makro_request_id,
        "makro_submit_error": p.makro_submit_error,
        "compliance_status": p.compliance_status or "PENDING_CHECK",
        "compliance_details": comp_parsed,
        "brand_nature": comp_parsed.get("brand_nature") if isinstance(comp_parsed, dict) else None,
        "target_compatible_brand": comp_parsed.get("target_compatible_brand") if isinstance(comp_parsed, dict) else None,
        "user_id": p.user_id,
        "creator_name": (creator.nickname or creator.username) if creator else "未分配",
        "creator_username": creator.username if creator else None,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "variants": variants_data,
        "store_listings": store_listings_data
    }


def format_product_summary_entity(p: Any) -> dict:
    """列表高性能精简序列化：大幅缩减 90% 数据传输体积与 Vue 渲染开销"""
    import json
    from ..services.translation_service import TranslationService

    raw_images = json.loads(p.raw_images) if getattr(p, "raw_images", None) else []
    var_attrs = json.loads(p.variant_attributes) if getattr(p, "variant_attributes", None) else {}

    store_listings_data = []
    if hasattr(p, "store_listings") and p.store_listings:
        for sl in p.store_listings:
            store_listings_data.append({
                "id": sl.id,
                "store_id": sl.store_id,
                "store_name": sl.store.name if sl.store else f"店铺#{sl.store_id}",
                "brand": getattr(sl, "brand", None) or (sl.store.default_brand if sl.store else "Beishi"),
                "status": sl.status,
                "makro_sku_id": sl.makro_sku_id,
                "makro_request_id": sl.makro_request_id,
                "makro_submit_error": sl.makro_submit_error,
                "selling_price": sl.selling_price,
                "mrp": sl.mrp,
                "submitted_at": sl.submitted_at
            })

    comp_details = None
    if getattr(p, "compliance_details", None):
        try:
            cd = json.loads(p.compliance_details) if isinstance(p.compliance_details, str) else p.compliance_details
            if cd:
                comp_details = cd
        except Exception:
            pass

    creator = getattr(p, "creator", None)
    return {
        "id": p.id,
        "takealot_id": p.takealot_id,
        "takealot_url": p.takealot_url,
        "takealot_title": p.takealot_title,
        "takealot_price": p.takealot_price,
        "takealot_brand": p.takealot_brand,
        "takealot_category": p.takealot_category,
        "raw_images": raw_images[:3],
        "status": p.status,
        "previous_status": getattr(p, "previous_status", None),
        "makro_vertical": p.makro_vertical,
        "makro_vertical_zh": TranslationService.get_vertical_zh(p.makro_vertical) if p.makro_vertical else None,
        "makro_title": p.makro_title,
        "takealot_title_zh": getattr(p, "takealot_title_zh", None),
        "makro_title_zh": getattr(p, "makro_title_zh", None),
        "clean_mode": getattr(p, "clean_mode", "text") or "text",
        "makro_brand": p.makro_brand,
        "makro_selling_price": p.makro_selling_price,
        "makro_mrp": p.makro_mrp,
        "group_code": p.group_code,
        "sku_id": p.sku_id,
        "barcode": p.barcode,
        "variant_attributes": var_attrs,
        "size": p.size,
        "colour": p.colour,
        "brand_colour": p.brand_colour,
        "pack_of": p.pack_of,
        "makro_sku_id": p.makro_sku_id,
        "makro_request_id": p.makro_request_id,
        "makro_submit_error": p.makro_submit_error,
        "compliance_status": p.compliance_status or "PENDING_CHECK",
        "compliance_details": comp_details,
        "user_id": p.user_id,
        "creator_name": (creator.nickname or creator.username) if creator else "未分配",
        "creator_username": creator.username if creator else None,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "store_listings": store_listings_data
    }

