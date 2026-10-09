import time
import logging
import threading
import queue
from typing import List, Optional, Dict, Any
from ..database import SessionLocal
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.setting import SystemSetting
from .makro_scraper_service import MakroScraperService
from .makro_piggyback_service import MakroPiggybackService

logger = logging.getLogger("piggyback_collect")

class PiggybackCollectService:
    """
    Makro 智能跟品后台静默并发采集消费队列服务 (单例)
    支持从插件/API秒级入库骨架数据后，后台多线程并发静默拉取完整数据并就地补全。
    """
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(PiggybackCollectService, cls).__new__(cls)
                cls._instance._queue = queue.Queue()
                cls._instance._enqueued_ids = set()
                cls._instance._queue_lock = threading.Lock()
                cls._instance._workers: List[threading.Thread] = []
                cls._instance._stop_event = threading.Event()
                cls._instance._ensure_workers()
        return cls._instance

    def _get_configured_concurrency(self) -> int:
        """读取系统设置中管理员配置的采集并发线程数 (默认 5，范围 1~50)"""
        db = SessionLocal()
        try:
            cfg = db.query(SystemSetting).filter(SystemSetting.key == "piggyback_collect_concurrency").first()
            if cfg and cfg.value:
                val = int(cfg.value)
                return max(1, min(val, 50))
        except Exception:
            pass
        finally:
            db.close()
        return 5

    def _ensure_workers(self):
        """确保后台守护工作线程池维持在管理员指定的并发数"""
        target_concurrency = self._get_configured_concurrency()
        with self._lock:
            # 清理已结束的线程
            self._workers = [w for w in self._workers if w.is_alive()]
            needed = target_concurrency - len(self._workers)
            for i in range(needed):
                t = threading.Thread(
                    target=self._worker_loop,
                    name=f"PiggybackCollectWorker-{len(self._workers)+1}",
                    daemon=True
                )
                t.start()
                self._workers.append(t)

    def enqueue_items(self, item_ids: List[int]) -> int:
        """
        将待静默拉取数据的跟品 ID 批量推入队列
        """
        if not item_ids:
            return 0
        self._ensure_workers()
        added_count = 0
        with self._queue_lock:
            for iid in item_ids:
                if iid not in self._enqueued_ids:
                    self._enqueued_ids.add(iid)
                    self._queue.put(iid)
                    added_count += 1
        logger.info(f"已将 {added_count} 件跟品推入后台静默拉取队列 (当前待处理任务数: {self._queue.qsize()})")
        return added_count

    def retry_item(self, item_id: int) -> bool:
        """
        一键重新触发单个失败商品的静默拉取
        """
        db = SessionLocal()
        try:
            item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
            if not item:
                return False
            item.status = "FETCHING"
            item.error_message = None
            db.commit()
            self.enqueue_items([item_id])
            return True
        finally:
            db.close()

    def retry_failed_items(self, store_id: Optional[int] = None) -> int:
        """
        一键批量重试所有失败的商品
        """
        db = SessionLocal()
        try:
            query = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.status == "FAILED")
            if store_id:
                query = query.filter(MakroPiggybackItem.store_id == store_id)
            failed_items = query.all()
            ids = [it.id for it in failed_items]
            for it in failed_items:
                it.status = "FETCHING"
                it.error_message = None
            db.commit()
            if ids:
                self.enqueue_items(ids)
            return len(ids)
        finally:
            db.close()

    def recover_pending_fetching(self) -> int:
        """
        系统启动时恢复历史遗留处于 FETCHING 状态的任务
        """
        db = SessionLocal()
        try:
            items = db.query(MakroPiggybackItem.id).filter(MakroPiggybackItem.status == "FETCHING").all()
            ids = [r[0] for r in items]
            if ids:
                logger.info(f"系统启动发现 {len(ids)} 件遗留 FETCHING 状态商品，自动恢复排队拉取")
                self.enqueue_items(ids)
            return len(ids)
        finally:
            db.close()

    def _worker_loop(self):
        """后台静默工作线程主循环"""
        while not self._stop_event.is_set():
            try:
                item_id = self._queue.get(timeout=2.0)
            except queue.Empty:
                continue

            try:
                self._process_single_item(item_id)
            except Exception as e:
                logger.error(f"处理跟品 [ID: {item_id}] 发生未捕获异常: {e}", exc_info=True)
            finally:
                with self._queue_lock:
                    self._enqueued_ids.discard(item_id)
                self._queue.task_done()

    def _process_single_item(self, item_id: int):
        """单个商品的深度静默拉取与补全"""
        db = SessionLocal()
        try:
            item = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
            if not item:
                return

            if item.status != "FETCHING":
                return

            logger.info(f"后台静默拉取启动: 商品 [ID: {item.id}, FSN: {item.makro_product_id}]")
            raw_target = item.makro_url or item.makro_product_id
            client_info = {
                "item_id": item.item_id,
                "variant_name": item.variant_name
            }

            try:
                data = MakroScraperService.resolve_piggyback_product(
                    raw_target,
                    store=item.store,
                    client_data=client_info
                )
            except Exception as ex:
                logger.warning(f"商品 [ID: {item.id}, FSN: {item.makro_product_id}] 静默拉取失败: {ex}")
                item.status = "FAILED"
                item.error_message = f"静默抓取失败: {str(ex)[:200]}"
                db.commit()
                return

            if not data:
                item.status = "FAILED"
                item.error_message = "未能解析出商品有效数据"
                db.commit()
                return

            # 数据就地增量补全
            res_fsn = data.get("makro_product_id") or item.makro_product_id
            target_item_id = item.item_id or data.get("item_id")
            raw_url = data.get("makro_url") or MakroScraperService.format_canonical_makro_url(res_fsn, target_item_id)
            title = data.get("title") or item.title or res_fsn
            title_zh = data.get("title_zh") or title
            image_url = data.get("image_url") or item.image_url or ""
            seller_name = data.get("original_seller") or ""
            seller_count = int(data.get("seller_count") or 1)
            real_price = float(data.get("original_price") or 0.0)
            real_mrp = float(data.get("original_mrp") or (real_price * 1.5 if real_price > 0 else 0.0))

            floor_val = float(item.min_price_floor or 0.0)
            if floor_val <= 0:
                floor_val = MakroPiggybackService.calculate_default_floor(real_price, db)

            target_p, target_m = MakroPiggybackService.calculate_price(
                original_price=real_price,
                strategy=item.price_strategy or "MINUS_15",
                min_floor=floor_val,
                original_mrp=real_mrp
            )

            if item.variant_name and not title_zh.endswith(f"({item.variant_name})"):
                title_zh = f"{title_zh} ({item.variant_name})"

            item.makro_product_id = res_fsn
            item.item_id = target_item_id
            item.makro_url = raw_url
            item.title = title
            item.title_zh = title_zh
            item.brand = data.get("brand") or getattr(item.store, "default_brand", "Generic") or "Generic"
            item.vertical = data.get("vertical") or item.vertical or "general"
            item.image_url = image_url
            item.model_number = data.get("model_number") or item.model_number
            item.barcode = data.get("barcode") or item.barcode
            item.original_price = real_price
            item.original_mrp = real_mrp
            item.original_seller = seller_name
            item.seller_count = seller_count
            item.target_price = target_p
            item.target_mrp = target_m
            item.min_price_floor = floor_val
            item.status = "PENDING"  # 成功流转为待发布挂靠
            item.error_message = None

            db.commit()
            logger.info(f"✅ 商品 [ID: {item.id}, FSN: {res_fsn}] 静默补全成功！已流转为 PENDING (原价: R{real_price}, 跟售价: R{target_p})")

        except Exception as e:
            logger.error(f"更新商品 [ID: {item_id}] 详情异常: {e}", exc_info=True)
            try:
                db.rollback()
                it = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.id == item_id).first()
                if it:
                    it.status = "FAILED"
                    it.error_message = f"补全异常: {str(e)[:200]}"
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()

# 单例实例
piggyback_collect_service = PiggybackCollectService()
