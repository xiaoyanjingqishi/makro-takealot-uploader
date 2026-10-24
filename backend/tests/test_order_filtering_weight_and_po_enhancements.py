# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from backend.main import app
from app.database import get_db, SessionLocal
from app.models.makro_order import MakroOrder
from app.models.makro_purchase_order import MakroPurchaseOrder
from app.models.makro_order_status_log import MakroOrderStatusLog
from app.models.store import Store
from app.models.user import User
from app.utils.auth import create_access_token, get_current_user

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

def test_test_data_cleanup():
    """验证测试数据已经彻底清理"""
    db = SessionLocal()
    try:
        test_orders = db.query(MakroOrder).filter(
            MakroOrder.order_id.like("TEST_%")
        ).all()
        assert len(test_orders) == 0, f"Found unexpected test orders: {test_orders}"

        test_logs = db.query(MakroOrderStatusLog).filter(
            MakroOrderStatusLog.operator == "test_runner"
        ).all()
        assert len(test_logs) == 0, f"Found unexpected test status logs: {test_logs}"
    finally:
        db.close()

def test_date_filtering_affects_counts(auth_headers):
    """验证出单日期过滤后，fulfillment_counts 和 counts 联动更新"""
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.name == "hzh").first()
        assert store is not None, "Store hzh not found"
        store_id = store.id

        # 1. 无日期过滤
        resp_all = client.get(f"/api/store-orders/list?store_id={store_id}&page=1&page_size=20", headers=auth_headers)
        assert resp_all.status_code == 200
        data_all = resp_all.json()
        total_all = data_all["total"]
        counts_all = data_all["fulfillment_counts"]
        assert counts_all["all"] == total_all

        # 2. 传入不存在的日期范围 (例如 2020-01-01 至 2020-01-02)
        resp_empty = client.get(
            f"/api/store-orders/list?store_id={store_id}&order_date_start=2020-01-01&order_date_end=2020-01-02",
            headers=auth_headers
        )
        assert resp_empty.status_code == 200
        data_empty = resp_empty.json()
        assert data_empty["total"] == 0
        assert data_empty["fulfillment_counts"]["all"] == 0
        assert data_empty["fulfillment_counts"]["NEW"] == 0
        assert data_empty["fulfillment_counts"]["PURCHASED"] == 0

        # 3. 传入涵盖最近订单的日期范围 (如 2026-10-20 至 2026-10-22)
        resp_range = client.get(
            f"/api/store-orders/list?store_id={store_id}&order_date_start=2026-10-20&order_date_end=2026-10-22",
            headers=auth_headers
        )
        assert resp_range.status_code == 200
        data_range = resp_range.json()
        assert data_range["fulfillment_counts"]["all"] == data_range["total"]
        assert data_range["total"] > 0
        assert data_range["total"] <= total_all
    finally:
        db.close()

def test_weight_measured_flag_and_zero_weight(auth_headers):
    """验证承重区分已填写与未填写，且支持填写为 0 克"""
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.name == "hzh").first()
        store_id = store.id

        # 取出一条未填写的订单
        unmeasured = db.query(MakroOrder).filter(
            MakroOrder.store_id == store_id,
            MakroOrder.is_weight_measured == False
        ).first()

        if unmeasured:
            resp = client.get(f"/api/store-orders/list?store_id={store_id}&search={unmeasured.order_id}", headers=auth_headers)
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert len(items) > 0
            item = items[0]
            assert item["is_weight_measured"] is False
            assert item["weight_status"] == "PENDING"

            # 录入为 0 克
            update_resp = client.put(
                "/api/store-orders/update-weight-and-freight",
                headers=auth_headers,
                json={
                    "order_id": unmeasured.id,
                    "total_weight_g": 0.0,
                    "sync_to_catalog": False,
                    "sync_same_sku_orders": False
                }
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["total_weight_g"] == 0

            # 再次查询，应变为已填写 (is_weight_measured: True, weight_status: MEASURED)
            resp_after = client.get(f"/api/store-orders/list?store_id={store_id}&search={unmeasured.order_id}", headers=auth_headers)
            item_after = resp_after.json()["items"][0]
            assert item_after["is_weight_measured"] is True
            assert item_after["weight_status"] == "MEASURED"
            assert item_after["total_weight_g"] == 0
    finally:
        db.close()

def test_po_list_image_and_remove_order(auth_headers):
    """验证 PO 列表中携带商品图片以及一键剔除接口"""
    db = SessionLocal()
    try:
        po = db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == 15).first()
        assert po is not None, "PO 15 not found"

        # 1. 查询 PO 列表
        resp = client.get(f"/api/store-orders/po/list?store_id={po.store_id}", headers=auth_headers)
        assert resp.status_code == 200
        pos = resp.json()["items"]
        po_15 = next((p for p in pos if p["id"] == 15), None)
        assert po_15 is not None
        assert "orders" in po_15
        assert len(po_15["orders"]) > 0

        # 验证每个订单包含 image_url 和 is_weight_measured
        for o_item in po_15["orders"]:
            assert "image_url" in o_item
            assert "is_weight_measured" in o_item
            assert "product_title" in o_item

        # 2. 测试剔除订单接口 (创建临时测试订单加入 PO 15 后剔除)
        temp_order = MakroOrder(
            store_id=po.store_id,
            seller_id="TEST_SELLER",
            order_id="TEMP_TEST_REMOVE_001",
            status="processing",
            fulfillment_status="NEW",
            po_id=po.id
        )
        db.add(temp_order)
        db.commit()
        db.refresh(temp_order)

        temp_id = temp_order.id
        assert temp_order.po_id == po.id

        # 调用剔除接口
        del_resp = client.post(
            "/api/store-orders/po/remove-order",
            headers=auth_headers,
            json={"po_id": po.id, "order_id": temp_id}
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True

        # 验证数据库中 po_id 已被置空
        db.refresh(temp_order)
        assert temp_order.po_id is None

        # 清理临时订单与测试日志
        db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_sn == "TEMP_TEST_REMOVE_001").delete()
        db.delete(temp_order)
        db.commit()
    finally:
        db.close()

def test_order_payout_calculation_multi_qty(auth_headers):
    """验证多件商品订单预计回款计算无虚高膨胀 (OD438950317727797100)"""
    resp = client.get("/api/store-orders/list?store_id=3&search=OD438950317727797100", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    order = items[0]
    assert order["total_amount"] == 1731.0
    payout = float(order["calculated_payout"])
    print(f"OD438950317727797100: total_amount={order['total_amount']}, calculated_payout={payout}")
    # 期望预计回款在 520 ~ 540 元之间，绝不是 1648.61 元
    assert 520.0 <= payout <= 540.0, f"Expected payout around 529.65, got {payout}"
    assert payout < 1000.0, "Payout must not be multiplied by quantity twice!"

    # 验证 order_items 中的 unit_price 和 total_price
    oi = order["order_items"][0]
    assert oi["quantity"] == 3
    assert oi["unit_price"] == 577.0
    assert oi["total_price"] == 1731.0

def test_image_preview_function_exists():
    """验证 PO 展开明细使用 openImagePreview 且 orders.js 具备 previewImage 容错代理"""
    with open("backend/app/templates/views/tab_orders.html", "r", encoding="utf-8") as f:
        html = f.read()
    assert "openImagePreview(cleanImageUrl(subO.image_url))" in html
    assert "previewImage(cleanImageUrl(subO.image_url))" not in html

    with open("backend/app/static/js/modules/orders.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "previewImage(url)" in js
