"""
一次性导入工具：将「Makro 旗舰主力店」(store_id=1) 和「Makro 2号测试店」(store_id=2)
的平台下架商品 (INACTIVATED_BY_FLIPKART / INACTIVE)，直接通过本地商品数据转换为跟品记录，
采集并归入店铺「hzh」(store_id=3) 的待处理池中。

配置规格：
- 目标店铺: hzh (store_id=3)
- 采集人员: 系统管理员 (user_id=1)
- 定价策略: 95折 (PERCENT_5)，目标售价 target_price = round(ssp * 0.95, 2)
- 默认库存: 500
- 状态设置: status='PENDING' (待处理池), compliance_status='PENDING_CHECK' (待质检)
- 保本底价: min_price_floor = round(ssp * 0.70, 2) (原价70%)

使用方法:
    python backend/tools/import_delisted_to_piggyback.py --dry-run
    python backend/tools/import_delisted_to_piggyback.py
"""
import os
import sys
import time
import uuid
import logging
import argparse
from datetime import datetime
from typing import Dict, Any, List, Set

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.database import SessionLocal
from app.models.makro_listing import MakroListing
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.store import Store
from app.models.user import User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("import_delisted_to_piggyback")


def generate_unique_sku(existing_skus: Set[str]) -> str:
    """生成唯一跟品 SKU"""
    while True:
        ts = datetime.now().strftime("%y%m%d%H%M%S")
        rand = uuid.uuid4().hex[:4].upper()
        sku = f"GP{ts}{rand}"
        if sku not in existing_skus:
            existing_skus.add(sku)
            return sku


