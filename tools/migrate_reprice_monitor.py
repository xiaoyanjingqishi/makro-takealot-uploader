import sys
import os
import logging

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.database import SessionLocal
from backend.app.models.store import Store
from backend.app.models.makro_listing import MakroListing
from backend.app.models.makro_piggyback import MakroPiggybackItem
from backend.app.models.store_product_reprice import StoreProductReprice
from backend.app.models.makro_reprice_log import MakroRepriceLog
from backend.app.models.product import Product

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MigrateRepriceMonitor")

def run_migration():
    db = SessionLocal()
    try:
        logger.info("=== 开始执行在售跟价监控数据解耦与底册迁移 ===")

        # 1. 查询所有有效店铺
        stores = db.query(Store).all()
        logger.info(f"共发现 {len(stores)} 个店铺配置")

        total_created = 0
        total_matched_piggy = 0
        total_self_published = 0
        total_store_synced = 0

        for store in stores:
            active_listings = db.query(MakroListing).filter(
                MakroListing.store_id == store.id,
                MakroListing.internal_state == "ACTIVE"
            ).all()

            if not active_listings:
                logger.info(f"店铺 [{store.name}] (ID: {store.id}) 暂无 ACTIVE 官方在线商品，跳过")
                continue

            logger.info(f"正在处理店铺 [{store.name}] (ID: {store.id}): 共有 {len(active_listings)} 件 ACTIVE 在售商品...")

            # 建立已有跟品记录字典加速比对: sku -> MakroPiggybackItem
            piggy_items = db.query(MakroPiggybackItem).filter(
                MakroPiggybackItem.store_id == store.id
            ).all()
            piggy_map = {p.seller_sku: p for p in piggy_items if p.seller_sku}

            # 建立自建商品库 SKU 集合加速比对
            from backend.app.models.product import ProductVariant
            self_sku_set = set(p[0] for p in db.query(ProductVariant.sku_id).filter(ProductVariant.sku_id.isnot(None)).all())
            self_sku_set.update(p[0] for p in db.query(ProductVariant.makro_sku_id).filter(ProductVariant.makro_sku_id.isnot(None)).all())

            # 遍历该店铺每一个官方在售 Listing
            store_created = 0
            store_matched = 0
            store_diff = 0

            for lst in active_listings:
                # 检查是否已建监控底册
                existing_rep = db.query(StoreProductReprice).filter(
                    StoreProductReprice.listing_id == lst.id
                ).first()

                if not existing_rep:
                    existing_rep = StoreProductReprice(
                        store_id=store.id,
                        listing_id=lst.id,
                        seller_sku=lst.sku_id,
                        makro_product_id=lst.product_id or ""
                    )
                    db.add(existing_rep)
                    store_created += 1

                # 匹配已有跟品配置
                piggy = piggy_map.get(lst.sku_id)
                if piggy:
                    existing_rep.source_type = "PIGGYBACK"
                    existing_rep.source_piggyback_id = piggy.id
                    existing_rep.target_fsn = piggy.makro_product_id or lst.product_id
                    existing_rep.target_makro_url = piggy.makro_url
                    existing_rep.auto_reprice = bool(piggy.auto_reprice)
                    existing_rep.price_strategy = piggy.price_strategy or "MINUS_1"
                    existing_rep.min_price_floor = float(piggy.min_price_floor or 0.0)
                    existing_rep.max_price_ceiling = float(piggy.max_price_ceiling or 0.0)
                    existing_rep.buybox_status = piggy.buybox_status or "UNKNOWN"
                    existing_rep.current_competitor_price = piggy.last_competitor_price or piggy.original_price or 0.0
                    existing_rep.current_competitor_seller = piggy.original_seller
                    existing_rep.seller_count = piggy.seller_count or 1
                    existing_rep.compliance_status = piggy.compliance_status or "SAFE"
                    existing_rep.compliance_details = piggy.compliance_details
                    existing_rep.last_reprice_at = piggy.last_reprice_at
                    existing_rep.last_reprice_result = piggy.last_reprice_result
                    existing_rep.monitor_status = "ACTIVE"
                    store_matched += 1
                    total_matched_piggy += 1
                else:
                    # 检查是否为选品箱自建商品
                    if lst.local_product_id or lst.sku_id in self_sku_set:
                        existing_rep.source_type = "SELF_PUBLISHED"
                        total_self_published += 1
                    else:
                        existing_rep.source_type = "STORE_SYNCED"
                        total_store_synced += 1

                    existing_rep.target_fsn = lst.product_id
                    existing_rep.auto_reprice = False
                    existing_rep.price_strategy = "MINUS_1"
                    existing_rep.min_price_floor = 0.0
                    existing_rep.max_price_ceiling = 0.0
                    existing_rep.buybox_status = "UNKNOWN"
                    existing_rep.compliance_status = "SAFE"
                    existing_rep.monitor_status = "PENDING_CONFIG"
                    store_diff += 1

            db.commit()
            total_created += store_created
            logger.info(f"店铺 [{store.name}] 底册初始化完成: 新建/校验 {store_created} 条, 继承已有配置 {store_matched} 件, 标记待补齐竞对/未配策略 {store_diff} 件")

        # 2. 批量回填历史调价日志的 reprice_id
        logger.info("正在关联合并历史调价日志 (MakroRepriceLog) -> StoreProductReprice...")
        reprice_all = db.query(StoreProductReprice).all()
        rep_sku_map = {(r.store_id, r.seller_sku): r.id for r in reprice_all}

        logs_to_update = db.query(MakroRepriceLog).filter(MakroRepriceLog.reprice_id.is_(None)).all()
        updated_logs = 0
        for l in logs_to_update:
            rid = rep_sku_map.get((l.store_id, l.seller_sku))
            if rid:
                l.reprice_id = rid
                updated_logs += 1

        db.commit()
        logger.info(f"历史调价日志关联成功: 共更新 {updated_logs} 条日志记录的 reprice_id")

        logger.info("=== 迁移完成总结 ===")
        logger.info(f"底册总数: {db.query(StoreProductReprice).count()}")
        logger.info(f" - 来源 PIGGYBACK (跟品挂靠): {db.query(StoreProductReprice).filter(StoreProductReprice.source_type == 'PIGGYBACK').count()}")
        logger.info(f" - 来源 SELF_PUBLISHED (自建上品): {db.query(StoreProductReprice).filter(StoreProductReprice.source_type == 'SELF_PUBLISHED').count()}")
        logger.info(f" - 来源 STORE_SYNCED (店铺老品): {db.query(StoreProductReprice).filter(StoreProductReprice.source_type == 'STORE_SYNCED').count()}")
        logger.info(f" - 状态 ACTIVE (正常监控): {db.query(StoreProductReprice).filter(StoreProductReprice.monitor_status == 'ACTIVE').count()}")
        logger.info(f" - 状态 PENDING_CONFIG (待补齐竞对): {db.query(StoreProductReprice).filter(StoreProductReprice.monitor_status == 'PENDING_CONFIG').count()}")

    except Exception as e:
        db.rollback()
        logger.error(f"迁移异常中断: {e}", exc_info=True)
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    run_migration()
