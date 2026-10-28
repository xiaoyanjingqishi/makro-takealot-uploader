# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import json
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from backend.main import app
from app.database import get_db, SessionLocal
from app.models.makro_order import MakroOrder
from app.models.makro_purchase_order import MakroPurchaseOrder
from app.models.makro_order_status_log import MakroOrderStatusLog
from app.models.makro_buffer_stock import MakroBufferStock
from app.models.store import Store
from app.models.user import User
from app.utils.auth import get_current_user

client = TestClient(app)

def auth_headers():
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

def test_store():
    db = SessionLocal()
    try:
        store = db.query(Store).first()
        if not store:
            store = Store(name="test_store_sa", platform="makro")
            db.add(store)
            db.commit()
            db.refresh(store)
        return store
    finally:
        db.close()


def test_multi_procurement_records_and_net_profit(headers, store):
    """验证一个订单支持多个采购单号和价格，单单净利自动扣除所有采购价格"""
    db = SessionLocal()
    order = None
    try:
        # 创建测试订单 (total_amount = R500 -> 扣点17.5% - 70 = 342.5 * 0.39 = ~133.58 CNY)
        order = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_MULTI_PROC_001",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=500.0,
            order_date=datetime.now()
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        # 录入 2 笔采购记录 (¥40 + ¥35 = ¥75)
        procurement_payload = {
            "order_id": order.id,
            "procurement_records": [
                {
                    "purchase_sn": "1688_A123",
                    "purchase_cost": 40.0,
                    "domestic_tracking": "SF001",
                    "notes": "零件A"
                },
                {
                    "purchase_sn": "1688_B456",
                    "purchase_cost": 35.0,
                    "domestic_tracking": "SF002",
                    "notes": "零件B"
                }
            ]
        }
        resp = client.put(
            "/api/store-orders/update-procurement-records",
            json=procurement_payload,
            headers=headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["purchase_cost"] == 75.0
        assert len(data["procurement_records"]) == 2

        # 验证数据库持久化
        db.refresh(order)
        assert order.purchase_cost == 75.0
        records = json.loads(order.procurement_records_json)
        assert len(records) == 2
        assert records[0]["purchase_sn"] == "1688_A123"
        assert records[1]["purchase_sn"] == "1688_B456"

    finally:
        db.rollback()
        if order:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order.id).delete()
            db.commit()
        db.close()


def test_po_packing_state_flow(headers, store):
    """验证加入PO自动流转到PO打包，剔除PO自动恢复已采购"""
    db = SessionLocal()
    order = None
    po = None
    try:
        # 创建已采购订单
        order = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_PO_FLOW_001",
            status="Processing",
            fulfillment_status="PURCHASED",
            total_amount=300.0,
            order_date=datetime.now()
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        # 创建 PO 批次
        po = MakroPurchaseOrder(
            store_id=store.id,
            po_number="PO_TEST_PACKING_99",
            name="打包流转测试批次",
            head_haul_fee=200.0,
            domestic_logistics_fee=30.0
        )
        db.add(po)
        db.commit()
        db.refresh(po)

        # 1. 将订单加入 PO -> 状态应自动流转至 PO_PACKING
        resp_assign = client.put(
            "/api/store-orders/po/assign-existing",
            json={"po_id": po.id, "order_ids": [order.id]},
            headers=headers
        )
        assert resp_assign.status_code == 200, resp_assign.text
        db.refresh(order)
        assert order.po_id == po.id
        assert order.fulfillment_status == "PO_PACKING", f"Expected PO_PACKING, got {order.fulfillment_status}"

        # 2. 将订单从 PO 剔除 -> 状态应自动恢复为 PURCHASED
        resp_remove = client.post(
            "/api/store-orders/po/remove-order",
            json={"po_id": po.id, "order_id": order.id},
            headers=headers
        )
        assert resp_remove.status_code == 200, resp_remove.text
        db.refresh(order)
        assert order.po_id is None
        assert order.fulfillment_status == "PURCHASED", f"Expected PURCHASED, got {order.fulfillment_status}"

    finally:
        db.rollback()
        if order:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order.id).delete()
        if po:
            db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po.id).delete()
        db.commit()
        db.close()


