# -*- coding: utf-8 -*-
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import json
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from backend.main import app
from app.database import SessionLocal
from app.models.makro_order import MakroOrder
from app.models.makro_buffer_stock import MakroBufferStock
from app.models.store import Store
from app.models.user import User
from app.utils.auth import get_current_user

client = TestClient(app)

def setup_auth_override():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").first()
        if not user:
            user = User(username="admin", is_admin=True, hashed_password="x")
            db.add(user)
            db.commit()
            db.refresh(user)
        app.dependency_overrides[get_current_user] = lambda: user
        return {"Authorization": "Bearer mock-token"}
    finally:
        db.close()

def test_operations_dashboard_endpoint_structure():
    headers = setup_auth_override()
    db = SessionLocal()
    store = db.query(Store).filter(Store.name == "hzh").first() or db.query(Store).first()
    db.close()
    assert store is not None

    resp = client.get(f"/api/store-orders/operations-dashboard?store_id={store.id}&days=14", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True

    # 1. 验证基础作用域与问候语
    assert "scope" in data
    assert data["scope"]["days"] == 14
    assert "greeting" in data
    assert "trend_insight" in data

    # 2. 验证 KPI 矩阵
    kpi = data.get("kpi", {})
    for key in ["window_orders", "window_gmv_zar", "window_payout_cny", "window_net_profit_cny", "avg_net_margin_pct", "avg_order_value_zar", "today_orders", "today_gmv_zar", "dod_order_pct", "wow_order_pct", "danger_orders_count"]:
        assert key in kpi, f"Missing KPI key: {key}"

    # 3. 验证走向时序数组
    trends = data.get("trends", [])
    assert len(trends) == 14
    for t in trends:
        assert "date" in t
        assert "orders_count" in t
        assert "gmv_zar" in t
        assert "payout_cny" in t
        assert "est_profit_cny" in t
        assert "cancelled_count" in t

    # 4. 验证 7 阶履约流转全景漏斗
    pipeline = data.get("pipeline", [])
    assert len(pipeline) >= 7
    stage_codes = [s["status"] for s in pipeline]
    assert "NEW" in stage_codes
    assert "PURCHASED" in stage_codes
    assert "PO_PACKING" in stage_codes
    assert "AIR_FREIGHT" in stage_codes
    assert "COMPLETED" in stage_codes

    # 5. 验证 SLA 发货四色雷达 (跨境梯队)
    sla_radar = data.get("sla_radar", {})
    assert "breached" in sla_radar
    assert "urgent_under_5d" in sla_radar
    assert "warning_5_to_15d" in sla_radar
    assert "safe_over_15d" in sla_radar
    # 兼容性别名
    assert "urgent_12h" in sla_radar
    assert "warning_24h" in sla_radar
    assert "safe_over24h" in sla_radar

    # 6. 验证爆款排行榜 TOP 10
    top_skus = data.get("top_skus", [])
    assert len(top_skus) <= 10
    if top_skus:
        assert top_skus[0]["rank"] == 1
        assert "sku_id" in top_skus[0]
        assert "quantity_sold" in top_skus[0]
        assert "sales_zar" in top_skus[0]
        assert "margin_pct" in top_skus[0]

    # 7. 验证运营决策行动待办
    action_items = data.get("action_items", [])
    assert isinstance(action_items, list)
    for act in action_items:
        assert "type" in act
        assert "badge" in act
        assert "title" in act
        assert "target_perspective" in act

def test_operations_dashboard_days_window():
    headers = setup_auth_override()
    db = SessionLocal()
    store = db.query(Store).filter(Store.name == "hzh").first() or db.query(Store).first()
    db.close()

    for days in [7, 14, 30]:
        resp = client.get(f"/api/store-orders/operations-dashboard?store_id={store.id}&days={days}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["trends"]) == days
        assert data["scope"]["days"] == days

def test_operations_dashboard_sla_classification():
    headers = setup_auth_override()
    db = SessionLocal()
    
    # 创建独立隔离店铺，杜绝历史订单干扰分类测试
    test_store = Store(name="test_sla_isolated_store", seller_id="test_sla_seller")
    db.add(test_store)
    db.commit()
    db.refresh(test_store)

    test_orders = []
    try:
        now = datetime.now()
        # 1. 逾期订单 (< 0 天)
        o_breached = MakroOrder(
            store_id=test_store.id,
            seller_id="test_seller",
            order_id="TEST_SLA_BREACHED_001",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=100.0,
            order_date=now - timedelta(days=5),
            dispatch_by_date=now - timedelta(days=2)
        )
        # 2. 紧急临界订单 (< 5 天)
        o_urgent = MakroOrder(
            store_id=test_store.id,
            seller_id="test_seller",
            order_id="TEST_SLA_URGENT_002",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=200.0,
            order_date=now - timedelta(days=1),
            dispatch_by_date=now + timedelta(days=3)
        )
        # 3. 临界预警订单 (5 - 15 天)
        o_warning = MakroOrder(
            store_id=test_store.id,
            seller_id="test_seller",
            order_id="TEST_SLA_WARNING_003",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=300.0,
            order_date=now - timedelta(days=1),
            dispatch_by_date=now + timedelta(days=10)
        )
        # 4. 时效充裕订单 (> 15 天)
        o_safe = MakroOrder(
            store_id=test_store.id,
            seller_id="test_seller",
            order_id="TEST_SLA_SAFE_004",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=400.0,
            order_date=now - timedelta(days=1),
            dispatch_by_date=now + timedelta(days=20)
        )
        test_orders = [o_breached, o_urgent, o_warning, o_safe]
        for o in test_orders:
            db.add(o)
        db.commit()
        for o in test_orders:
            db.refresh(o)

        resp = client.get(f"/api/store-orders/operations-dashboard?store_id={test_store.id}&days=7", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        radar = data["sla_radar"]

        # 检查各梯队精确计数
        assert radar["breached"]["count"] == 1
        assert radar["urgent_under_5d"]["count"] == 1
        assert radar["warning_5_to_15d"]["count"] == 1
        assert radar["safe_over_15d"]["count"] == 1

        # 检查逾期
        breached_ids = [x["order_id"] for x in radar["breached"]["orders"]]
        assert "TEST_SLA_BREACHED_001" in breached_ids
        breached_item = radar["breached"]["orders"][0]
        assert "已超时" in breached_item["delay_text"]

        # 检查紧急临界 (<5天)
        urgent_ids = [x["order_id"] for x in radar["urgent_under_5d"]["orders"]]
        assert "TEST_SLA_URGENT_002" in urgent_ids
        urgent_item = radar["urgent_under_5d"]["orders"][0]
        assert "剩余" in urgent_item["remaining_text"]
        assert "天" in urgent_item["remaining_text"]

        # 检查临界预警 (5-15天)
        warning_ids = [x["order_id"] for x in radar["warning_5_to_15d"]["orders"]]
        assert "TEST_SLA_WARNING_003" in warning_ids

        # 检查时效充裕 (>15天)
        safe_ids = [x["order_id"] for x in radar["safe_over_15d"]["orders"]]
        assert "TEST_SLA_SAFE_004" in safe_ids

        # 检查 KPI danger_orders_count = breached (1) + urgent_under_5d (1) = 2
        assert data["kpi"]["danger_orders_count"] == 2

    finally:
        for o in test_orders:
            if o and o.id:
                db.query(MakroOrder).filter(MakroOrder.id == o.id).delete()
        if test_store and test_store.id:
            db.query(Store).filter(Store.id == test_store.id).delete()
        db.commit()
        db.close()

if __name__ == "__main__":
    print("Running operations dashboard tests...")
    test_operations_dashboard_endpoint_structure()
    print("✓ PASSED: Operations dashboard structure and data contract")
    test_operations_dashboard_days_window()
    print("✓ PASSED: Operations dashboard time range window switching (7, 14, 30 days)")
    test_operations_dashboard_sla_classification()
    print("✓ PASSED: Operations dashboard SLA radar cross-border classification (<0d, <5d, 5-15d, >15d)")
    print("\n🎉 ALL OPERATIONS DASHBOARD TESTS PASSED PERFECTLY!")
