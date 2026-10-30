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
    for key in ["window_orders", "window_gmv_zar", "window_payout_cny", "window_net_profit_cny", "avg_net_margin_pct", "avg_order_value_zar", "today_orders", "today_gmv_zar", "dod_order_pct", "wow_order_pct"]:
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

    # 5. 验证 SLA 发货四色雷达
    sla_radar = data.get("sla_radar", {})
    assert "breached" in sla_radar
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
    store = db.query(Store).filter(Store.name == "hzh").first() or db.query(Store).first()
    
    test_order_urgent = None
    try:
        now = datetime.now()
        # 创建一个发货截止在 5 小时以后的订单 (应落入 urgent_12h)
        test_order_urgent = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_SLA_URGENT_999",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=500.0,
            order_date=now - timedelta(days=1),
            dispatch_by_date=now + timedelta(hours=5)
        )
        db.add(test_order_urgent)
        db.commit()
        db.refresh(test_order_urgent)

        resp = client.get(f"/api/store-orders/operations-dashboard?store_id={store.id}&days=7", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        urgent_orders = data["sla_radar"]["urgent_12h"]["orders"]
        matched = [o for o in urgent_orders if o["order_id"] == "TEST_SLA_URGENT_999"]
        assert len(matched) == 1, "Urgent order not found in urgent_12h radar"
        assert "剩余" in matched[0]["remaining_text"]
    finally:
        if test_order_urgent and test_order_urgent.id:
            db.query(MakroOrder).filter(MakroOrder.id == test_order_urgent.id).delete()
            db.commit()
        db.close()

if __name__ == "__main__":
    print("Running operations dashboard tests...")
    test_operations_dashboard_endpoint_structure()
    print("✓ PASSED: Operations dashboard structure and data contract")
    test_operations_dashboard_days_window()
    print("✓ PASSED: Operations dashboard time range window switching (7, 14, 30 days)")
    test_operations_dashboard_sla_classification()
    print("✓ PASSED: Operations dashboard SLA radar urgency classification")
    print("\n🎉 ALL OPERATIONS DASHBOARD TESTS PASSED PERFECTLY!")
