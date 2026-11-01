# -*- coding: utf-8 -*-
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from fastapi.testclient import TestClient
from backend.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.makro_order import MakroOrder
from app.models.makro_buffer_stock import MakroBufferStock
from app.models.store_product_reprice import StoreProductReprice
from app.models.makro_piggyback import MakroPiggybackItem
from app.utils.auth import get_current_user
import json

db = SessionLocal()
try:
    user = db.query(User).filter(User.username == 'admin').first()
    if not user:
        user = User(username='admin', is_admin=True, hashed_password='x')
        db.add(user)
        db.commit()
        db.refresh(user)
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    print("Running test suite for buffer stock manual add & order filter...")

    # -------------------------------------------------------------
    # Test 1: Buffer Stock SKU Lookup API
    # -------------------------------------------------------------
    order = db.query(MakroOrder).filter(MakroOrder.raw_items_json.isnot(None)).first()
    assert order, "Need at least one order in DB for testing"
    test_sku = "GP261008113831A2D6"
    if order.raw_items_json:
        try:
            items = json.loads(order.raw_items_json)
            if items and items[0].get('sku'):
                test_sku = items[0]['sku']
        except Exception:
            pass

    res_lookup = client.get(f"/api/store-orders/buffer-stocks/lookup-sku?store_id={order.store_id}&sku={test_sku}")
    assert res_lookup.status_code == 200, res_lookup.text
    data_lookup = res_lookup.json()
    assert "sku" in data_lookup
    assert "title" in data_lookup
    assert "found" in data_lookup
    assert "existing_stock_count" in data_lookup
    print(f"✓ 1. Buffer stock lookup SKU passed! SKU={test_sku}, found={data_lookup['found']}, title={data_lookup['title'][:30]}...")

    # -------------------------------------------------------------
    # Test 2: Buffer Stock Manual Add by SKU
    # -------------------------------------------------------------
    add_payload = {
        "store_id": order.store_id,
        "sku_id": "TEST_MANUAL_SKU_888",
        "title": "单元测试手动入库商品",
        "quantity": 3,
        "location": "SA_WAREHOUSE",
        "purchase_cost_cny": 35.5,
        "head_haul_cost_cny": 12.0,
        "relabeling_fee_cny": 2.5,
        "notes": "自动化测试手动入库批次"
    }
    res_add = client.post("/api/store-orders/buffer-stocks/create", json=add_payload)
    assert res_add.status_code == 200, res_add.text
    add_data = res_add.json()
    assert add_data["success"] is True
    created_id = add_data["stock_id"]

    # Verify query in buffer stock list
    res_list = client.get(f"/api/store-orders/buffer-stocks?store_id={order.store_id}&search=TEST_MANUAL_SKU_888")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert any(it["id"] == created_id for it in list_data["items"]), "Created stock not found in buffer list"
    matched_stock = next(it for it in list_data["items"] if it["id"] == created_id)
    assert matched_stock["quantity_available"] == 3
    assert matched_stock["location"] == "SA_WAREHOUSE"
    print(f"✓ 2. Manual stock creation by SKU passed! Stock ID={created_id}, Qty={matched_stock['quantity_available']}")

    # Clean up test stock
    stock_record = db.query(MakroBufferStock).filter(MakroBufferStock.id == created_id).first()
    if stock_record:
        db.delete(stock_record)
        db.commit()

    # -------------------------------------------------------------
    # Test 3: Reprice Monitoring "是否出单" (has_orders) Filter
    # -------------------------------------------------------------
    # 3.1 All items
    res_rep_all = client.get(f"/api/reprice/list?store_id={order.store_id}&has_orders=ALL")
    assert res_rep_all.status_code == 200, res_rep_all.text
    rep_all_data = res_rep_all.json()
    assert "kpi" in rep_all_data
    assert "ordered_count" in rep_all_data["kpi"]
    assert "no_order_count" in rep_all_data["kpi"]
    total_reprice = rep_all_data["total"]

    # 3.2 Filter ORDERED
    res_rep_ordered = client.get(f"/api/reprice/list?store_id={order.store_id}&has_orders=ORDERED")
    assert res_rep_ordered.status_code == 200
    rep_ord_data = res_rep_ordered.json()
    for it in rep_ord_data["items"]:
        assert it["has_orders"] is True
        assert it["order_count"] > 0
    print(f"✓ 3. Reprice filter [has_orders=ORDERED] passed! Total ordered in store={rep_ord_data['total']}, KPI ordered={rep_all_data['kpi']['ordered_count']}")

    # 3.3 Filter NO_ORDER
    res_rep_no = client.get(f"/api/reprice/list?store_id={order.store_id}&has_orders=NO_ORDER")
    assert res_rep_no.status_code == 200
    rep_no_data = res_rep_no.json()
    for it in rep_no_data["items"]:
        assert it["has_orders"] is False
        assert it["order_count"] == 0
    print(f"✓ 3. Reprice filter [has_orders=NO_ORDER] passed! Total no-order in store={rep_no_data['total']}, Sum={rep_ord_data['total'] + rep_no_data['total']} == {total_reprice}")
    assert rep_ord_data["total"] + rep_no_data["total"] == total_reprice

    # -------------------------------------------------------------
    # Test 4: Piggyback Management "是否出单" (has_orders) Filter
    # -------------------------------------------------------------
    # 4.1 Filter ORDERED
    res_piggy_ord = client.get(f"/api/piggyback/items?store_id={order.store_id}&has_orders=ORDERED")
    assert res_piggy_ord.status_code == 200
    piggy_ord_data = res_piggy_ord.json()
    for it in piggy_ord_data["items"]:
        assert it["has_orders"] is True
        assert it["order_count"] > 0
    print(f"✓ 4. Piggyback filter [has_orders=ORDERED] passed! Total ordered in store={piggy_ord_data['total']}")

    # 4.2 Filter NO_ORDER
    res_piggy_no = client.get(f"/api/piggyback/items?store_id={order.store_id}&has_orders=NO_ORDER")
    assert res_piggy_no.status_code == 200
    piggy_no_data = res_piggy_no.json()
    for it in piggy_no_data["items"]:
        assert it["has_orders"] is False
        assert it["order_count"] == 0
    print(f"✓ 4. Piggyback filter [has_orders=NO_ORDER] passed! Total no-order in store={piggy_no_data['total']}")

    print("\n🎉 ALL TESTS PASSED PERFECTLY!")
finally:
    db.close()
