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

            for store in active_stores:
                if not store.cookie or not store.seller_id:
                    continue
                try:
                    logger.info(f"后台自动跟价: 正在巡检店铺 '{store.name}' (ID: {store.id})...")
                    res = AutoRepriceService.run_reprice_for_store(store, db)
                    total_repriced += res.get("success_count", 0)
                    logger.info(f"后台自动跟价: 店铺 '{store.name}' 巡检完成，处理 {res.get('total_items')} 件商品 (降价跟进: {res.get('undercut_count')}, 己方胜出保持: {res.get('winning_hold_count')}, 触底保本: {res.get('floor_count')})")
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