def import_delisted_listings(dry_run: bool = False, chunk_size: int = 500):
    start_time = time.time()
    db = SessionLocal()

    try:
        # 1. 验证目标店铺与操作人
        target_store_id = 3
        target_user_id = 1

        store = db.query(Store).filter(Store.id == target_store_id).first()
        if not store:
            raise ValueError(f"目标店铺 (id={target_store_id}) 不存在！")

        user = db.query(User).filter(User.id == target_user_id).first()
        if not user:
            raise ValueError(f"操作用户 (id={target_user_id}) 不存在！")

        logger.info(f"== 目标店铺: [{store.id}] {store.name} (Seller ID: {store.seller_id}) ==")
        logger.info(f"== 归属用户: [{user.id}] {user.username} ({user.nickname}) ==")
        logger.info(f"== 运行模式: {'【预演 (DRY-RUN - 不写入数据库)】' if dry_run else '【正式执行 (COMMIT)】'} ==")

        # 2. 查询目标店铺中现存的 FSN 和 SKU，防止重复
        existing_store_items = db.query(
            MakroPiggybackItem.makro_product_id,
            MakroPiggybackItem.seller_sku
        ).filter(MakroPiggybackItem.store_id == target_store_id).all()

        target_store_fsns = {r[0].strip().upper() for r in existing_store_items if r[0]}
        existing_skus = {r[1].strip() for r in existing_store_items if r[1]}
        logger.info(f"目标店铺当前已存跟品数: {len(target_store_fsns)}")

        # 3. 查询全局已弃用黑名单 (is_abandoned == True)
        abandoned_fsns = {
            r[0].strip().upper()
            for r in db.query(MakroPiggybackItem.makro_product_id)
            .filter(MakroPiggybackItem.is_abandoned == True)
            .all()
            if r[0]
        }
        logger.info(f"系统全局弃用黑名单 FSN 数: {len(abandoned_fsns)}")

        # 4. 获取来源店铺 (1 和 2) 的下架商品
        source_listings = db.query(MakroListing).filter(
            MakroListing.store_id.in_([1, 2]),
            MakroListing.internal_state.in_(["INACTIVATED_BY_FLIPKART", "INACTIVE"]),
            MakroListing.product_id.isnot(None),
            MakroListing.product_id != ""
        ).order_by(MakroListing.updated_at.desc()).all()

        logger.info(f"从店铺 1 和 2 查询到符合条件的下架 Listings 总数: {len(source_listings)}")

        # 5. 去重并聚合有效 Listing (FSN 唯一化，优先选用最新更新的 listing)
        unique_listings_by_fsn: Dict[str, MakroListing] = {}
        for listing in source_listings:
            fsn = (listing.product_id or "").strip().upper()
            if not fsn or len(fsn) < 8:
                continue
            if fsn in target_store_fsns:
                continue
            if fsn in abandoned_fsns:
                continue
            if fsn not in unique_listings_by_fsn:
                unique_listings_by_fsn[fsn] = listing

        total_to_import = len(unique_listings_by_fsn)
        logger.info(f"过滤后净待转入跟品库商品数: {total_to_import} 款")

        if total_to_import == 0:
            logger.info("没有需要导入的新商品，处理结束。")
            return

        # 6. 构造 MakroPiggybackItem 对象并分批提交
        inserted_count = 0
        batch_items: List[MakroPiggybackItem] = []
        default_brand = getattr(store, "default_brand", "TaoTa") or "TaoTa"

        for idx, (fsn, listing) in enumerate(unique_listings_by_fsn.items(), 1):
            ssp = float(listing.ssp or 0.0)
            mrp = float(listing.mrp or ssp or 0.0)
            if mrp < ssp:
                mrp = round(ssp * 1.5, 2)

            # 95折目标售价 (保留两位小数，最低保底 1.0)
            target_price = max(round(ssp * 0.95, 2), 1.0)
            # 70% 保本底价
            min_floor = max(round(ssp * 0.70, 2), 1.0)
            target_mrp = mrp

            sku = generate_unique_sku(existing_skus)
            canonical_url = f"https://www.makro.co.za/-/p/p?pid={fsn}"

            item = MakroPiggybackItem(
                store_id=target_store_id,
                user_id=target_user_id,
                makro_product_id=fsn,
                item_id=None,
                makro_url=canonical_url,
                title=listing.title or f"Makro Product {fsn}",
                title_zh=listing.title or f"Makro Product {fsn}",
                brand=listing.brand or default_brand,
                vertical=listing.vertical or "general",
                image_url=listing.image_url or "",
                barcode=None,
                model_number=None,
                original_price=ssp,
                original_mrp=mrp,
                original_seller=None,
                seller_count=1,
                seller_sku=sku,
                target_price=target_price,
                target_mrp=target_mrp,
                min_price_floor=min_floor,
                max_price_ceiling=0.0,
                price_strategy="PERCENT_5",
                inventory=500,
                lead_time_days=14,
                is_abandoned=False,
                abandoned_reason=None,
                abandoned_at=None,
                variant_attributes=None,
                variant_name=None,
                weight=float(listing.weight) if listing.weight and listing.weight > 0 else 0.5,
                length=float(listing.length) if listing.length and listing.length > 0 else 15.0,
                breadth=float(listing.breadth) if listing.breadth and listing.breadth > 0 else 10.0,
                height=float(listing.height) if listing.height and listing.height > 0 else 5.0,
                auto_reprice=True,
                buybox_status="UNKNOWN",
                last_competitor_price=None,
                compliance_status="PENDING_CHECK",
                compliance_details=None,
                status="PENDING",
                makro_listing_id=None,
                error_message=None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )

            batch_items.append(item)

            if len(batch_items) >= chunk_size:
                if not dry_run:
                    db.bulk_save_objects(batch_items)
                    db.commit()
                inserted_count += len(batch_items)
                batch_items = []
                progress = (inserted_count / total_to_import) * 100
                logger.info(f"进度: {inserted_count}/{total_to_import} ({progress:.1f}%) 已经入库...")

        # 处理剩余批次
        if batch_items:
            if not dry_run:
                db.bulk_save_objects(batch_items)
                db.commit()
            inserted_count += len(batch_items)

        elapsed = time.time() - start_time
        logger.info(f"==================================================")
        logger.info(f"✅ 处理成功完成！")
        logger.info(f"总计成功转入跟品库商品: {inserted_count} 件")
        logger.info(f"耗时: {elapsed:.2f} 秒")
        logger.info(f"目标店铺: [{store.id}] {store.name} 待处理池 (STAGING)")
        logger.info(f"==================================================")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ 导入失败并回滚: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="一键将店铺下架商品转入跟品库")
    parser.add_argument("--dry-run", action="store_true", help="预演模式，不写入数据库")
    parser.add_argument("--chunk", type=int, default=500, help="每批提交数量，默认500")
    args = parser.parse_args()

    import_delisted_listings(dry_run=args.dry_run, chunk_size=args.chunk)
