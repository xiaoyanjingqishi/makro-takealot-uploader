# -*- coding: utf-8 -*-
"""
自动化测试：自动巡检进度条、成功/失败/阻断明细收集与异步调度回归测试
"""
import sys
import os
import time

sys.stdout.reconfigure(encoding='utf-8')
# 确保 backend 在 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models.store import Store
from app.models.store_product_reprice import StoreProductReprice
from app.models.makro_listing import MakroListing
from app.services.task_manager import task_manager
from app.services.auto_reprice_service import AutoRepriceService
from app.api.reprice import trigger_batch_reprice, trigger_full_cruise_reprice, BatchRepriceRequest, FullCruiseRequest
from app.models.user import User

def test_task_manager_progress_and_failure_tracking():
    print("\n--- [TEST 1] TaskManager 进度条与失败明细收集能力 ---")
    task = task_manager.create_task(
        task_type="FULL_CRUISE_INSPECTION",
        name="测试巡检任务",
        total=5,
        product_ids=[101, 102, 103, 104, 105]
    )
    tid = task["id"]
    assert task["failed_items"] == []
    assert task["breakdown"] == {}

    # 模拟步骤 1: 成功 1 件
    task_manager.update_progress(
        task_id=tid,
        current=1,
        current_title="SKU-101: 抢流降价至 R150",
        success_inc=1,
        breakdown={"undercut": 1, "winning": 0, "floor": 0, "blocked": 0, "failed": 0, "success": 1}
    )
    t1 = task_manager.get_task(tid)
    assert t1["current"] == 1
    assert t1["progress"] == 20
    assert t1["success_count"] == 1
    assert t1["fail_count"] == 0
    assert t1["breakdown"]["undercut"] == 1

    # 模拟步骤 2: 阻断 1 件
    task_manager.update_progress(
        task_id=tid,
        current=2,
        current_title="SKU-102: 🛡️反爬阻断防护(维持现价)",
        blocked_inc=1,
        breakdown={"undercut": 1, "winning": 0, "floor": 0, "blocked": 1, "failed": 0, "success": 1}
    )
    t2 = task_manager.get_task(tid)
    assert t2["current"] == 2
    assert t2["progress"] == 40
    assert t2["blocked_count"] == 1

    # 模拟步骤 3: 失败 1 件 (结构化记录 failed_items)
    fail_record = [{"id": 103, "sku": "SKU-103", "reason": "未能获取到前台有效竞对售价"}]
    task_manager.update_progress(
        task_id=tid,
        current=3,
        current_title="SKU-103: 失败: 未能获取到前台有效竞对售价",
        fail_inc=1,
        error="SKU-103: 未能获取到前台有效竞对售价",
        failed_items=fail_record,
        breakdown={"undercut": 1, "winning": 0, "floor": 0, "blocked": 1, "failed": 1, "success": 1}
    )
    t3 = task_manager.get_task(tid)
    assert t3["current"] == 3
    assert t3["progress"] == 60
    assert t3["fail_count"] == 1
    assert len(t3["failed_items"]) == 1
    assert t3["failed_items"][0]["sku"] == "SKU-103"
    assert t3["failed_items"][0]["reason"] == "未能获取到前台有效竞对售价"

    # 完成任务
    task_manager.finish_task(
        task_id=tid,
        status="SUCCESS",
        message="巡检完成",
        success_count=3,
        fail_count=1,
        blocked_count=1,
        failed_items=fail_record
    )
    t_final = task_manager.get_task(tid)
    assert t_final["status"] == "SUCCESS"
    assert t_final["progress"] == 100
    assert t_final["success_count"] == 3
    assert t_final["fail_count"] == 1
    assert t_final["blocked_count"] == 1
    assert len(t_final["failed_items"]) == 1
    print("  ✅ 1.1 TaskManager 正确记录了进度百分比、成功数、失败数、阻断数和失败明细列表")

def test_trigger_batch_reprice_async(db_session, mock_admin_user):
    print("\n--- [TEST 2] 批量即时巡检 trigger-batch 异步化验证 ---")
    store = db_session.query(Store).first()
    if not store:
        store = Store(name="测试巡检店铺", is_active=True, seller_id="TEST_SELLER")
        db_session.add(store)
        db_session.commit()
        db_session.refresh(store)

    listing = MakroListing(
        store_id=store.id,
        seller_id=store.seller_id or "TEST_SELLER",
        sku_id="CRUISE_SKU_01",
        product_id="CRUISE_FSN_01",
        internal_state="ACTIVE",
        ssp=199.0
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    reprice_item = StoreProductReprice(
        store_id=store.id,
        listing_id=listing.id,
        seller_sku="CRUISE_SKU_01",
        makro_product_id="CRUISE_FSN_01",
        monitor_status="ACTIVE",
        auto_reprice=True
    )
    db_session.add(reprice_item)
    db_session.commit()
    db_session.refresh(reprice_item)

    try:
        req = BatchRepriceRequest(ids=[reprice_item.id])
        res = trigger_batch_reprice(req=req, db=db_session, current_user=mock_admin_user)

        assert res["success"] is True
        assert "task_id" in res
        assert res["total"] == 1
        tid = res["task_id"]
        print(f"  ✅ 2.1 批量巡检接口即刻返回 task_id: {tid}, total: {res['total']}")

        task = task_manager.get_task(tid)
        assert task is not None
        assert task["task_type"] == "FULL_CRUISE_INSPECTION"
        print(f"  ✅ 2.2 TaskManager 成功注册任务: [{task['name']}], status: {task['status']}")

        # 等待后台线程执行结束 (超时 10 秒)
        for _ in range(50):
            t_curr = task_manager.get_task(tid)
            if t_curr and t_curr["status"] != "RUNNING":
                break
            time.sleep(0.2)

        t_done = task_manager.get_task(tid)
        assert t_done["status"] in ["SUCCESS", "FAILED"]
        assert t_done["progress"] == 100
        print(f"  ✅ 2.3 批量巡检任务执行完毕: status={t_done['status']}, 处理 {t_done['current']}/{t_done['total']} 件 (成功: {t_done['success_count']}, 失败: {t_done['fail_count']}, 阻断: {t_done['blocked_count']})")
    finally:
        db_session.delete(reprice_item)
        db_session.delete(listing)
        db_session.commit()

def test_full_cruise_task_cancellation():
    print("\n--- [TEST 3] 巡检任务终止 (cancel_task) 验证 ---")
    task = task_manager.create_task(
        task_type="FULL_CRUISE_INSPECTION",
        name="待终止的巡检任务",
        total=100
    )
    tid = task["id"]
    assert task_manager.is_cancelled(tid) is False

    ok = task_manager.cancel_task(tid)
    assert ok is True
    assert task_manager.is_cancelled(tid) is True
    t_cancelled = task_manager.get_task(tid)
    assert t_cancelled["status"] == "CANCELLED"
    print("  ✅ 3.1 任务取消指令成功下发并置为 CANCELLED 状态")

def run_all():
    print("=" * 70)
    print("🚀 开始执行自动巡检进度条与成功/失败结果展示自动化回归测试")
    print("=" * 70)
    db = SessionLocal()
    mock_admin = User(id=9999, username="admin_tester", role="ADMIN", is_active=True)
    try:
        test_task_manager_progress_and_failure_tracking()
        test_trigger_batch_reprice_async(db, mock_admin)
        test_full_cruise_task_cancellation()
        print("\n" + "=" * 70)
        print("🎉 全部自动巡检进度条与成功/失败展示自动化测试 100% 验证通过！")
        print("=" * 70)
    finally:
        db.close()

if __name__ == "__main__":
    run_all()
