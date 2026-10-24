import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.database import SessionLocal
from backend.app.models.makro_piggyback import MakroPiggybackItem
from backend.app.models.makro_listing import MakroListing
from backend.app.models.store_product_reprice import StoreProductReprice
from backend.app.models.store import Store
from backend.app.services.makro_piggyback_service import MakroPiggybackService
from backend.app.services.proxy_service import ProxyPoolService

def fix_55_items():
    db = SessionLocal()
    try:
        # 1. 查找合规但挂靠失败/报错的所有商品
        items = db.query(MakroPiggybackItem).filter(
            MakroPiggybackItem.compliance_status == "SAFE",
            MakroPiggybackItem.status.in_(["FAILED", "ERROR", "SUBMITTING"])
        ).all()
        
        print(f"找到 {len(items)} 件合规但未成功商品...")
        
        auto_bound_count = 0
        gated_count = 0
        
        GATED_VERTICALS = {
            "brush_applicator", "baking_decorative", "kitchen_roll",
            "electric_insect_killer", "garbage_bag", "insect_repellent", "air_freshener"
        }
        
        for item in items:
            store = db.query(Store).filter(Store.id == item.store_id).first()
            if not store:
                continue
                
            # 检查是否有现成的 Listing (包括 ID 13600, 16811 等)
            existing_listing = db.query(MakroListing).filter(
                MakroListing.store_id == store.id,
                MakroListing.product_id == item.makro_product_id
            ).first()
            
            # 如果本地无记录，但错误是 SKU is not allowed to be updated，或者已经是特定冲突商品
            if existing_listing:
                print(f"[自愈绑定] 商品 ID {item.id} (FSN: {item.makro_product_id}) 命中已有 Listing {existing_listing.listing_id}, 官方 SKU: {existing_listing.sku_id}")
                item.seller_sku = existing_listing.sku_id
                item.makro_listing_id = existing_listing.listing_id
                item.status = "ACTIVE"
                item.error_message = None
                
                # 更新 Listing 状态
                existing_listing.internal_state = "ACTIVE"
                existing_listing.title = item.title
                existing_listing.brand = item.brand
                existing_listing.vertical = item.vertical
                existing_listing.image_url = item.image_url
                existing_listing.ssp = item.target_price or existing_listing.ssp
                existing_listing.synced_at = datetime.now()
                
                # 建立/关联跟价监控
                reprice = db.query(StoreProductReprice).filter(
                    StoreProductReprice.listing_id == existing_listing.id
                ).first()
                if not reprice:
                    reprice = StoreProductReprice(
                        store_id=store.id,
                        listing_id=existing_listing.id,
                        seller_sku=existing_listing.sku_id,
                        makro_product_id=item.makro_product_id
                    )
                    db.add(reprice)
                reprice.source_type = "PIGGYBACK"
                reprice.source_piggyback_id = item.id
                reprice.target_fsn = item.makro_product_id
                reprice.target_makro_url = item.makro_url
                reprice.auto_reprice = bool(item.auto_reprice)
                reprice.price_strategy = item.price_strategy or "MINUS_1"
                reprice.min_price_floor = float(item.min_price_floor or 0.0)
                reprice.max_price_ceiling = float(item.max_price_ceiling or 0.0)
                reprice.compliance_status = "SAFE"
                reprice.monitor_status = "ACTIVE"
                
                # 同步向 Makro 发起库存与激活请求
                try:
                    payload = {
                        "bulkRequests": [
                            MakroPiggybackService._build_listing_bulk_request(item, store, listing_status="ACTIVE")
                        ],
                        "sellerId": store.seller_id
                    }
                    headers = MakroPiggybackService._build_headers(store)
                    create_url = f"https://seller.makro.co.za/napi/listing/create-update-listings?sellerId={store.seller_id}"
                    ProxyPoolService.resilient_request("POST", create_url, headers=headers, json=payload, timeout=20, use_proxy=False)
                    
                    target_loc = getattr(item, "location_id", None) or store.default_location_id
                    if target_loc:
                        inv_url = f"https://seller.makro.co.za/napi/sfx/updateListingsInventory?sellerId={store.seller_id}&locationId={target_loc}"
                        inv_headers = MakroPiggybackService._build_headers(store, {"x-location-id": target_loc})
                        inv_payload = {
                            existing_listing.sku_id: {
                                "product_id": item.makro_product_id,
                                "locations": [{"id": target_loc, "inventory": int(item.inventory or 99)}]
                            }
                        }
                        ProxyPoolService.resilient_request("POST", inv_url, headers=inv_headers, json=inv_payload, timeout=20, use_proxy=False)
                except Exception as sync_e:
                    print(f"  [提示] 向 Makro 激活同步非致命警告: {sync_e}")
                
                auto_bound_count += 1
                db.commit()
                continue
                
            # 类别一：53 件资质受限商品
            is_license_error = "license" in str(item.error_message).lower()
            is_gated_vertical = item.vertical in GATED_VERTICALS
            if is_license_error or is_gated_vertical:
                vert_desc = item.vertical or "受限类目"
                item.error_message = f"平台类目资质受限: 需在 Makro 卖家后台申请提交对应类目营业执照/许可证 (Vertical: {vert_desc})"
                item.status = "FAILED"
                gated_count += 1
                db.commit()
                
        print(f"处理完成: 成功自愈绑定已有 Listing {auto_bound_count} 件，规范化标记类目资质受限 {gated_count} 件。")
        
    finally:
        db.close()

if __name__ == "__main__":
    fix_55_items()
