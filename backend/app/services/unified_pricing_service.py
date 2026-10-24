import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from ..models.store import Store
from ..models.makro_listing import MakroListing
from ..models.store_product_reprice import StoreProductReprice
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.makro_reprice_log import MakroRepriceLog
from .auto_reprice_service import AutoRepriceService

logger = logging.getLogger(__name__)


class UnifiedPricingService:
    @classmethod
    def adjust_single_price(
        cls,
        store: Store,
        db: Session,
        new_price: float,
        listing_id: Optional[int] = None,
        reprice_id: Optional[int] = None,
        seller_sku: Optional[str] = None,
        sync_makro: bool = True,
        force: bool = False,
        operator: str = "MANUAL"
    ) -> Dict[str, Any]:
        """
        统一调整单品售价，并三表级联双向同步：
        1. MakroListing.ssp
        2. StoreProductReprice (跟价底册)
        3. MakroPiggybackItem.target_price (跟品池)
        4. 推送到 Makro 官方 Listing 网关
        5. 写入调价流水日志并清空 KPI 缓存
        """
        # 1. 唯一定位 MakroListing
        listing = None
        if listing_id:
            listing = db.query(MakroListing).filter(MakroListing.id == listing_id, MakroListing.store_id == store.id).first()
        elif reprice_id:
            rep = db.query(StoreProductReprice).filter(StoreProductReprice.id == reprice_id, StoreProductReprice.store_id == store.id).first()
            if rep:
                listing = rep.listing or db.query(MakroListing).filter(MakroListing.id == rep.listing_id).first()
        elif seller_sku:
            listing = db.query(MakroListing).filter(MakroListing.sku_id == seller_sku, MakroListing.store_id == store.id).first()

        if not listing:
            return {"success": False, "message": "未找到对应的店铺商品 (Listing)"}

        sku = listing.sku_id
        old_price = float(listing.ssp or 0.0)
        new_price = round(float(new_price), 2)
        if new_price <= 0:
            return {"success": False, "message": "售价必须大于 0"}

        # 2. 定位关联的跟价记录与跟品池记录
        reprice = db.query(StoreProductReprice).filter(
            StoreProductReprice.store_id == store.id,
            StoreProductReprice.listing_id == listing.id
        ).first()
        if not reprice:
            reprice = db.query(StoreProductReprice).filter(
                StoreProductReprice.store_id == store.id,
                StoreProductReprice.seller_sku == sku
            ).first()

        piggyback = None
        if reprice and reprice.source_piggyback_id:
            piggyback = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == reprice.source_piggyback_id).first()
        if not piggyback:
            piggyback = db.query(MakroPiggybackItem).filter(
                MakroPiggybackItem.store_id == store.id,
                MakroPiggybackItem.seller_sku == sku
            ).first()

        # 3. 底价防穿风控校验
        min_floor = 0.0
        if reprice and reprice.min_price_floor and reprice.min_price_floor > 0:
            min_floor = float(reprice.min_price_floor)
        elif piggyback and piggyback.min_price_floor and piggyback.min_price_floor > 0:
            min_floor = float(piggyback.min_price_floor)

        if min_floor > 0 and new_price < min_floor and not force:
            return {
                "success": False,
                "below_floor": True,
                "min_price_floor": min_floor,
                "current_price": old_price,
                "new_price": new_price,
                "message": f"修改售价 R{new_price:.2f} 低于该商品设定的保本底价 R{min_floor:.2f}，可能导致亏损，请确认是否强制改价？"
            }

        # 4. 推送到 Makro 官方网关
        if sync_makro:
            item_proxy = reprice or piggyback or listing
            try:
                AutoRepriceService._push_price_to_makro(
                    item=item_proxy,
                    store=store,
                    new_price=new_price,
                    listing=listing
                )
            except Exception as e:
                logger.error(f"推送 Makro 官方售价失败 [{sku}]: {e}")
                return {
                    "success": False,
                    "makro_error": True,
                    "message": f"推送到 Makro 官方网关失败: {str(e)}"
                }

        # 5. 三表级联数据更新
        now = datetime.now()
        listing.ssp = new_price
        listing.updated_at = now

        if reprice:
            reprice.updated_at = now
            if reprice.current_competitor_price and reprice.current_competitor_price > 0:
                if new_price <= reprice.current_competitor_price:
                    reprice.buybox_status = "WINNING"
                else:
                    reprice.buybox_status = "LOSING"

        if piggyback:
            piggyback.target_price = new_price
            piggyback.updated_at = now
            if piggyback.last_competitor_price and piggyback.last_competitor_price > 0:
                if new_price <= piggyback.last_competitor_price:
                    piggyback.buybox_status = "WINNING"
                else:
                    piggyback.buybox_status = "LOSING"

        # 6. 记录调价流水日志
        try:
            fsn = listing.product_id or (reprice.makro_product_id if reprice else "") or (piggyback.makro_product_id if piggyback else "")
            log = MakroRepriceLog(
                store_id=store.id,
                piggyback_id=piggyback.id if piggyback else None,
                reprice_id=reprice.id if reprice else None,
                seller_sku=sku,
                makro_product_id=fsn,
                old_price=old_price or 0.0,
                new_price=new_price,
                action="MANUAL_ADJUST",
                reason=f"手动直接改价 ({operator})" + (" (强制破底)" if force else ""),
                created_at=now
            )
            db.add(log)
        except Exception as log_err:
            logger.warning(f"记录调价日志失败: {log_err}")

        db.commit()

        # 7. 主动清理 KPI 短时内存缓存
        try:
            from ..api.reprice import invalidate_reprice_kpi_cache
            from ..api.piggyback import invalidate_kpi_cache
            invalidate_reprice_kpi_cache()
            invalidate_kpi_cache()
        except Exception:
            pass

        return {

            "success": True,
            "seller_sku": sku,
            "old_price": old_price,
            "new_price": new_price,
            "min_price_floor": min_floor,
            "message": f"商品 [{sku}] 售价已成功调整为 R{new_price:.2f}，并已同步官方网关及所有业务模块"
        }

    @classmethod
    def batch_adjust_prices(
        cls,
        store: Store,
        db: Session,
        items: List[Dict[str, Any]], # [{"listing_id": 1, "reprice_id": 2, "seller_sku": "xxx", "new_price": 120.0}]
        sync_makro: bool = True,
        force: bool = False,
        operator: str = "BATCH_MANUAL"
    ) -> Dict[str, Any]:
        """
        批量调整售价，支持底价检查与多品三表同步
        """
        success_count = 0
        failed_count = 0
        below_floor_items = []
        errors = []

        for it in items:
            l_id = it.get("listing_id")
            r_id = it.get("reprice_id")
            sku = it.get("seller_sku")
            p = it.get("new_price")
            if p is None:
                continue

            res = cls.adjust_single_price(
                store=store,
                db=db,
                new_price=float(p),
                listing_id=l_id,
                reprice_id=r_id,
                seller_sku=sku,
                sync_makro=sync_makro,
                force=force,
                operator=operator
            )

            if res.get("success"):
                success_count += 1
            else:
                if res.get("below_floor"):
                    below_floor_items.append({
                        "seller_sku": res.get("seller_sku") or sku,
                        "min_price_floor": res.get("min_price_floor"),
                        "new_price": p
                    })
                failed_count += 1
                errors.append(f"[{sku or l_id or r_id}]: {res.get('message')}")

        return {
            "success": success_count > 0 or (failed_count == 0 and len(items) == 0),
            "total": len(items),
            "success_count": success_count,
            "failed_count": failed_count,
            "below_floor_items": below_floor_items,
            "errors": errors[:10],
            "message": f"批量调价完成：成功 {success_count} 件，失败 {failed_count} 件"
        }