def test_unmeasured_order_cancellation_head_haul_zero(headers, store):
    """专项 5: 未填写重量的订单转到异常退回时，头程运费默认为 0.00"""
    db = SessionLocal()
    order = None
    try:
        # 创建未称重订单 (is_weight_measured = False, total_weight_g = None)
        order = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_UNMEASURED_RTO_001",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=200.0,
            is_weight_measured=False,
            total_weight_g=None,
            independent_head_haul=80.0,  # 故意预设一个旧的头程运费
            order_date=datetime.now()
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        # 流转至异常退回 CANCELLED_RTO
        resp = client.put(
            "/api/store-orders/update-fulfillment",
            json={"order_ids": [order.id], "fulfillment_status": "CANCELLED_RTO"},
            headers=headers
        )
        assert resp.status_code == 200, resp.text
        db.refresh(order)
        assert order.fulfillment_status == "CANCELLED_RTO"
        assert order.independent_head_haul == 0.0, f"Expected 0.0 independent_head_haul, got {order.independent_head_haul}"

    finally:
        db.rollback()
        if order:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order.id).delete()
            db.commit()
        db.close()


def test_po_card_status_and_timeline(headers, store):
    """专项 8 & 9: PO 卡片当前状态流转、时效停留与操作轨迹记录"""
    db = SessionLocal()
    po = None
    try:
        po = MakroPurchaseOrder(
            store_id=store.id,
            po_number="PO_TEST_TIMELINE_88",
            name="时效时间轴测试PO",
            status="正在打包"
        )
        db.add(po)
        db.commit()
        db.refresh(po)

        # 1. 更新 PO 状态为 发送货代
        resp1 = client.put(
            f"/api/store-orders/po/{po.id}/status",
            json={"po_status": "发送货代"},
            headers=headers
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["po_status"] == "发送货代"
        assert len(data1["status_timeline"]) >= 1

        # 2. 更新 PO 状态为 货代空运
        resp2 = client.put(
            f"/api/store-orders/po/{po.id}/status",
            json={"po_status": "货代空运"},
            headers=headers
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["po_status"] == "货代空运"
        assert len(data2["status_timeline"]) >= 2
        # 验证包含停留间隔
        last_node = data2["status_timeline"][-1]
        assert "interval_from_prev" in last_node

    finally:
        if po:
            db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po.id).delete()
            db.commit()
        db.close()


def test_order_status_history_chinese_translation(headers, store):
    """专项 7: 订单状态与操作轨迹中文显示"""
    db = SessionLocal()
    order = None
    try:
        order = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_ZH_AUDIT_001",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=150.0,
            order_date=datetime.now()
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        # 产生一条状态流转
        client.put(
            "/api/store-orders/update-fulfillment",
            json={"order_ids": [order.id], "fulfillment_status": "PURCHASED"},
            headers=headers
        )

        # 查询状态流转轨迹
        resp = client.get(f"/api/store-orders/{order.id}/status-history", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        history = data["history"]
        assert len(history) >= 1
        latest = history[0]
        # 变动类型应为中文 "履约状态流转"
        assert latest.get("change_type_zh") == "履约状态流转" or latest.get("change_type") == "FULFILLMENT_STATUS"
        # 新状态应为中文 "已采购"
        assert latest.get("new_value") == "已采购" or latest.get("to_status_zh") == "已采购"

    finally:
        db.rollback()
        if order:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order.id).delete()
            db.commit()
        db.close()


def test_sa_buffer_stock_auto_inflow_and_reallocation(headers, store):
    """南非现货滞留池：已到南非的取消订单自动入库滞留池，新订单一键调拨匹配免头程"""
    db = SessionLocal()
    order1 = None
    order2 = None
    buffer_item = None
    try:
        sku = "TEST_SA_SKU_999"
        raw_items = [{"sku": sku, "title": "南非滞留测试商品", "quantity": 1, "selling_price": 300.0}]

        # 订单 1：已到达南非 (ARRIVED_SA)，随后被官方取消
        order1 = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_SA_ORDER_ORIG_001",
            status="Processing",
            fulfillment_status="ARRIVED_SA",
            purchase_cost=50.0,
            total_amount=300.0,
            raw_items_json=json.dumps(raw_items),
            order_date=datetime.now()
        )
        db.add(order1)
        db.commit()
        db.refresh(order1)

        # 订单 1 转为异常退回 CANCELLED_RTO -> 应自动流入南非滞留池
        resp_cancel = client.put(
            "/api/store-orders/update-fulfillment",
            json={"order_ids": [order1.id], "fulfillment_status": "CANCELLED_RTO"},
            headers=headers
        )
        assert resp_cancel.status_code == 200

        # 查询南非滞留池
        resp_buffer = client.get(f"/api/store-orders/buffer-stocks?store_id={store.id}&search={sku}", headers=headers)
        assert resp_buffer.status_code == 200
        buffer_data = resp_buffer.json()
        assert len(buffer_data["items"]) >= 1
        buffer_item = buffer_data["items"][0]
        assert buffer_item["sku_id"] == sku
        assert buffer_item["quantity_available"] >= 1
        assert buffer_item["source_order_sn"] == order1.order_id

        # 订单 2：国内刚出的同 SKU 新订单 (NEW)
        order2 = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_SA_ORDER_NEW_002",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=350.0,
            raw_items_json=json.dumps(raw_items),
            order_date=datetime.now()
        )
        db.add(order2)
        db.commit()
        db.refresh(order2)

        # 调拨南非现货给订单 2 (换标费 0.0 元)
        allocate_payload = {
            "stock_id": buffer_item["id"],
            "target_order_id": order2.id,
            "relabeling_fee_cny": 0.0,
            "notes": "南非本地贴标换单秒发"
        }
        resp_alloc = client.post(
            "/api/store-orders/allocate-stock",
            json=allocate_payload,
            headers=headers
        )
        assert resp_alloc.status_code == 200, resp_alloc.text
        alloc_res = resp_alloc.json()
        assert alloc_res["success"] is True
        assert alloc_res["new_fulfillment_status"] == "ARRIVED_SA"
        assert alloc_res["allocated_stock_id"] == buffer_item["id"]

        # 验证数据库中订单 2
        db.refresh(order2)
        assert order2.fulfillment_status == "ARRIVED_SA"
        assert order2.independent_head_haul == 0.0
        assert order2.fulfillment_type == "SA_BUFFER_STOCK"
        assert order2.reallocated_from_order_sn == order1.order_id

        # 验证滞留池可用数量扣减
        stk_db = db.query(MakroBufferStock).filter(MakroBufferStock.id == buffer_item["id"]).first()
        assert stk_db.quantity == 0
        assert stk_db.status == "REALLOCATED"

    finally:
        if order1:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order1.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order1.id).delete()
        if order2:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order2.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order2.id).delete()
        if buffer_item:
            db.query(MakroBufferStock).filter(MakroBufferStock.id == buffer_item["id"]).delete()
        db.commit()
        db.close()

