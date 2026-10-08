"""
Makro 待处理池商品数据全量补全与清洗工具
使用场景:
    生产端待处理池商品因网络或反爬拦截导致价格为0、缺少中文标题、品牌、MRP等数据时，
    调用此脚本进行批量抓取回填与重算定价。

使用方法:
    python backend/tools/backfill_piggyback_data.py [--dry-run] [--force-all] [--limit 50]
"""
import os
import sys
import time
import logging
import argparse
from datetime import datetime

# 自动将 backend 加入 sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.database import SessionLocal
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.store import Store
from app.services.makro_scraper_service import MakroScraperService
from app.services.makro_piggyback_service import MakroPiggybackService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("backfill_piggyback")

def backfill_pending_items(dry_run: bool = False, force_all: bool = False, limit: int = 0):
    db = SessionLocal()
    try:
        query = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.status == 'PENDING')
        if not force_all:
            query = query.filter(
                (MakroPiggybackItem.original_price == 0.0) |
                (MakroPiggybackItem.original_price == None) |
                (MakroPiggybackItem.original_mrp == 0.0) |
                (MakroPiggybackItem.original_mrp == None) |
                (MakroPiggybackItem.title_zh == None) |
                (MakroPiggybackItem.title_zh == '')
            )
        
        query = query.order_by(MakroPiggybackItem.id.asc())
        if limit > 0:
            query = query.limit(limit)

        items = query.all()
        total_count = len(items)
        logger.info(f"===> 待清洗/补全商品总量: {total_count} 条 (模式: {'Dry-Run (仅预览)' if dry_run else 'Live (真实更新)'})")

        if total_count == 0:
            logger.info("未发现需要补全的待处理商品，处理完毕。")
            return

        success_count = 0
        fail_count = 0

        # 预先获取店铺缓存
        stores_cache = {s.id: s for s in db.query(Store).all()}
        default_store = next((s for s in stores_cache.values() if s.is_default), None) or (list(stores_cache.values())[0] if stores_cache else None)

        for idx, it in enumerate(items, 1):
            logger.info(f"[{idx}/{total_count}] 开始补全 ID: {it.id} | FSN: {it.makro_product_id} | 当前价格: R{it.original_price}")
            
            store = stores_cache.get(it.store_id) or default_store
            client_hint = {
                "item_id": it.item_id,
                "image_url": it.image_url,
                "title": it.title,
                "brand": it.brand,
                "vertical": it.vertical,
                "model_number": it.model_number,
                "barcode": it.barcode
            }

            scraped = None
            last_err = None
            # 允许最多 2 次重试
            for retry in range(2):
                try:
                    scraped = MakroScraperService.resolve_piggyback_product(
                        url_or_fsn=it.makro_url or it.makro_product_id,
                        store=store,
                        client_data=client_hint
                    )
                    if scraped and (scraped.get("original_price") or 0.0) > 0:
                        break
                except Exception as e:
                    last_err = e
                    time.sleep(1.0)

            if not scraped or (scraped.get("original_price") or 0.0) <= 0:
                logger.warning(f"  ❌ ID {it.id} (FSN {it.makro_product_id}) 抓取补全失败: {last_err or '售价未能获取'}")
                fail_count += 1
                continue

            # 提取清洗回填值
            old_p = it.original_price
            old_mrp = it.original_mrp
            new_p = scraped["original_price"]
            new_mrp = scraped["original_mrp"]
            
            it.original_price = new_p
            it.original_mrp = new_mrp
            it.original_seller = scraped.get("original_seller") or it.original_seller
            it.seller_count = scraped.get("seller_count") or it.seller_count or 1

            if scraped.get("title") and not scraped["title"].startswith("Makro Product "):
                it.title = scraped["title"]
            if scraped.get("title_zh"):
                it.title_zh = scraped["title_zh"]
            if scraped.get("image_url"):
                it.image_url = scraped["image_url"]
            if scraped.get("brand") and scraped["brand"] != "Generic":
                it.brand = scraped["brand"]
            if scraped.get("vertical") and scraped["vertical"] != "general":
                it.vertical = scraped["vertical"]
            if scraped.get("model_number"):
                it.model_number = scraped["model_number"]
            if scraped.get("barcode"):
                it.barcode = scraped["barcode"]

            # 重新核算保本底价与跟卖目标价
            it.min_price_floor = MakroPiggybackService.calculate_default_floor(it.original_price, db)
            it.target_price, it.target_mrp = MakroPiggybackService.calculate_price(
                original_price=it.original_price,
                strategy=it.price_strategy or "MINUS_15",
                min_floor=it.min_price_floor,
                original_mrp=it.original_mrp
            )
            it.updated_at = datetime.now()

            logger.info(f"  ✅ 成功回填: 原价 R{old_p} -> R{new_p} | MRP R{old_mrp} -> R{new_mrp} | 底价: R{it.min_price_floor} | 建议跟卖: R{it.target_price} | 卖家: {it.original_seller}")
            success_count += 1

            # 实时每条或小批量保存
            if not dry_run:
                db.commit()

        logger.info(f"\n==========================================")
        logger.info(f"清洗完成! 总扫描: {total_count} 条 | 成功回填: {success_count} 条 | 失败: {fail_count} 条")
        logger.info(f"==========================================")

    finally:
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Makro 待处理池商品数据回填工具")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，仅抓取比对不写入数据库")
    parser.add_argument("--force-all", action="store_true", help="强制全量扫描所有 PENDING 商品，不仅限于缺失商品")
    parser.add_argument("--limit", type=int, default=0, help="限制处理条数，默认全部")
    args = parser.parse_args()

    backfill_pending_items(dry_run=args.dry_run, force_all=args.force_all, limit=args.limit)
