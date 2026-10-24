import time
import threading
import logging
from datetime import datetime
from ..database import SessionLocal
from ..models.store import Store
from .auto_reprice_service import AutoRepriceService

logger = logging.getLogger(__name__)

class AutoRepriceScheduler:
    """
    后台智能自动跟价定时巡检调度器
    默认每 60 分钟巡检一次所有启用了自动跟价的已挂靠商品
    """
    def __init__(self, interval_minutes: int = 60):
        self.interval_seconds = interval_minutes * 60
        self.running = False
        self._thread = None
        self.last_run_at = None
        self.last_summary = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MakroAutoRepriceThread")
        self._thread.start()
        logger.info(f"Makro 智能自动跟价引擎已启动 (巡检间隔: {self.interval_seconds // 60} 分钟)")

    def stop(self):
        self.running = False

    def _run_loop(self):
        # 启动后等待 2 分钟再开始首轮巡检，保证所有网络连接与配置就绪
        time.sleep(120)
        while self.running:
            try:
                self.poll_all_stores()
            except Exception as e:
                logger.warning(f"后台自动跟价轮询异常: {e}")

            # 睡眠等待下一个周期
            for _ in range(self.interval_seconds):
                if not self.running:
                    break
                time.sleep(1)

    def poll_all_stores(self):
        db = SessionLocal()
        try:
            self.last_run_at = datetime.now()
            active_stores = db.query(Store).filter(Store.is_active == True).all()
            total_stores = len(active_stores)
            total_repriced = 0

            from .task_manager import task_manager
            from ..models.store_product_reprice import StoreProductReprice
            from ..models.makro_listing import MakroListing
            from ..models.makro_piggyback import MakroPiggybackItem
            from ..models.setting import SystemSetting

            sys_conc = 3
            try:
                cfg_item = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_cruise_concurrency").first()
                if cfg_item and cfg_item.value:
                    sys_conc = max(1, min(int(cfg_item.value), 100))
            except Exception:
                pass

            for store in active_stores:
                if not store.cookie or not store.seller_id:
                    continue
                try:
                    logger.info(f"后台自动跟价: 正在巡检店铺 '{store.name}' (ID: {store.id})...")
                    # 查询该店铺在线监控商品
                    query = db.query(StoreProductReprice.id).join(MakroListing, StoreProductReprice.listing_id == MakroListing.id).filter(
                        StoreProductReprice.store_id == store.id,
                        StoreProductReprice.monitor_status == "ACTIVE",
                        MakroListing.internal_state == "ACTIVE",
                        StoreProductReprice.makro_product_id.notlike("TEST_FSN_%")
                    ).order_by(
                        StoreProductReprice.last_reprice_at.asc().nullsfirst(),
                        StoreProductReprice.id.asc()
                    )
                    item_ids = [it[0] for it in query.all()]
                    is_reprice_mode = True

                    if not item_ids:
                        query_pb = db.query(MakroPiggybackItem.id).filter(
                            MakroPiggybackItem.store_id == store.id,
                            MakroPiggybackItem.status.in_(["ACTIVE", "PUBLISHED"]),
                            MakroPiggybackItem.makro_product_id.notlike("TEST_FSN_%")
                        ).order_by(
                            MakroPiggybackItem.last_reprice_at.asc().nullsfirst(),
                            MakroPiggybackItem.id.asc()
                        )
                        item_ids = [it[0] for it in query_pb.all()]
                        is_reprice_mode = False

                    total_items = len(item_ids)
                    if total_items > 0:
                        task = task_manager.create_task(
                            task_type="FULL_CRUISE_INSPECTION",
                            name=f"24h定时巡检 [{store.name}] ({total_items} 件 · {sys_conc}线程)",
                            total=total_items,
                            product_ids=item_ids
                        )
                        AutoRepriceService.run_full_cruise_task(
                            tm=task_manager,
                            task_id=task["id"],
                            item_ids=item_ids,
                            concurrency=sys_conc,
                            is_reprice_monitor=is_reprice_mode
                        )
                        final_t = task_manager.get_task(task["id"])
                        succ_c = final_t.get("success_count", 0) if final_t else 0
                        total_repriced += succ_c
                        logger.info(f"后台自动跟价: 店铺 '{store.name}' 24h定时巡检完成，成功 {succ_c}/{total_items} 件")
                    else:
                        logger.info(f"后台自动跟价: 店铺 '{store.name}' 无需巡检的在线商品")
                except Exception as se:
                    logger.warning(f"店铺 '{store.name}' 跟价执行异常: {se}")

            self.last_summary = {
                "run_at": self.last_run_at.isoformat(),
                "stores_checked": total_stores,
                "total_repriced": total_repriced
            }
        finally:
            db.close()

# 单例导出
auto_reprice_scheduler = AutoRepriceScheduler(interval_minutes=60)