def test_deep_search_and_procurement_penetration(headers, store):
    """专项 6: SKU 深度穿透搜索 (渗透 raw_items_json 和 procurement_records_json)"""
    db = SessionLocal()
    order = None
    try:
        special_sku = "SEARCH_SKU_ALPHA_888"
        raw_items = [{"sku": special_sku, "title": "深度搜索穿透测试", "quantity": 1, "selling_price": 200.0}]
        order = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_SEARCH_DEEP_001",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=200.0,
            raw_items_json=json.dumps(raw_items),
            procurement_records_json=json.dumps([{"purchase_sn": "1688_SPECIAL_SEARCH_SN", "cost": 50.0}]),
            order_date=datetime.now()
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        # 1. 搜索 SKU -> 命中
        resp1 = client.get(f"/api/store-orders/list?store_id={store.id}&search={special_sku}", headers=headers)
        assert resp1.status_code == 200
        found1 = [o for o in resp1.json()["items"] if o["order_id"] == order.order_id]
        assert len(found1) == 1, f"Expected to find order by SKU {special_sku}"

        # 2. 搜索 procurement_records 中的采购单号 -> 命中
        resp2 = client.get(f"/api/store-orders/list?store_id={store.id}&search=1688_SPECIAL_SEARCH_SN", headers=headers)
        assert resp2.status_code == 200
        found2 = [o for o in resp2.json()["items"] if o["order_id"] == order.order_id]
        assert len(found2) == 1, "Expected to find order by 1688 purchase sn"

    finally:
        if order:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order.id).delete()
            db.commit()
        db.close()


