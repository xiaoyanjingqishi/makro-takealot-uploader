# -*- coding: utf-8 -*-
"""
跟卖全流程业务服务：负责单品/批量抓取入库、合规质检、挂靠发布编排与状态流转
"""

import uuid
import time
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session

from app.models.makro_piggyback import MakroPiggybackItem
from app.models.makro_listing import MakroListing
from app.models.store import Store
from app.models.user import User
from app.services.makro_scraper_service import MakroScraperService
from app.services.makro_piggyback_service import MakroPiggybackService
from app.services.makro_portal_service import MakroPortalService
from app.services.audit_logger import record_audit_log

logger = logging.getLogger(__name__)


class PiggybackService:
    """
    跟卖领域业务门面服务
    """

    @staticmethod
    def generate_piggyback_sku() -> str:
        """自动生成规范且不重复的跟品 SKU，如 GP2609301234"""
        ts = datetime.now().strftime("%y%m%d%H%M%S")
        rand_suffix = str(uuid.uuid4().hex[:4]).upper()
        return f"GP{ts}{rand_suffix}"

    @staticmethod
    def get_target_store(db: Session, store_id: Optional[int] = None) -> Store:
        if store_id:
            store = db.query(Store).filter(Store.id == store_id).first()
            if store:
                return store
        store = db.query(Store).filter(Store.is_default == True, Store.is_active == True).first()
        if not store:
            store = db.query(Store).filter(Store.is_active == True).first()
        if not store:
            raise ValueError("系统未找到任何可用店铺，请先在多店铺管理中添加店铺凭据。")
        return store

    @staticmethod
    def format_piggyback_item(item: MakroPiggybackItem) -> dict:
        comp_details = None
        if item.compliance_details:
            try:
                comp_details = json.loads(item.compliance_details)
            except Exception:
                pass

        op_name = None
        if getattr(item, "creator", None):
            op_name = item.creator.nickname or item.creator.username
        elif getattr(item, "user", None):
            op_name = item.user.nickname or item.user.username

        return {
            "id": item.id,
            "store_id": item.store_id,
            "store_name": item.store.name if item.store else "未知店铺",
            "user_id": item.user_id,
            "operator_name": op_name,
            "makro_product_id": item.makro_product_id,
            "item_id": item.item_id,
            "makro_url": item.makro_url or MakroScraperService.format_canonical_makro_url(item.makro_product_id, item.item_id),
            "title": item.title,
            "title_zh": item.title_zh,
            "brand": item.brand,
            "vertical": item.vertical,
            "image_url": item.image_url,
            "barcode": item.barcode,
            "model_number": item.model_number,
            "original_price": item.original_price or 0.0,
            "original_mrp": item.original_mrp or 0.0,
            "original_seller": item.original_seller or "",
            "seller_count": item.seller_count or 1,
            "seller_sku": item.seller_sku,
            "target_price": item.target_price or 0.0,
            "target_mrp": item.target_mrp or 0.0,
            "min_price_floor": item.min_price_floor or 0.0,
            "max_price_ceiling": item.max_price_ceiling or 0.0,
            "auto_reprice": item.auto_reprice if item.auto_reprice is not None else True,
            "last_reprice_at": item.last_reprice_at.strftime("%Y-%m-%d %H:%M:%S") if item.last_reprice_at else None,
            "last_reprice_result": item.last_reprice_result or "",
            "buybox_status": item.buybox_status or "UNKNOWN",
            "last_competitor_price": item.last_competitor_price,
            "price_strategy": item.price_strategy or "MINUS_15",
            "inventory": item.inventory or 500,
            "lead_time_days": item.lead_time_days or 14,
            "variant_attributes": item.variant_attributes,
            "variant_name": item.variant_name or "",
            "weight": item.weight or 0.5,
            "length": item.length or 15.0,
            "breadth": item.breadth or 10.0,
            "height": item.height or 5.0,
            "compliance_status": item.compliance_status or "PENDING_CHECK",
            "compliance_details": comp_details,
            "status": item.status or "PENDING",
            "makro_listing_id": item.makro_listing_id,
            "error_message": item.error_message,
            "is_abandoned": bool(item.is_abandoned),
            "abandoned_reason": item.abandoned_reason or "",
            "abandoned_at": item.abandoned_at.strftime("%Y-%m-%d %H:%M:%S") if item.abandoned_at else None,
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else None,
            "updated_at": item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else None
        }
