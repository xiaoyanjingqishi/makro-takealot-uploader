import time
import threading
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from ..database import SessionLocal
from ..models.store import Store
from ..models.setting import SystemSetting
from .makro_auth_service import MakroAuthService
from .audit_logger import record_audit_log

logger = logging.getLogger(__name__)

class AutoLoginScheduler:
    """
    Makro 店铺全自动登录与凭据保活定时调度引擎
    每隔 N 小时 (管理员可自定义，默认 21 小时) 自动为已配置账密与邮箱授权码的店铺重新登录，
    获取最新的 connect.sid、T Cookie 与 CSRF 令牌，确保持久有效。
    """
    def __init__(self, default_interval_hours: float = 21.0):
        self.interval_hours = default_interval_hours
        self.interval_seconds = int(default_interval_hours * 3600)
        self.enabled = True
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_check_at: Optional[datetime] = None

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MakroAutoLoginSchedulerThread")
            self._thread.start()
            logger.info(f"Makro 店铺全自动登录与凭据保活定时引擎已启动 (默认检测周期: {self.interval_hours} 小时)")

    def stop(self):
        with self._lock:
            self.running = False

    def set_interval_hours(self, hours: float, enabled: bool = True):
        """动态更新执行周期 (支持管理员在前端调整并即刻生效)"""
        with self._lock:
            self.interval_hours = max(1.0, float(hours))
            self.interval_seconds = int(self.interval_hours * 3600)
            self.enabled = enabled
            logger.info(f"Makro 自动保活引擎配置已热重载: 启用={self.enabled}, 检测周期={self.interval_hours} 小时 ({self.interval_seconds} 秒)")

    def _sync_config_from_db(self):
        """从数据库读取最新的全局周期与开关配置"""
        try:
            with SessionLocal() as db:
                s_enabled = db.query(SystemSetting).filter(SystemSetting.key == "auto_login_check_enabled").first()
                s_interval = db.query(SystemSetting).filter(SystemSetting.key == "auto_login_check_interval_hours").first()

                if s_enabled is not None:
                    self.enabled = str(s_enabled.value).lower() in ["true", "1", "yes"]
                if s_interval is not None and s_interval.value:
                    try:
                        self.interval_hours = max(1.0, float(s_interval.value))
                        self.interval_seconds = int(self.interval_hours * 3600)
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning(f"读取自动保活系统配置异常: {e}")

    def _run_loop(self):
        # 启动后等待 90 秒，待系统和网络组件完全就绪后再进行首次检测
        time.sleep(90)
        self._sync_config_from_db()

        while self.running:
            try:
                if self.enabled:
                    logger.info(f"⚡ [定时保活] 触发周期性店铺凭据自动登录检测 (周期: {self.interval_hours} 小时)...")
                    self.trigger_all_stores()
                else:
                    logger.info("⏸️ [定时保活] 当前自动登录保活功能已被管理员关闭，跳过本轮执行")
            except Exception as e:
                logger.error(f"[定时保活] 周期性轮询异常: {e}")

            # 睡眠并分秒等待下一个周期，方便随时响应配置热重载或服务关闭
            self._sync_config_from_db()
            sleep_step = 2
            waited = 0
            while waited < self.interval_seconds and self.running:
                time.sleep(sleep_step)
                waited += sleep_step

    def trigger_all_stores(self) -> Dict[str, Any]:
        """
        立即遍历并执行全部已启用且配置了账密的店铺自动登录与凭据刷新
        """
        self.last_check_at = datetime.now()
        results = []
        with SessionLocal() as db:
            active_stores = db.query(Store).filter(Store.is_active == True).all()
            logger.info(f"[定时保活] 检测到 {len(active_stores)} 个活跃店铺，开始逐一评估自动登录就绪状态...")

            for idx, store in enumerate(active_stores):
                # 检查是否具备自动登录与收码凭据
                has_cred = bool(
                    store.login_email and 
                    store.login_password and 
                    store.imap_password
                )
                if not has_cred:
                    logger.info(f"[定时保活] 跳过店铺【{store.name}】(ID: #{store.id})：未完整配置 Makro 登录密码或邮箱授权码")
                    results.append({
                        "store_id": store.id,
                        "store_name": store.name,
                        "status": "SKIPPED",
                        "message": "未配置登录密码或邮箱授权码"
                    })
                    continue

                # 逐个店铺执行全自动登录 (含 UID 预检、发码、IMAP 读码与凭据回写)
                logger.info(f"[定时保活] ({idx+1}/{len(active_stores)}) 正在为店铺【{store.name}】执行全自动登录与凭据刷新...")
                try:
                    res = MakroAuthService.run_full_auto_login(
                        store_id=store.id,
                        db=db,
                        max_wait_seconds=60
                    )
                    
                    # 重新刷新 store 实例
                    db.refresh(store)
                    if res.get("success"):
                        store.last_auto_login_at = datetime.now()
                        store.last_auto_login_status = "SUCCESS"
                        db.commit()
                        logger.info(f"[定时保活] 🎉 店铺【{store.name}】全自动登录成功！凭据与 Cookie 有效期已更新")
                        record_audit_log(
                            task_type="STORE_AUTO_REFRESH",
                            status="SUCCESS",
                            message=f"店铺【{store.name}】定时全自动登录保活成功，凭据已刷新",
                            detail_logs={"store_id": store.id, "seller_id": store.seller_id},
                            db=db
                        )
                        results.append({
                            "store_id": store.id,
                            "store_name": store.name,
                            "status": "SUCCESS",
                            "message": "自动登录并刷新凭据成功"
                        })
                    else:
                        err_msg = res.get("message") or "自动登录未成功"
                        store.last_auto_login_status = f"FAILED: {err_msg[:200]}"
                        db.commit()
                        logger.warning(f"[定时保活] ⚠️ 店铺【{store.name}】全自动登录失败: {err_msg}")
                        record_audit_log(
                            task_type="STORE_AUTO_REFRESH",
                            status="FAILED",
                            message=f"店铺【{store.name}】定时登录保活失败: {err_msg[:100]}",
                            detail_logs={"store_id": store.id, "error": err_msg},
                            db=db
                        )
                        results.append({
                            "store_id": store.id,
                            "store_name": store.name,
                            "status": "FAILED",
                            "message": err_msg
                        })
                except Exception as se:
                    logger.error(f"[定时保活] 执行店铺【{store.name}】自动登录发生未捕获异常: {se}")
                    store.last_auto_login_status = f"ERROR: {str(se)[:200]}"
                    db.commit()
                    results.append({
                        "store_id": store.id,
                        "store_name": store.name,
                        "status": "ERROR",
                        "message": str(se)
                    })

                # 店铺间执行间隔 (等待 15 秒，避免多店铺连环发信被邮件服务商或平台判定频繁请求)
                if idx < len(active_stores) - 1:
                    time.sleep(15)

        return {
            "total": len(results),
            "executed_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "details": results
        }

auto_login_scheduler = AutoLoginScheduler(default_interval_hours=21.0)