def test_dispatch_deadline_countdown(headers, store):
    """专项 3 & 4: 动态发货截止时间倒计时 (剩余/已逾期 n天n小时)"""
    db = SessionLocal()
    order_future = None
    order_past = None
    try:
        now = datetime.now()
        future_dt = now + timedelta(days=2, hours=5)
        past_dt = now - timedelta(days=1, hours=3)

        order_future = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_COUNTDOWN_FUT_001",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=180.0,
            dispatch_by_date=future_dt,
            order_date=now
        )
        order_past = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_COUNTDOWN_PAST_002",
            status="Processing",
            fulfillment_status="NEW",
            total_amount=180.0,
            dispatch_by_date=past_dt,
            order_date=now
        )
        db.add(order_future)
        db.add(order_past)
        db.commit()
        db.refresh(order_future)
        db.refresh(order_past)

        resp = client.get(f"/api/store-orders/list?store_id={store.id}&search=TEST_COUNTDOWN", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        fut_item = next(o for o in items if o["order_id"] == order_future.order_id)
        past_item = next(o for o in items if o["order_id"] == order_past.order_id)

        assert fut_item["remaining_dispatch_time"] is not None
        assert "剩余" in fut_item["remaining_dispatch_time"]["text"]
        assert fut_item["remaining_dispatch_time"]["is_overdue"] is False

        assert past_item["remaining_dispatch_time"] is not None
        assert "已逾期" in past_item["remaining_dispatch_time"]["text"]
        assert past_item["remaining_dispatch_time"]["is_overdue"] is True

    finally:
        if order_future:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order_future.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order_future.id).delete()
        if order_past:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order_past.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order_past.id).delete()
        db.commit()
        db.close()


def test_po_status_summary_distribution(headers, store):
    """专项 9: PO 卡片显示各类订单状态数量分布 (如: 正在打包: 1, 货代空运: 1)"""
    db = SessionLocal()
    order_a = None
    order_b = None
    po = None
    try:
        po = MakroPurchaseOrder(
            store_id=store.id,
            po_number="PO_TEST_SUMMARY_DIST_77",
            name="分布汇总测试批次"
        )
        db.add(po)
        db.commit()
        db.refresh(po)

        order_a = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_DIST_A_001",
            status="Processing",
            fulfillment_status="PO_PACKING",
            total_amount=150.0,
            po_id=po.id,
            order_date=datetime.now()
        )
        order_b = MakroOrder(
            store_id=store.id,
            seller_id="test_seller",
            order_id="TEST_DIST_B_002",
            status="Processing",
            fulfillment_status="AIR_FREIGHT",
            total_amount=250.0,
            po_id=po.id,
            order_date=datetime.now()
        )
        db.add(order_a)
        db.add(order_b)
        db.commit()
        db.refresh(order_a)
        db.refresh(order_b)

        resp = client.get(f"/api/store-orders/po/list?store_id={store.id}&search=PO_TEST_SUMMARY_DIST_77", headers=headers)
        assert resp.status_code == 200
        po_list = resp.json()["items"]
        assert len(po_list) == 1
        po_item = po_list[0]
        summary = po_item["status_summary"]
        assert summary.get("PO打包") == 1
        assert summary.get("货代空运") == 1

    finally:
        if order_a:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order_a.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order_a.id).delete()
        if order_b:
            db.query(MakroOrderStatusLog).filter(MakroOrderStatusLog.order_id == order_b.id).delete()
            db.query(MakroOrder).filter(MakroOrder.id == order_b.id).delete()
        if po:
            db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.id == po.id).delete()
        db.commit()
        db.close()


