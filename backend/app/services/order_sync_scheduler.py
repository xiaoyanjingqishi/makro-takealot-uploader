import time
import threading
import logging
from datetime import datetime
from ..database import SessionLocal
from ..models.store import Store
from .makro_portal_service import MakroPortalService

logger = logging.getLogger(__name__)

class OrderSyncScheduler:
    """
    后台静默定时轮询调度器 (每隔 30 分钟为已启用的店铺自动同步订单与在线商品)
    """
    def __init__(self, interval_minutes: int = 30):
        self.interval_seconds = interval_minutes * 60
        self.running = False
        self._thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MakroAutoSyncThread")
        self._thread.start()
        logger.info(f"Makro 后台订单与商品双模式定时同步引擎已启动 (轮询间隔: {self.interval_seconds // 60} 分钟)")

    def stop(self):
        self.running = False

    def _run_loop(self):
        # 启动后等待 1 分钟再开始首次轮询，让应用充分就绪
        time.sleep(60)
        while self.running:
            try:
                self._poll_all_stores()
            except Exception as e:
                logger.warning(f"后台自动同步轮询异常: {e}")

            # 睡眠等待下一个周期
            for _ in range(self.interval_seconds):
                if not self.running:
                    break
                time.sleep(1)

    def _poll_all_stores(self):
        db = SessionLocal()
        try:
            active_stores = db.query(Store).filter(Store.is_active == True).all()
            for store in active_stores:
                if not store.cookie or not store.seller_id:
                    continue
                try:
                    logger.info(f"后台自动同步: 正在轮询店铺 '{store.name}' (ID: {store.id})...")
                    # 同步订单
                    o_res = MakroPortalService.sync_store_orders(store, db)
                    # 同步商品
                    l_res = MakroPortalService.sync_store_listings(store, db)
                    logger.info(f"后台自动同步: 店铺 '{store.name}' 同步成功 (订单: {o_res.get('total_synced')}, 商品: {l_res.get('total_synced')})")
                except Exception as se:
                    logger.warning(f"后台自动同步店铺 '{store.name}' 失败: {se}")
        finally:
            db.close()

order_sync_scheduler = OrderSyncScheduler(interval_minutes=30)
