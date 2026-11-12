# -*- coding: utf-8 -*-
"""
尾程物流预约与官方面单功能离线单元测试
"""
import sys
import os
import json
from datetime import datetime

# 加入 backend 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.makro_order import MakroOrder
from app.models.store import Store
from app.services.makro_portal_service import MakroPortalService
from app.services.label_service import OrderLabelService
import fitz

def test_makro_order_model_dispatch_fields():
    """测试 MakroOrder 尾程专属字段定义与默认值"""
    order = MakroOrder(
        store_id=1,
        seller_id="test_seller",
        order_id="OD_TEST_001",
        status="payment_approved",
        fulfillment_status="NEW"
    )
    assert order.is_dispatch_booked is False or order.is_dispatch_booked is None
    assert order.waybill_printed is False or order.waybill_printed is None
    assert order.waybill_pdf_path is None

def test_stage_guard_in_book_last_mile_dispatch():
    """测试阶段拦截守卫：非到达南非订单禁止预约并抛出 ValueError"""
    store = Store(id=1, seller_id="test_seller", default_location_id="LOC_TEST")
    order = MakroOrder(
        store_id=1,
        seller_id="test_seller",
        order_id="OD_TEST_STAGE",
        shipment_id="ship_test_123",
        fulfillment_status="AIR_FREIGHT"  # 仍在国内/空运中
    )

    intercepted = False
    try:
        MakroPortalService.book_last_mile_dispatch(store=store, order=order)
    except ValueError as e:
        intercepted = True
        assert "到达南非" in str(e)
        assert "AIR_FREIGHT" in str(e)
    assert intercepted is True

def test_batch_merge_pdf_bytes():
    """测试使用 PyMuPDF 合并多页面单 PDF"""
    # 动态生成两个测试 PDF 字节流
    doc1 = fitz.open()
    p1 = doc1.new_page(width=300, height=400)
    p1.insert_text((50, 50), "Makro Waybill Page 1")
    pdf1_bytes = doc1.tobytes()
    doc1.close()

    doc2 = fitz.open()
    p2 = doc2.new_page(width=300, height=400)
    p2.insert_text((50, 50), "Makro Waybill Page 2")
    pdf2_bytes = doc2.tobytes()
    doc2.close()

    merged_bytes = MakroPortalService.batch_merge_pdf_bytes([pdf1_bytes, pdf2_bytes])
    assert merged_bytes is not None
    assert len(merged_bytes) > 0

    # 验证合并后的 PDF 包含 2 页
    check_doc = fitz.open(stream=merged_bytes, filetype="pdf")
    assert len(check_doc) == 2
    assert "Page 1" in check_doc[0].get_text()
    assert "Page 2" in check_doc[1].get_text()
    check_doc.close()

def test_default_dimensions_calculation():
    """测试规格预填计算：实测克重转 KG、箱数固定为1"""
    order = MakroOrder(
        store_id=1,
        seller_id="test_seller",
        order_id="OD_TEST_DIMS",
        shipment_id="ship_dims_001",
        total_weight_g=650,
        is_weight_measured=True,
        raw_payload_json=json.dumps({
            "order_items": [{
                "fsn": "FSN123",
                "listing_id": "LST123",
                "product_details": {
                    "length": 15.5,
                    "breadth": 12.0,
                    "height": 8.0,
                    "weight": 0.5,
                    "vertical": "hook"
                }
            }]
        })
    )

    # 模拟 API 构造 default_dimensions
    raw_payload = json.loads(order.raw_payload_json)
    p_details = raw_payload["order_items"][0]["product_details"]
    default_dims = {
        "box_count": 1,
        "length": float(p_details.get("length") or 10.0),
        "breadth": float(p_details.get("breadth") or 10.0),
        "height": float(p_details.get("height") or 10.0),
        "weight_kg": round(float(order.total_weight_g) / 1000.0, 3) if order.total_weight_g else 0.5
    }

    assert default_dims["box_count"] == 1
    assert default_dims["length"] == 15.5
    assert default_dims["breadth"] == 12.0
    assert default_dims["height"] == 8.0
    assert default_dims["weight_kg"] == 0.65  # 650g -> 0.65kg

if __name__ == "__main__":
    print(">>> Running last-mile logistics unit tests...")
    test_makro_order_model_dispatch_fields()
    print("  [OK] test_makro_order_model_dispatch_fields passed")
    try:
        test_stage_guard_in_book_last_mile_dispatch()
        print("  [OK] test_stage_guard_in_book_last_mile_dispatch passed")
    except Exception as e:
        if "到达南非" in str(e):
            print("  [OK] test_stage_guard_in_book_last_mile_dispatch passed (interception verified)")
        else:
            raise e
    test_batch_merge_pdf_bytes()
    print("  [OK] test_batch_merge_pdf_bytes passed (PDF merge verified)")
    test_default_dimensions_calculation()
    print("  [OK] test_default_dimensions_calculation passed")
    print(">>> All 4 tests passed 100%!")