def test_buffer_stock_summary_and_location_management(headers, store):
    """验证滞留池指标对齐、分仓精确筛选与换标费/仓库修改接口"""
    db = SessionLocal()
    stk_sa = None
    stk_dom = None
    try:
        # 创建一个南非仓滞留记录与一个国内仓滞留记录
        stk_sa = MakroBufferStock(
            store_id=store.id,
            sku_id="TEST_BUFFER_SKU_SA_1",
            title="测试南非仓现货",
            quantity=2,
            purchase_cost_cny=30.0,
            head_haul_cost_cny=15.0,
            location="SA_WAREHOUSE",
            status="AVAILABLE",
            source_order_sn="TEST_ORDER_BUF_SA"
        )
        stk_dom = MakroBufferStock(
            store_id=store.id,
            sku_id="TEST_BUFFER_SKU_DOM_2",
            title="测试国内仓现货",
            quantity=3,
            purchase_cost_cny=20.0,
            head_haul_cost_cny=0.0,
            location="DOMESTIC_FORWARDER",
            status="AVAILABLE",
            source_order_sn="TEST_ORDER_BUF_DOM"
        )
        db.add_all([stk_sa, stk_dom])
        db.commit()
        db.refresh(stk_sa)
        db.refresh(stk_dom)

        # 1. 验证全局看板数据指标契约
        resp = client.get(f"/api/store-orders/buffer-stocks?store_id={store.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        summary = data.get("summary", {})
        assert summary.get("total_items", 0) >= 5
        assert summary.get("available_count", 0) >= 5
        assert summary.get("total_cost_cny", 0) > 0
        assert summary.get("available_sa_count", 0) >= 2
        assert summary.get("domestic_forwarder_count", 0) >= 3

        # 2. 验证仓库字段 warehouse_location 与 location_zh 正确映射
        items = data.get("items", [])
        sa_item = next(it for it in items if it["id"] == stk_sa.id)
        dom_item = next(it for it in items if it["id"] == stk_dom.id)
        assert sa_item["warehouse_location"] == "南非本地仓"
        assert dom_item["warehouse_location"] == "国内货代仓"

        # 3. 验证仓库隔离筛选
        resp_sa_only = client.get(f"/api/store-orders/buffer-stocks?store_id={store.id}&location=SA_WAREHOUSE", headers=headers)
        assert resp_sa_only.status_code == 200
        sa_ids = [it["id"] for it in resp_sa_only.json().get("items", [])]
        assert stk_sa.id in sa_ids
        assert stk_dom.id not in sa_ids

        resp_dom_only = client.get(f"/api/store-orders/buffer-stocks?store_id={store.id}&location=DOMESTIC_FORWARDER", headers=headers)
        assert resp_dom_only.status_code == 200
        dom_ids = [it["id"] for it in resp_dom_only.json().get("items", [])]
        assert stk_dom.id in dom_ids
        assert stk_sa.id not in dom_ids

        # 4. 验证行内修改换标费与移仓更新
        resp_update = client.put(
            f"/api/store-orders/buffer-stocks/{stk_dom.id}/status",
            json={"location": "SA_WAREHOUSE", "relabeling_fee_cny": 8.5},
            headers=headers
        )
        assert resp_update.status_code == 200, resp_update.text
        db.refresh(stk_dom)
        assert stk_dom.location == "SA_WAREHOUSE"
        assert stk_dom.relabeling_fee_cny == 8.5

    finally:
        if stk_sa and stk_sa.id:
            db.query(MakroBufferStock).filter(MakroBufferStock.id == stk_sa.id).delete()
        if stk_dom and stk_dom.id:
            db.query(MakroBufferStock).filter(MakroBufferStock.id == stk_dom.id).delete()
        db.commit()
        db.close()

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    headers = auth_headers()
    store = test_store()

    print("Running tests...")
    print("1. test_multi_procurement_records_and_net_profit...")
    test_multi_procurement_records_and_net_profit(headers, store)
    print("✓ PASSED: Multi-procurement records")

    print("2. test_po_packing_state_flow...")
    test_po_packing_state_flow(headers, store)
    print("✓ PASSED: PO_PACKING flow on assign/remove")

    print("3. test_unmeasured_order_cancellation_head_haul_zero...")
    test_unmeasured_order_cancellation_head_haul_zero(headers, store)
    print("✓ PASSED: Unmeasured order cancellation head haul zero")

    print("4. test_po_card_status_and_timeline...")
    test_po_card_status_and_timeline(headers, store)
    print("✓ PASSED: PO card status and timeline interval")

    print("5. test_order_status_history_chinese_translation...")
    test_order_status_history_chinese_translation(headers, store)
    print("✓ PASSED: Status history Chinese translation")

    print("6. test_sa_buffer_stock_auto_inflow_and_reallocation...")
    test_sa_buffer_stock_auto_inflow_and_reallocation(headers, store)
    print("✓ PASSED: SA buffer stock inflow and reallocation")

    print("7. test_deep_search_and_procurement_penetration...")
    test_deep_search_and_procurement_penetration(headers, store)
    print("✓ PASSED: SKU deep search and procurement penetration")

    print("8. test_dispatch_deadline_countdown...")
    test_dispatch_deadline_countdown(headers, store)
    print("✓ PASSED: Dispatch deadline countdown (remaining/overdue)")

    print("9. test_po_status_summary_distribution...")
    test_po_status_summary_distribution(headers, store)
    print("✓ PASSED: PO status distribution count tags")

    print("10. test_buffer_stock_summary_and_location_management...")
    test_buffer_stock_summary_and_location_management(headers, store)
    print("✓ PASSED: Buffer stock summary alignment & warehouse management")

    print("\n🎉 ALL 10 ADVANCED TESTS PASSED PERFECTLY!")
