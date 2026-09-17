import time
import uuid
import threading
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List
from .audit_logger import record_audit_log

logger = logging.getLogger("task_manager")

class TaskManager:
    """
    后台异步批处理任务管理器 (单例)
    提供多线程异步执行、实时进度跟踪、防页面刷新丢失、协同取消与日志自动归档。
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TaskManager, cls).__new__(cls)
                cls._instance._tasks: Dict[str, Dict[str, Any]] = {}
                cls._instance._task_lock = threading.Lock()
        return cls._instance

    def create_task(self, task_type: str, name: str, total: int, product_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        创建并注册新任务
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task_id = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        task_info = {
            "id": task_id,
            "task_type": task_type,
            "name": name,
            "status": "RUNNING",
            "total": total,
            "current": 0,
            "progress": 0,
            "success_count": 0,
            "fail_count": 0,
            "current_title": "准备启动...",
            "message": f"任务启动: 共 {total} 项",
            "errors": [],
            "product_ids": product_ids or [],
            "created_at": now_str,
            "updated_at": now_str,
            "finished_at": None,
            "cancel_requested": False
        }
        with self._task_lock:
            self._tasks[task_id] = task_info
        return task_info

    def start_task(self, task_id: str, worker_fn: Callable[['TaskManager', str], None]):
        """
        在后台守护线程中启动任务
        """
        thread = threading.Thread(target=self._run_wrapper, args=(task_id, worker_fn), daemon=True)
        thread.start()

    def _run_wrapper(self, task_id: str, worker_fn: Callable[['TaskManager', str], None]):
        try:
            worker_fn(self, task_id)
        except Exception as e:
            logger.error(f"后台任务 {task_id} 执行异常: {e}", exc_info=True)
            self.finish_task(task_id, status="FAILED", message=f"执行异常中断: {str(e)}")

    def update_progress(
        self,
        task_id: str,
        current: int,
        current_title: str = "",
        success_inc: int = 0,
        fail_inc: int = 0,
        error: Optional[str] = None
    ):
        """
        更新任务实时进度
        """
        with self._task_lock:
            t = self._tasks.get(task_id)
            if not t or t["status"] != "RUNNING":
                return

            t["current"] = current
            total = max(1, t["total"])
            t["progress"] = min(100, int((current / total) * 100))
            if current_title:
                t["current_title"] = current_title[:100]
            if success_inc:
                t["success_count"] += success_inc
            if fail_inc:
                t["fail_count"] += fail_inc
            if error:
                t["errors"].append(error[:250])
            t["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def is_cancelled(self, task_id: str) -> bool:
        with self._task_lock:
            t = self._tasks.get(task_id)
            return bool(t and t.get("cancel_requested"))

    def cancel_task(self, task_id: str) -> bool:
        with self._task_lock:
            t = self._tasks.get(task_id)
            if t and t["status"] == "RUNNING":
                t["cancel_requested"] = True
                t["status"] = "CANCELLED"
                t["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                t["message"] = "用户已取消任务"
                return True
            return False

    def finish_task(self, task_id: str, status: str = "SUCCESS", message: str = ""):
        """
        结束任务并记录系统操作日志
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._task_lock:
            t = self._tasks.get(task_id)
            if not t:
                return

            # 如果用户已经取消，保持 CANCELLED 状态
            if t["status"] == "CANCELLED":
                status = "CANCELLED"

            t["status"] = status
            t["finished_at"] = now_str
            t["updated_at"] = now_str
            t["progress"] = 100
            if not message:
                message = f"执行完成: 成功 {t['success_count']} 项, 失败 {t['fail_count']} 项"
            t["message"] = message

            # 复制一份用于持久化审计日志
            task_copy = dict(t)

        # 写入持久化系统操作日志
        try:
            record_audit_log(
                task_type=task_copy["task_type"],
                status=task_copy["status"],
                message=f"[{task_copy['name']}] {task_copy['message']}",
                detail_logs={
                    "total": task_copy["total"],
                    "success": task_copy["success_count"],
                    "failed": task_copy["fail_count"],
                    "errors": task_copy["errors"][:10],
                    "product_ids": task_copy.get("product_ids", [])[:50]
                }
            )
        except Exception as ex:
            logger.warning(f"归档任务日志异常: {ex}")

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._task_lock:
            t = self._tasks.get(task_id)
            return dict(t) if t else None

    def get_active_tasks(self) -> List[Dict[str, Any]]:
        """
        获取所有当前正在运行的任务，以及最近 15 秒内刚完成/取消的任务列表
        支持多个批处理任务（如批量清洗与批量上品）并发推进且进度条独立展示互不覆盖
        """
        result = []
        now = time.time()
        with self._task_lock:
            for t in self._tasks.values():
                if t["status"] == "RUNNING":
                    result.append(dict(t))
                elif t.get("finished_at"):
                    try:
                        fin_dt = datetime.strptime(t["finished_at"], "%Y-%m-%d %H:%M:%S")
                        if (now - fin_dt.timestamp()) < 15:
                            result.append(dict(t))
                    except Exception:
                        pass
        return result

    def get_active_task(self) -> Optional[Dict[str, Any]]:
        """
        向后兼容获取单个最近任务
        """
        tasks = self.get_active_tasks()
        return tasks[-1] if tasks else None

# 全局单例
task_manager = TaskManager()
