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
    makro_title: Optional[str] = None
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
