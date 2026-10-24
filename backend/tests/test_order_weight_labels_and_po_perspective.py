# -*- coding: utf-8 -*-
"""
单元测试：订单打包40x30mm标签PDF生成、单品称重录入、商品库记忆联动、
订单与PO双重视角成本利润核算、头程对账差异
"""

import unittest
import json
from unittest.mock import patch
from datetime import datetime
from fastapi.testclient import TestClient
from main import app
from app.database import SessionLocal
from app.models.store import Store
from app.models.user import User
from app.models.makro_order import MakroOrder
from app.models.makro_purchase_order import MakroPurchaseOrder
from app.models.makro_listing import MakroListing
from app.models.makro_piggyback import MakroPiggybackItem
from app.models.setting import SystemSetting
from app.utils.auth import create_access_token
import fitz


class TestOrderWeightLabelsAndPoPerspective(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        # 准备或获取默认测试店铺
        self.store = self.db.query(Store).filter(Store.is_default == True).first()
        if not self.store:
            self.store = Store(
                name="标签与核算测试店铺",
                seller_id="test_seller_label_01",
                is_active=True,
                is_default=True
            )
            self.db.add(self.store)
            self.db.commit()
            self.db.refresh(self.store)

        # 准备管理员用户 Token
        self.user = self.db.query(User).filter(User.username == "admin").first()
        if not self.user:
            self.user = User(username="admin", role="ADMIN", is_active=True)
            self.user.set_password("admin123")
            self.db.add(self.user)
            self.db.commit()
            self.db.refresh(self.user)

        token = create_access_token({"sub": self.user.username, "user_id": self.user.id, "role": self.user.role})
        self.headers = {"Authorization": f"Bearer {token}"}

        # 确保全局空运单价配置存在
        s_rate = self.db.query(SystemSetting).filter(SystemSetting.key == "default_air_freight_rate").first()
        if not s_rate:
            s_rate = SystemSetting(key="default_air_freight_rate", value="90.0")
            self.db.add(s_rate)
        else:
            s_rate.value = "90.0"
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_label_pdf_generation_single_and_batch(self):
        """测试 1: 40x30mm 标签 PDF 生成 (单单及批量)"""
        # 创建测试订单
        test_sn = f"OD_TEST_LBL_{int(datetime.now().timestamp())}"
        order = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id=test_sn,
            status="pending_rtd",
            total_amount=500.0,
            order_date=datetime(2026, 10, 14, 16, 35)
        )
        self.db.add(order)
        self.db.commit()
        self.db.refresh(order)

        try:
            # 1. 测试单张标签下载
            resp = self.client.get(f"/api/store-orders/labels/{order.id}/pdf", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["content-type"], "application/pdf")
            self.assertIn(f"label_{test_sn}.pdf", resp.headers["content-disposition"])

            # 校验 PDF 尺寸与内容
            doc = fitz.open(stream=resp.content, filetype="pdf")
            self.assertEqual(len(doc), 1)
            page = doc[0]
            # 40mm ≈ 113.39 pt, 30mm ≈ 85.04 pt
            self.assertAlmostEqual(page.rect.width, 113.39, delta=0.5)
            self.assertAlmostEqual(page.rect.height, 85.04, delta=0.5)
            text = page.get_text()
            self.assertIn(test_sn, text)
            self.assertIn("2026-1014-1635", text)
            doc.close()

            # 2. 测试批量连打标签下载
            batch_resp = self.client.post(
                "/api/store-orders/batch-labels/pdf",
                headers=self.headers,
                json={"order_ids": [order.id]}
            )
            self.assertEqual(batch_resp.status_code, 200)
            self.assertEqual(batch_resp.headers["content-type"], "application/pdf")
            b_doc = fitz.open(stream=batch_resp.content, filetype="pdf")
            self.assertEqual(len(b_doc), 1)
            b_doc.close()

            print("[OK] Test 1: 40x30mm single & batch PDF label generation passed!")
        finally:
            self.db.delete(order)
            self.db.commit()

    def test_weight_update_catalog_sync_and_same_sku_diffusion(self):
        """测试 2: 称重录入、商品库记忆双向同步、同 SKU 订单智能扩散"""
        sku_code = "SKU_WEIGHT_TEST_001"
        fsn_code = "FSN_WEIGHT_TEST_001"

        # 清理历史残留
        self.db.query(MakroListing).filter(MakroListing.sku_id == sku_code).delete()
        self.db.query(MakroPiggybackItem).filter(MakroPiggybackItem.seller_sku == sku_code).delete()
        self.db.commit()

        # 创建一个 Listing
        listing = MakroListing(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            sku_id=sku_code,
            product_id=fsn_code,
            title="称重测试商品",
            internal_state="ACTIVE",
            weight=0.0
        )
        self.db.add(listing)

        # 创建两笔同 SKU 订单: order1 和 order2
        items_payload = json.dumps([{
            "title": "称重测试商品",
            "sku_id": sku_code,
            "fsn": fsn_code,
            "quantity": 2,
            "price": 100.0,
            "weight_g": 0
        }])

        order1 = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_W_DIFFUSE_01",
            status="pending_rtd",
            total_amount=200.0,
            raw_items_json=items_payload,
            total_weight_g=0
        )
        order2 = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_W_DIFFUSE_02",
            status="pending_rtd",
            total_amount=200.0,
            raw_items_json=items_payload,
            total_weight_g=0
        )
        self.db.add_all([order1, order2])
        self.db.commit()
        self.db.refresh(order1)
        self.db.refresh(order2)

        try:
            # 为 order1 录入称重 (单个 350g, 2件即 700g), 空运单价 90元/kg -> 头程应为 0.7kg * 90 = 63.0元
            w_resp = self.client.put(
                "/api/store-orders/update-weight-and-freight",
                headers=self.headers,
                json={
                    "order_id": order1.id,
                    "items_weight": {sku_code: 350},
                    "air_freight_rate": 90.0,
                    "sync_to_catalog": True,
                    "sync_same_sku_orders": True
                }
            )
            self.assertEqual(w_resp.status_code, 200)
            w_data = w_resp.json()
            self.assertTrue(w_data["success"])
            self.assertEqual(w_data["total_weight_g"], 700)
            self.assertEqual(w_data["head_haul_cost"], 63.0)
            self.assertGreaterEqual(w_data["synced_catalog_count"], 1)
            self.assertGreaterEqual(w_data["diffused_orders_count"], 1)

            # 验证商品资料库 Listing 的 weight 是否更新为 0.35kg
            self.db.refresh(listing)
            self.assertEqual(listing.weight, 0.35)

            # 验证同 SKU 的 order2 是否被智能扩散更新
            self.db.refresh(order2)
            self.assertEqual(order2.total_weight_g, 700)
            self.assertEqual(order2.independent_head_haul, 63.0)

            print("[OK] Test 2: Weight update, catalog sync & same SKU diffusion passed!")
        finally:
            self.db.delete(order1)
            self.db.delete(order2)
            self.db.delete(listing)
            self.db.commit()

    def test_dual_perspective_cost_and_reconciliation(self):
        """测试 3: 订单与 PO 双重视角利润核算、国内运费 PO 平摊、头程对账差异"""
        po_sn = f"PO_TEST_{int(datetime.now().timestamp())}"
        po = MakroPurchaseOrder(
            po_number=po_sn,
            store_id=self.store.id,
            name="双重视角核算测试批次",
            domestic_logistics_fee=30.0,  # PO 录入国内快递费 30 元
            head_haul_fee=500.0,          # 货代实收账单 500 元
            logistics_loss_fee=10.0       # 物流损耗 10 元
        )
        self.db.add(po)
        self.db.flush()

        # 创建两笔归档入 PO 的订单
        # order_a: 总重 4000g (4kg @ 60 = 240元), 采购价 100元, 回款 500元
        order_a = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_PO_TEST_A",
            status="pending_rtd",
            total_amount=1500.0,
            purchase_cost=100.0,
            total_weight_g=4000,
            air_freight_rate=60.0,
            po_id=po.id
        )
        # order_b: 总重 5000g (5kg @ 60 = 300元), 采购价 150元, 回款 600元
        order_b = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_PO_TEST_B",
            status="pending_rtd",
            total_amount=1800.0,
            purchase_cost=150.0,
            total_weight_g=5000,
            air_freight_rate=60.0,
            po_id=po.id
        )
        self.db.add_all([order_a, order_b])
        self.db.commit()
        self.db.refresh(po)

        try:
            # 1. 验证订单视角列表查询
            orders_resp = self.client.get(
                f"/api/store-orders/list?store_id={self.store.id}&search=OD_PO_TEST_",
                headers=self.headers
            )
            self.assertEqual(orders_resp.status_code, 200)
            res_orders = orders_resp.json()["items"]
            self.assertEqual(len(res_orders), 2)

            item_a = next(o for o in res_orders if o["order_id"] == "OD_PO_TEST_A")
            # 验证国内运费必须由 PO 平摊: 30 / 2 = 15.0元
            self.assertEqual(item_a["domestic_logistics_fee"], 15.0)
            # 验证头程必须按重计算: 4kg * 60 = 240.0元
            self.assertEqual(item_a["head_haul_cost"], 240.0)
            self.assertEqual(item_a["weight_status"], "MEASURED")

            # 2. 验证 PO 视角列表查询
            po_resp = self.client.get(
                f"/api/store-orders/po/list?store_id={self.store.id}",
                headers=self.headers
            )
            self.assertEqual(po_resp.status_code, 200)
            pos_list = po_resp.json()["items"]
            po_data = next(p for p in pos_list if p["id"] == po.id)

            # 验证 PO 实报实销费用
            self.assertEqual(po_data["domestic_logistics_fee"], 30.0)
            self.assertEqual(po_data["head_haul_fee"], 500.0)
            self.assertEqual(po_data["logistics_loss_fee"], 10.0)
            self.assertEqual(po_data["orders_count"], 2)
            self.assertEqual(po_data["total_weight_g"], 9000)
            self.assertEqual(po_data["total_weight_kg"], 9.0)

            # 验证头程对账差异:
            # order_a理论头程: 240元, order_b理论头程: 300元 -> 累计理论 540元
            # 货代实收: 500元 -> 对账差异: 540 - 500 = +40.0元 (货代实收比理论预估便宜40元)
            self.assertEqual(po_data["sum_order_theoretical_head_haul"], 540.0)
            self.assertEqual(po_data["freight_diff_cny"], 40.0)

            # 验证 PO 总成本 = 总采购(100+150=250) + PO国内(30) + 货代头程(500) + 损耗(10) = 790.0元
            self.assertEqual(po_data["total_cost_cny"], 790.0)

            print("[OK] Test 3: Dual perspective cost, PO domestic allocation & freight reconciliation passed!")
        finally:
            self.db.delete(order_a)
            self.db.delete(order_b)
            self.db.delete(po)
            self.db.commit()

    def test_weight_update_with_frontend_payload_structure(self):
        """测试 4: 模拟前端实际提交格式 (items 数组 + propagate_to_same_sku + 默认 90 费率)"""
        sku_code = "SKU_FE_PAYLOAD_001"
        test_order = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_FE_PAYLOAD_TEST",
            status="pending_rtd",
            total_amount=300.0,
            raw_items_json=json.dumps([{
                "title": "前端载荷测试商品",
                "sku_id": sku_code,
                "quantity": 1,
                "weight_g": 0
            }]),
            total_weight_g=0
        )
        self.db.add(test_order)
        self.db.commit()
        self.db.refresh(test_order)

        try:
            # 模拟前端仅发送 items 列表格式和 90 元/kg，整重 663g
            payload = {
                "order_id": test_order.id,
                "total_weight_g": 663,
                "air_freight_rate": 90.0,
                "items": [{"sku_id": sku_code, "weight_g": 221}],
                "sync_to_catalog": True,
                "propagate_to_same_sku": True
            }
            resp = self.client.put(
                "/api/store-orders/update-weight-and-freight",
                headers=self.headers,
                json=payload
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["total_weight_g"], 663)
            # 663g @ 90元/kg = round((663/1000)*90, 2) = 59.67元
            self.assertEqual(data["head_haul_cost"], 59.67)
            print("[OK] Test 4: Frontend payload structure & 90 rate calculation passed!")
        finally:
            self.db.delete(test_order)
            self.db.commit()

    def test_po_box_dimensions_and_weight_lifecycle(self):
        """测试 5: PO 外箱尺寸(长宽高)与实测整箱重量创建、自动带入、行内更新与对账展示"""
        # 创建两笔测试订单
        ord1 = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_PO_DIM_01",
            status="pending_rtd",
            total_amount=500.0,
            total_weight_g=2500
        )
        ord2 = MakroOrder(
            store_id=self.store.id,
            seller_id=self.store.seller_id,
            order_id="OD_PO_DIM_02",
            status="pending_rtd",
            total_amount=600.0,
            total_weight_g=3500
        )
        self.db.add_all([ord1, ord2])
        self.db.commit()
        self.db.refresh(ord1)
        self.db.refresh(ord2)

        po_sn = f"PO_DIM_TEST_{int(datetime.now().timestamp())}"
        try:
            # 1. 创建 PO：未传 package_weight_kg，应自动带入订单累计重量 (2500+3500)/1000 = 6.0kg
            create_resp = self.client.post(
                "/api/store-orders/po/create-and-assign",
                headers=self.headers,
                json={
                    "store_id": self.store.id,
                    "po_number": po_sn,
                    "name": "箱规与重量测试批次",
                    "forwarder_name": "速派国际",
                    "air_waybill_no": "AWB-889900",
                    "box_length_cm": 45.0,
                    "box_width_cm": 35.5,
                    "box_height_cm": 25.0,
                    "package_weight_kg": 0.0,
                    "order_ids": [ord1.id, ord2.id]
                }
            )
            self.assertEqual(create_resp.status_code, 200)
            po_id = create_resp.json()["po_id"]

            # 2. 检查 /po/list 返回结构
            list_resp = self.client.get(
                f"/api/store-orders/po/list?store_id={self.store.id}",
                headers=self.headers
            )
            self.assertEqual(list_resp.status_code, 200)
            items = list_resp.json()["items"]
            po_data = next((p for p in items if p["id"] == po_id), None)
            self.assertIsNotNone(po_data)
            self.assertEqual(po_data["box_length_cm"], 45.0)
            self.assertEqual(po_data["box_width_cm"], 35.5)
            self.assertEqual(po_data["box_height_cm"], 25.0)
            self.assertEqual(po_data["box_dimensions_str"], "45×35.5×25 cm")
            self.assertEqual(po_data["package_weight_kg"], 6.0)  # 自动带入 6.0 kg

            # 3. 测试通过 /po/update/{po_id} 修改为实际过秤重量 (含外箱包装 6.8kg) 与新尺寸
            up_resp = self.client.put(
                f"/api/store-orders/po/update/{po_id}",
                headers=self.headers,
                json={
                    "box_length_cm": 50.0,
                    "box_width_cm": 40.0,
                    "box_height_cm": 30.0,
                    "package_weight_kg": 6.8,
                    "head_haul_fee": 612.0
                }
            )
            self.assertEqual(up_resp.status_code, 200)

            # 4. 再次验证 list 返回已更新为新箱规与新实重
            list_resp2 = self.client.get(
                f"/api/store-orders/po/list?store_id={self.store.id}",
                headers=self.headers
            )
            po_data2 = next((p for p in list_resp2.json()["items"] if p["id"] == po_id), None)
            self.assertEqual(po_data2["box_dimensions_str"], "50×40×30 cm")
            self.assertEqual(po_data2["package_weight_kg"], 6.8)
            self.assertEqual(po_data2["head_haul_fee"], 612.0)
            print("[OK] Test 5: PO box dimensions and weight lifecycle passed!")
        finally:
            self.db.delete(ord1)
            self.db.delete(ord2)
            po_row = self.db.query(MakroPurchaseOrder).filter(MakroPurchaseOrder.po_number == po_sn).first()
            if po_row:
                self.db.delete(po_row)
            self.db.commit()


if __name__ == "__main__":
    unittest.main()

