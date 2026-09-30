from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class CollectPiggybackRequest(BaseModel):
    url_or_fsn: str = Field(..., description="Makro 商品详情页链接或 FSN (如 GSPHPVTNMFHDAWV4)")
    store_id: Optional[int] = Field(None, description="目标跟品店铺 ID，若未填则使用默认店铺")
    price_strategy: Optional[str] = Field("MINUS_1", description="比价策略: MINUS_1(低1兰特), PERCENT_2(低2%), MANUAL(保持原价)")
    min_price_floor: Optional[float] = Field(0.0, description="保本安全底价")

class BatchCollectPiggybackRequest(BaseModel):
    items: List[str] = Field(..., description="多个 Makro 链接或 FSN 列表")
    store_id: Optional[int] = Field(None, description="目标跟品店铺 ID")
    price_strategy: Optional[str] = Field("MINUS_1", description="比价策略")
    min_price_floor: Optional[float] = Field(0.0, description="保本底价")

class PiggybackItemUpdate(BaseModel):
    seller_sku: Optional[str] = None
    target_price: Optional[float] = None
    target_mrp: Optional[float] = None
    min_price_floor: Optional[float] = None
    price_strategy: Optional[str] = None
    inventory: Optional[int] = None
    lead_time_days: Optional[int] = None
    store_id: Optional[int] = None
    weight: Optional[float] = None
    length: Optional[float] = None
    breadth: Optional[float] = None
    height: Optional[float] = None

class BatchApplyPricingRequest(BaseModel):
    ids: List[int] = Field(..., description="选中的跟品商品 ID 列表")
    price_strategy: str = Field(..., description="MINUS_1, PERCENT_2, MANUAL, COST_PLUS")
    min_price_floor: Optional[float] = None
    custom_delta: Optional[float] = Field(None, description="自定义下浮或上浮金额/百分比")

class BatchCheckComplianceRequest(BaseModel):
    ids: List[int] = Field(..., description="需要批量执行 AI 侵权检测的商品 ID 列表")

class BatchPublishPiggybackRequest(BaseModel):
    ids: List[int] = Field(..., description="需要批量挂靠上架的跟品商品 ID 列表")

class BatchDeletePiggybackRequest(BaseModel):
    ids: List[int] = Field(..., description="需要批量删除的跟品商品 ID 列表")

class PiggybackItemResponse(BaseModel):
    id: int
    store_id: int
    store_name: Optional[str] = None
    user_id: Optional[int] = None
    makro_product_id: str
    makro_url: Optional[str] = None
    title: str
    title_zh: Optional[str] = None
    brand: Optional[str] = None
    vertical: Optional[str] = None
    image_url: Optional[Text] if False else Optional[str] = None
    barcode: Optional[str] = None
    model_number: Optional[str] = None
    original_price: float
    original_mrp: float
    seller_sku: str
    target_price: float
    target_mrp: float
    min_price_floor: float
    price_strategy: str
    inventory: int
    lead_time_days: int
    weight: Optional[float] = None
    length: Optional[float] = None
    breadth: Optional[float] = None
    height: Optional[float] = None
    compliance_status: str
    compliance_details: Optional[Dict[str, Any]] = None
    status: str
    makro_listing_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {
        "from_attributes": True,
        "protected_namespaces": ()
    }
