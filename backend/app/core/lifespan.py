# -*- coding: utf-8 -*-
"""
FastAPI 现代 lifespan 生命周期管理
优雅控制数据库迁移自检与后台常驻调度器的平稳启停
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import engine, SessionLocal
from app.core.db_migrations import run_system_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 启动阶段：执行结构自检与数据迁移
    try:
        run_system_migrations(engine, SessionLocal)
    except Exception as e:
        logger.error(f"[LIFESPAN] 数据库迁移自检失败: {e}", exc_info=True)

    # 2. 检查是否启动后台定时调度器 (单元测试可通过环境变量 ENABLE_BACKGROUND_SCHEDULERS=false 禁用)
    enable_schedulers = os.getenv("ENABLE_BACKGROUND_SCHEDULERS", "true").lower() not in ["false", "0", "no"]

    if enable_schedulers:
        try:
            from app.services.order_sync_scheduler import order_sync_scheduler
            order_sync_scheduler.start()
        except Exception as e:
            logger.warning(f"[LIFESPAN] 启动订单同步调度器失败: {e}")

        try:
            from app.services.auto_login_scheduler import auto_login_scheduler
            auto_login_scheduler.start()
        except Exception as e:
            logger.warning(f"[LIFESPAN] 启动自动登录保活调度器失败: {e}")

        try:
            from app.services.auto_reprice_scheduler import auto_reprice_scheduler
            auto_reprice_scheduler.start()
        except Exception as e:
            logger.warning(f"[LIFESPAN] 启动自动跟价调度器失败: {e}")

        try:
            from app.services.piggyback_collect_service import piggyback_collect_service
            piggyback_collect_service.recover_pending_fetching()
        except Exception as e:
            logger.warning(f"[LIFESPAN] 恢复未完成的静默采集任务失败: {e}")
    else:
        logger.info("[LIFESPAN] 检测到调度器禁用标识 (ENABLE_BACKGROUND_SCHEDULERS=false)，跳过后台调度器自启")

    yield

    # 3. 停机阶段：优雅停止所有后台常驻调度器
    if enable_schedulers:
        try:
            from app.services.order_sync_scheduler import order_sync_scheduler
            if hasattr(order_sync_scheduler, "stop"):
                order_sync_scheduler.stop()
        except Exception:
            pass

        try:
            from app.services.auto_login_scheduler import auto_login_scheduler
            if hasattr(auto_login_scheduler, "stop"):
                auto_login_scheduler.stop()
        except Exception:
            pass

        try:
            from app.services.auto_reprice_scheduler import auto_reprice_scheduler
            if hasattr(auto_reprice_scheduler, "stop"):
                auto_reprice_scheduler.stop()
        except Exception:
            pass
